"""
elk_logger.py
─────────────────────────────────────────────────────────────────────────────
Filebeat-optimized structured JSON logger.

✔ Writes JSON logs to file (consumed by Filebeat)
✔ Context propagation (trace_id, request_id, etc.)
✔ Exception serialization
✔ Rotation support
✔ Container-safe flushing
✔ NO direct Logstash dependency (removed TCP handler)

Log path format:
  /app/logs/<environment>/<product>/<service>/app.log
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
# Context variables
# ─────────────────────────────────────────────────────────────────────────────
_ctx_trace_id:   ContextVar[Optional[str]] = ContextVar("trace_id", default=None)
_ctx_span_id:    ContextVar[Optional[str]] = ContextVar("span_id", default=None)
_ctx_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
_ctx_user_id:    ContextVar[Optional[str]] = ContextVar("user_id", default=None)
_ctx_session_id: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
_ctx_extra:      ContextVar[dict]          = ContextVar("extra", default={})


class LogContext:
    def __init__(
        self,
        trace_id=None,
        span_id=None,
        request_id=None,
        user_id=None,
        session_id=None,
        **extra_fields,
    ):
        self._tokens = []
        self._values = {
            _ctx_trace_id: trace_id or str(uuid.uuid4()),
            _ctx_span_id: span_id,
            _ctx_request_id: request_id or str(uuid.uuid4()),
            _ctx_user_id: user_id,
            _ctx_session_id: session_id,
            _ctx_extra: extra_fields,
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

    RESERVED_KEYS = {
        "args", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message",
        "module", "msecs", "msg", "name", "pathname", "process",
        "processName", "relativeCreated", "stack_info", "thread",
        "threadName",
    }

    def __init__(self, environment, product, service):
        super().__init__()
        self.environment = environment
        self.product = product
        self.service = service
        self.hostname = platform.node() or socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()

        doc = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "environment": self.environment,
            "product": self.product,
            "service": self.service,
            "message": message,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "host": self.hostname,
        }

        # Context fields
        if (v := _ctx_trace_id.get()): doc["trace_id"] = v
        if (v := _ctx_span_id.get()): doc["span_id"] = v
        if (v := _ctx_request_id.get()): doc["request_id"] = v
        if (v := _ctx_user_id.get()): doc["user_id"] = v
        if (v := _ctx_session_id.get()): doc["session_id"] = v

        # Exception
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            doc["exception"] = {
                "type": exc_type.__name__,
                "message": str(exc_value),
                "stacktrace": "".join(
                    traceback.format_exception(exc_type, exc_value, exc_tb)
                ).strip(),
            }

        # Extra fields
        record_extra = {
            k: v for k, v in vars(record).items()
            if k not in self.RESERVED_KEYS and not k.startswith("_")
        }

        merged_extra = {**_ctx_extra.get({}), **record_extra}
        if merged_extra:
            doc["extra"] = merged_extra

        return json.dumps(doc, default=str, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# Logger Factory
# ─────────────────────────────────────────────────────────────────────────────
def get_logger(
    name: Optional[str] = None,
    *,
    environment: Optional[str] = None,
    product: Optional[str] = None,
    service: Optional[str] = None,
    log_dir: Optional[str] = None,
    log_filename: str = "app.log",
    log_level: Optional[str] = None,
    max_bytes: int = 50 * 1024 * 1024,
    backup_count: int = 10,
    enable_console: bool = True,
) -> logging.Logger:

    environment = environment or os.environ.get("ENVIRONMENT", "dev")
    product = product or os.environ.get("PRODUCT", "unknown-product")
    service = service or os.environ.get("SERVICE", "unknown-service")
    level_name = (log_level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    base_dir = pathlib.Path(log_dir or "/app/logs")
    log_path = base_dir / environment / product / service / log_filename
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger_name = name or f"{product}.{service}"
    logger = logging.getLogger(logger_name)

    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    formatter = ELKJsonFormatter(environment, product, service)

    # File handler
    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Flush fix (important for Docker)
    file_handler.flush = file_handler.stream.flush
    file_handler.terminator = "\n"

    logger.addHandler(file_handler)

    # Console handler
    if enable_console:
        console = logging.StreamHandler(sys.stdout)
        if environment in ("dev", "local"):
            console.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s"
            ))
        else:
            console.setFormatter(formatter)
        logger.addHandler(console)

    return logger


# ─────────────────────────────────────────────────────────────────────────────
# Decorators
# ─────────────────────────────────────────────────────────────────────────────
def log_duration(logger, operation=None):
    def decorator(func):
        op = operation or func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                logger.info(
                    f"{op} completed",
                    extra={"duration_ms": round((time.perf_counter()-start)*1000, 2)}
                )
                return result
            except Exception:
                logger.error(
                    f"{op} failed",
                    exc_info=True,
                    extra={"duration_ms": round((time.perf_counter()-start)*1000, 2)}
                )
                raise
        return wrapper
    return decorator


def log_request(logger):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with LogContext():
                logger.info(f"→ {func.__name__} started")
                try:
                    result = func(*args, **kwargs)
                    logger.info(f"← {func.__name__} finished")
                    return result
                except Exception:
                    logger.error(f"← {func.__name__} failed", exc_info=True)
                    raise
        return wrapper
    return decorator