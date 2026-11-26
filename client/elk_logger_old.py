"""
elk_logger.py
─────────────────────────────────────────────────────────────────────────────
Structured JSON logger for the ELK stack.

Features
  • JSON-formatted log lines  →  written to a rotating file (read by Filebeat)
  • Optional TCP handler      →  ships logs DIRECTLY to Logstash (port 5000)
  • Automatic context fields  →  environment, product, service, hostname
  • Trace / span / request ID propagation via contextvars
  • Exception serialisation with full stack-trace in the JSON payload
  • Performance timing decorator
  • Thread-safe

Usage
  from elk_logger import get_logger, LogContext

  logger = get_logger()

  with LogContext(trace_id="abc-123", user_id="u-456"):
      logger.info("User logged in", extra={"ip": "1.2.3.4"})
      logger.error("Something broke", extra={"order_id": "ord-789"})
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import pathlib
import platform
import socket
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Context variables  (propagated across async tasks & threads via copy_context)
# ─────────────────────────────────────────────────────────────────────────────
_ctx_trace_id:   ContextVar[Optional[str]] = ContextVar("trace_id",   default=None)
_ctx_span_id:    ContextVar[Optional[str]] = ContextVar("span_id",    default=None)
_ctx_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_ctx_user_id:    ContextVar[Optional[str]] = ContextVar("user_id",    default=None)
_ctx_session_id: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
_ctx_extra:      ContextVar[dict]          = ContextVar("extra",      default={})


class LogContext:
    """
    Context manager that sets trace/request context fields for all log
    records emitted inside the block.

    with LogContext(trace_id="t-123", user_id="u-456", order_id="o-789"):
        logger.info("processing order")
    """

    def __init__(
        self,
        trace_id:   Optional[str] = None,
        span_id:    Optional[str] = None,
        request_id: Optional[str] = None,
        user_id:    Optional[str] = None,
        session_id: Optional[str] = None,
        **extra_fields: Any,
    ):
        self._tokens: list = []
        self._values = {
            _ctx_trace_id:   trace_id   or str(uuid.uuid4()),
            _ctx_span_id:    span_id,
            _ctx_request_id: request_id or str(uuid.uuid4()),
            _ctx_user_id:    user_id,
            _ctx_session_id: session_id,
            _ctx_extra:      extra_fields,
        }

    def __enter__(self):
        for var, value in self._values.items():
            self._tokens.append(var.set(value))
        return self

    def __exit__(self, *_):
        for token in reversed(self._tokens):
            token.var.reset(token)


# ─────────────────────────────────────────────────────────────────────────────
# JSON Formatter
# ─────────────────────────────────────────────────────────────────────────────

class ELKJsonFormatter(logging.Formatter):
    """
    Converts a LogRecord into a single-line JSON string matching the
    Elasticsearch index template defined in setup.sh.

    Log record format:
    {
      "timestamp":   "2024-01-01T12:00:00.123456Z",
      "level":       "INFO",
      "environment": "prod",
      "product":     "product-a",
      "service":     "auth-service",
      "message":     "User logged in",
      "logger":      "auth_service.login",
      "module":      "login",
      "function":    "authenticate",
      "line":        42,
      "host":        "container-hostname",
      "trace_id":    "...",
      "span_id":     "...",
      "request_id":  "...",
      "user_id":     "...",
      "session_id":  "...",
      "exception":   { "type": "...", "message": "...", "stacktrace": "..." },
      "extra":       { ...any additional fields... }
    }
    """

    RESERVED_KEYS = {
        "args", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message",
        "module", "msecs", "msg", "name", "pathname", "process",
        "processName", "relativeCreated", "stack_info", "taskName",
        "thread", "threadName",
    }

    def __init__(
        self,
        environment: str,
        product: str,
        service: str,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.environment = environment
        self.product     = product
        self.service     = service
        self.hostname    = platform.node() or socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # Base message
        message = record.getMessage()

        # Build the log document
        doc: dict[str, Any] = {
            "timestamp":   datetime.fromtimestamp(
                               record.created, tz=timezone.utc
                           ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level":       record.levelname,
            "environment": self.environment,
            "product":     self.product,
            "service":     self.service,
            "message":     message,
            "logger":      record.name,
            "module":      record.module,
            "function":    record.funcName,
            "line":        record.lineno,
            "host":        self.hostname,
        }

        # Context-var fields
        if (v := _ctx_trace_id.get()):   doc["trace_id"]   = v
        if (v := _ctx_span_id.get()):    doc["span_id"]    = v
        if (v := _ctx_request_id.get()): doc["request_id"] = v
        if (v := _ctx_user_id.get()):    doc["user_id"]    = v
        if (v := _ctx_session_id.get()): doc["session_id"] = v

        # Exception info
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc_value, exc_tb = record.exc_info
            doc["exception"] = {
                "type":       exc_type.__name__,
                "message":    str(exc_value),
                "stacktrace": "".join(
                    traceback.format_exception(exc_type, exc_value, exc_tb)
                ).strip(),
            }

        # Stack info
        if record.stack_info:
            doc["stack_info"] = record.stack_info

        # Merge 'extra' from LogContext
        ctx_extra = _ctx_extra.get({})

        # Merge 'extra' passed directly to logger.info(..., extra={...})
        record_extra: dict[str, Any] = {}
        for key, value in vars(record).items():
            if key not in self.RESERVED_KEYS and not key.startswith("_"):
                record_extra[key] = value

        merged_extra = {**ctx_extra, **record_extra}
        if merged_extra:
            doc["extra"] = merged_extra

        return json.dumps(doc, default=str, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# TCP handler (direct-to-Logstash)
# ─────────────────────────────────────────────────────────────────────────────

class LogstashTCPHandler(logging.handlers.SocketHandler):
    """
    Ships log records as newline-delimited JSON directly to Logstash
    TCP input (port 5000).  Falls back silently on connection errors so
    file-based logging still works even if Logstash is unreachable.
    """

    def __init__(self, host: str, port: int, formatter: ELKJsonFormatter):
        super().__init__(host, port)
        self.setFormatter(formatter)

    def makePickle(self, record: logging.LogRecord) -> bytes:  # noqa: N802
        """Override: send JSON + newline instead of pickle."""
        msg = self.format(record) + "\n"
        return msg.encode("utf-8")

    def handleError(self, record: logging.LogRecord) -> None:
        """Swallow socket errors so the application keeps running."""
        # Optionally log to stderr
        print(
            f"[LogstashTCPHandler] Failed to ship log to Logstash: {sys.exc_info()[1]}",
            file=sys.stderr,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def get_logger(
    name: Optional[str] = None,
    *,
    environment:     Optional[str] = None,
    product:         Optional[str] = None,
    service:         Optional[str] = None,
    log_dir:         Optional[str] = None,
    log_filename:    str           = "app.log",
    log_level:       Optional[str] = None,
    max_bytes:       int           = 50 * 1024 * 1024,   # 50 MB
    backup_count:    int           = 10,
    logstash_host:   Optional[str] = None,
    logstash_port:   int           = 5000,
    enable_console:  bool          = True,
) -> logging.Logger:
    """
    Build and return a configured logger.

    All settings fall back to environment variables if not provided:

      ELK_ENVIRONMENT   (default: "dev")
      ELK_PRODUCT       (required)
      ELK_SERVICE       (required)
      ELK_LOG_DIR       (default: ./logs)
      ELK_LOG_LEVEL     (default: "INFO")
      LOGSTASH_HOST     (optional – enables direct TCP shipping)
      LOGSTASH_PORT     (default: 5000)
    """
    environment = environment or os.environ.get("ENVIRONMENT", "dev")
    product     = product     or os.environ.get("PRODUCT",     "unknown-product")
    service     = service     or os.environ.get("SERVICE",     "unknown-service")
    level_name  = (log_level  or os.environ.get("LOG_LEVEL",   "INFO")).upper()
    level       = getattr(logging, level_name, logging.INFO)

    # Log directory: ./logs/<env>/<product>/<service>/
    if log_dir is None:
        log_dir = os.environ.get("LOG_DIR")
        if log_dir is None:
            base = pathlib.Path("/app/logs") if pathlib.Path("/app").exists() \
                   else pathlib.Path("logs")
            log_dir = str(base)

    log_path = pathlib.Path(log_dir) / log_filename
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Logger name
    logger_name = name or f"{product}.{service}"
    logger      = logging.getLogger(logger_name)

    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    formatter = ELKJsonFormatter(
        environment=environment,
        product=product,
        service=service,
    )

    # ── Rotating file handler (primary – read by Filebeat) ─────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        filename    = str(log_path),
        maxBytes    = max_bytes,
        backupCount = backup_count,
        encoding    = "utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    # ── Console handler (human-readable in dev, JSON in prod) ─────────────
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        if environment in ("dev", "development", "local"):
            # Pretty print for local development
            console_fmt = logging.Formatter(
                fmt="%(asctime)s [%(levelname)-8s] %(name)s – %(message)s",
                datefmt="%H:%M:%S",
            )
            console_handler.setFormatter(console_fmt)
        else:
            console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

    # ── Optional: direct TCP to Logstash ──────────────────────────────────
    ls_host = logstash_host or os.environ.get("LOGSTASH_HOST")
    ls_port = int(os.environ.get("LOGSTASH_PORT", logstash_port))
    if ls_host:
        tcp_handler = LogstashTCPHandler(ls_host, ls_port, formatter)
        tcp_handler.setLevel(level)
        logger.addHandler(tcp_handler)

    return logger


# ─────────────────────────────────────────────────────────────────────────────
# Decorators
# ─────────────────────────────────────────────────────────────────────────────

def log_duration(logger: logging.Logger, operation: Optional[str] = None):
    """
    Decorator that logs the duration of a function call.

    @log_duration(logger, operation="fetch_user")
    def fetch_user(user_id: str): ...
    """
    def decorator(func):
        op_name = operation or func.__qualname__

        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info(
                    f"{op_name} completed",
                    extra={"operation": op_name, "duration_ms": round(elapsed_ms, 2)},
                )
                return result
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.error(
                    f"{op_name} failed",
                    exc_info=True,
                    extra={"operation": op_name, "duration_ms": round(elapsed_ms, 2)},
                )
                raise

        return wrapper
    return decorator


def log_request(logger: logging.Logger):
    """
    Decorator for HTTP handler functions.  Automatically creates a LogContext
    with a fresh trace_id and request_id and logs request start/end.

    @app.route("/login", methods=["POST"])
    @log_request(logger)
    def login(): ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            trace_id   = str(uuid.uuid4())
            request_id = str(uuid.uuid4())
            with LogContext(trace_id=trace_id, request_id=request_id):
                logger.info(f"→ {func.__name__} started")
                start = time.perf_counter()
                try:
                    result    = func(*args, **kwargs)
                    elapsed   = (time.perf_counter() - start) * 1000
                    logger.info(
                        f"← {func.__name__} finished",
                        extra={"duration_ms": round(elapsed, 2)},
                    )
                    return result
                except Exception:
                    elapsed = (time.perf_counter() - start) * 1000
                    logger.error(
                        f"← {func.__name__} failed",
                        exc_info=True,
                        extra={"duration_ms": round(elapsed, 2)},
                    )
                    raise
        return wrapper
    return decorator
