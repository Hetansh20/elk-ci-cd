"""
product-a · api-service
─────────────────────────────────────────────────────────────────────────────
Simulates a REST API gateway generating HTTP access-style structured logs.
─────────────────────────────────────────────────────────────────────────────
"""

import random
import time
import uuid

from elk_logger import LogContext, get_logger

logger = get_logger("product-a.api-service")

ENDPOINTS = [
    ("GET",    "/api/v1/users",          200, 0.02),
    ("GET",    "/api/v1/users/{id}",     200, 0.01),
    ("POST",   "/api/v1/users",          201, 0.03),
    ("PUT",    "/api/v1/users/{id}",     200, 0.025),
    ("DELETE", "/api/v1/users/{id}",     204, 0.02),
    ("GET",    "/api/v1/products",       200, 0.015),
    ("POST",   "/api/v1/orders",         201, 0.04),
    ("GET",    "/api/v1/health",         200, 0.001),
    ("GET",    "/api/v1/metrics",        200, 0.003),
]

ERROR_SCENARIOS = [
    (400, "Bad Request",             0.04),
    (401, "Unauthorized",            0.03),
    (403, "Forbidden",               0.02),
    (404, "Not Found",               0.05),
    (422, "Unprocessable Entity",    0.03),
    (429, "Too Many Requests",       0.02),
    (500, "Internal Server Error",   0.02),
    (503, "Service Unavailable",     0.01),
]


def simulate_request():
    method, path, success_code, avg_duration = random.choice(ENDPOINTS)
    resource_id = str(uuid.uuid4())[:8]
    path        = path.replace("{id}", resource_id)
    trace_id    = str(uuid.uuid4())
    request_id  = str(uuid.uuid4())
    user_id     = f"user-{random.randint(1, 200):04d}"

    with LogContext(trace_id=trace_id, request_id=request_id, user_id=user_id):

        logger.info(
            f"→ {method} {path}",
            extra={
                "http_method":  method,
                "http_path":    path,
                "user_agent":   random.choice([
                    "Mozilla/5.0", "python-httpx/0.26", "curl/8.0", "PostmanRuntime/7.36"
                ]),
            },
        )

        # Simulate processing
        duration_s = random.expovariate(1 / avg_duration)

        # Decide outcome
        roll = random.random()
        cumulative = 0.0
        status_code = success_code
        status_msg  = "OK"

        for code, msg, prob in ERROR_SCENARIOS:
            cumulative += prob
            if roll < cumulative:
                status_code = code
                status_msg  = msg
                break

        time.sleep(duration_s)

        log_extra = {
            "http_method":   method,
            "http_path":     path,
            "status_code":   status_code,
            "duration_ms":   round(duration_s * 1000, 2),
            "response_size": random.randint(64, 65536),
        }

        if status_code >= 500:
            logger.error(f"← {method} {path} [{status_code}] {status_msg}", extra=log_extra)
        elif status_code >= 400:
            logger.warning(f"← {method} {path} [{status_code}] {status_msg}", extra=log_extra)
        else:
            logger.info(f"← {method} {path} [{status_code}]", extra=log_extra)


def main():
    logger.info("api-service starting")

    iteration = 0
    while True:
        iteration += 1
        simulate_request()
        time.sleep(random.expovariate(8))   # avg ~125 ms – busier service

        if iteration % 100 == 0:
            logger.info(
                "api-service stats",
                extra={"requests_processed": iteration},
            )


if __name__ == "__main__":
    main()
