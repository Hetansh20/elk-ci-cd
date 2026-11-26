"""
product-b · notification-service  (prod environment)
─────────────────────────────────────────────────────────────────────────────
Simulates a multi-channel notification dispatcher (email / SMS / push / webhook).
─────────────────────────────────────────────────────────────────────────────
"""

import random
import time
import uuid

from elk_logger import LogContext, get_logger

logger = get_logger("product-b.notification-service")

CHANNELS    = ["email", "sms", "push", "webhook", "in-app"]
TEMPLATES   = ["payment_success", "payment_failed", "otp", "promo", "alert", "welcome"]
PROVIDERS   = {
    "email":   ["sendgrid", "ses", "mailgun"],
    "sms":     ["twilio",   "sns", "textlocal"],
    "push":    ["fcm",      "apns"],
    "webhook": ["internal"],
    "in-app":  ["internal"],
}


def dispatch_notification(user_id: str, channel: str, template: str):
    notification_id = f"notif-{uuid.uuid4().hex[:10]}"
    provider        = random.choice(PROVIDERS.get(channel, ["internal"]))
    trace_id        = str(uuid.uuid4())

    with LogContext(trace_id=trace_id, user_id=user_id, notification_id=notification_id):

        logger.info(
            "Notification queued",
            extra={
                "notification_id": notification_id,
                "channel":         channel,
                "template":        template,
                "provider":        provider,
            },
        )

        # Simulate provider API call
        latency = random.uniform(0.05, 0.8)
        time.sleep(latency)

        # Provider errors
        if random.random() < 0.06:
            logger.error(
                "Notification delivery failed",
                extra={
                    "notification_id": notification_id,
                    "channel":         channel,
                    "provider":        provider,
                    "error_code":      random.choice(["rate_limit", "invalid_token",
                                                      "unreachable", "quota_exceeded"]),
                    "latency_ms":      round(latency * 1000, 2),
                },
            )

            # Retry
            logger.info(
                "Retrying notification via fallback provider",
                extra={
                    "notification_id": notification_id,
                    "channel":         channel,
                    "primary_provider": provider,
                },
            )
            return

        if random.random() < 0.03:
            logger.warning(
                "Notification soft bounce",
                extra={
                    "notification_id": notification_id,
                    "channel":         channel,
                    "provider":        provider,
                    "bounce_type":     "soft",
                },
            )
            return

        logger.info(
            "Notification delivered",
            extra={
                "notification_id": notification_id,
                "channel":         channel,
                "template":        template,
                "provider":        provider,
                "latency_ms":      round(latency * 1000, 2),
            },
        )


def batch_dispatcher():
    """Simulate a batch digest notification run."""
    batch_id   = str(uuid.uuid4())
    batch_size = random.randint(10, 500)

    with LogContext(trace_id=batch_id):
        logger.info(
            "Batch dispatch started",
            extra={"batch_id": batch_id, "batch_size": batch_size},
        )
        start = time.perf_counter()

        for _ in range(min(batch_size, 5)):   # cap sim iterations
            user_id  = f"user-{random.randint(1, 1000):04d}"
            channel  = random.choice(CHANNELS)
            template = random.choice(TEMPLATES)
            dispatch_notification(user_id, channel, template)

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Batch dispatch completed",
            extra={
                "batch_id":        batch_id,
                "batch_size":      batch_size,
                "total_ms":        round(elapsed_ms, 2),
                "avg_latency_ms":  round(elapsed_ms / max(batch_size, 1), 2),
            },
        )


def main():
    logger.info("notification-service starting")
    users = [f"user-{i:04d}" for i in range(1, 1001)]

    iteration = 0
    while True:
        iteration += 1

        if random.random() < 0.1:
            batch_dispatcher()
        else:
            user_id  = random.choice(users)
            channel  = random.choice(CHANNELS)
            template = random.choice(TEMPLATES)
            dispatch_notification(user_id, channel, template)

        time.sleep(random.expovariate(5))

        if iteration % 40 == 0:
            logger.info(
                "notification-service stats",
                extra={"notifications_dispatched": iteration},
            )


if __name__ == "__main__":
    main()
