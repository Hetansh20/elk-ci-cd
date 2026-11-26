"""
product-a · auth-service
─────────────────────────────────────────────────────────────────────────────
Simulates an authentication service continuously generating structured logs.
Each log line is written as JSON to:
  /app/logs/app.log  →  picked up by Filebeat
  stdout             →  human-readable in dev
  Logstash TCP       →  direct shipping (optional, if LOGSTASH_HOST is set)
─────────────────────────────────────────────────────────────────────────────
"""

import random
import time
import uuid

from elk_logger import LogContext, get_logger, log_duration

logger = get_logger("product-a.auth-service")

# ── Simulated user pool ────────────────────────────────────────────────────
USERS   = [f"user-{i:04d}" for i in range(1, 201)]
REGIONS = ["us-east-1", "eu-west-1", "ap-south-1", "us-west-2"]
METHODS = ["password", "oauth2", "api-key", "mfa"]
CLIENT_IPS = [
    "203.0.113.10", "198.51.100.42", "192.0.2.88",
    "10.0.0.5",     "172.16.4.20",   "185.220.101.7",
]


@log_duration(logger, operation="validate_token")
def validate_token(token: str) -> bool:
    """Simulate JWT validation with occasional failures."""
    time.sleep(random.uniform(0.002, 0.015))
    if random.random() < 0.03:
        raise ValueError(f"Token signature mismatch: {token[:12]}...")
    return True


def handle_login(user_id: str, method: str, client_ip: str):
    """Simulate a full login flow."""
    trace_id   = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    with LogContext(
        trace_id=trace_id,
        request_id=request_id,
        user_id=user_id,
        client_ip=client_ip,
    ):
        logger.info(
            "Login attempt",
            extra={
                "auth_method": method,
                "client_ip":   client_ip,
                "region":      random.choice(REGIONS),
            },
        )

        start = time.perf_counter()

        # ── Credential validation ─────────────────────────────────────────
        if random.random() < 0.08:
            logger.warning(
                "Invalid credentials",
                extra={
                    "auth_method":    method,
                    "failure_reason": "bad_password",
                    "attempt_count":  random.randint(1, 5),
                },
            )
            return

        # ── MFA check ─────────────────────────────────────────────────────
        if method == "mfa" and random.random() < 0.04:
            logger.warning(
                "MFA code expired",
                extra={"auth_method": method, "failure_reason": "mfa_timeout"},
            )
            return

        # ── Token generation ──────────────────────────────────────────────
        try:
            token = f"tok_{uuid.uuid4().hex}"
            validate_token(token)
        except ValueError:
            logger.error(
                "Token validation failed",
                exc_info=True,
                extra={"auth_method": method},
            )
            return

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "Login successful",
            extra={
                "auth_method": method,
                "duration_ms": round(elapsed_ms, 2),
                "token_prefix": token[:16],
            },
        )


def handle_logout(user_id: str):
    with LogContext(user_id=user_id, trace_id=str(uuid.uuid4())):
        logger.info("User logged out", extra={"session_duration_s": random.randint(60, 7200)})


def handle_password_reset(user_id: str, client_ip: str):
    with LogContext(
        user_id=user_id,
        trace_id=str(uuid.uuid4()),
        client_ip=client_ip,
    ):
        logger.info(
            "Password reset requested",
            extra={"client_ip": client_ip},
        )
        time.sleep(random.uniform(0.05, 0.3))

        if random.random() < 0.02:
            logger.error(
                "Password reset email delivery failed",
                extra={"client_ip": client_ip, "provider": "sendgrid"},
            )
        else:
            logger.info("Password reset email sent")


def main():
    logger.info("auth-service starting", extra={"pid": __import__("os").getpid()})

    actions = [
        ("login",          0.55),
        ("logout",         0.30),
        ("password_reset", 0.15),
    ]
    action_names, weights = zip(*actions)

    iteration = 0
    while True:
        iteration += 1
        user_id   = random.choice(USERS)
        client_ip = random.choice(CLIENT_IPS)
        action    = random.choices(action_names, weights=weights, k=1)[0]

        if action == "login":
            handle_login(user_id, random.choice(METHODS), client_ip)
        elif action == "logout":
            handle_logout(user_id)
        else:
            handle_password_reset(user_id, client_ip)

        # Simulate variable request rate
        time.sleep(random.expovariate(4))   # avg ~250 ms between events

        # Periodic health log
        if iteration % 50 == 0:
            logger.info(
                "auth-service health check",
                extra={"iterations": iteration, "uptime_ok": True},
            )


if __name__ == "__main__":
    main()
