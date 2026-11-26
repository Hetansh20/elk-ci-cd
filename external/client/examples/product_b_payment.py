"""
product-b · payment-service  (prod environment)
─────────────────────────────────────────────────────────────────────────────
Simulates a payment processing service with rich financial event logging.
─────────────────────────────────────────────────────────────────────────────
"""

import random
import time
import uuid

from elk_logger import LogContext, get_logger, log_duration

logger = get_logger("product-b.payment-service")

CURRENCIES       = ["USD", "EUR", "GBP", "INR", "JPY", "AUD"]
PAYMENT_METHODS  = ["card", "upi", "bank_transfer", "wallet", "bnpl"]
CARD_NETWORKS    = ["visa", "mastercard", "amex", "rupay"]
MERCHANTS        = [f"merchant-{i:03d}" for i in range(1, 51)]


@log_duration(logger, operation="fraud_check")
def fraud_check(amount: float, user_id: str) -> str:
    """Simulate ML-based fraud scoring."""
    time.sleep(random.uniform(0.05, 0.15))
    score = random.random()
    if amount > 5000 and score > 0.7:
        return "HIGH"
    if score > 0.85:
        return "MEDIUM"
    return "LOW"


def process_payment(user_id: str):
    amount      = round(random.uniform(1, 10_000), 2)
    currency    = random.choice(CURRENCIES)
    method      = random.choice(PAYMENT_METHODS)
    merchant_id = random.choice(MERCHANTS)
    order_id    = f"ord-{uuid.uuid4().hex[:12]}"
    trace_id    = str(uuid.uuid4())

    with LogContext(
        trace_id=trace_id,
        user_id=user_id,
        order_id=order_id,
    ):
        logger.info(
            "Payment initiated",
            extra={
                "order_id":      order_id,
                "amount":        amount,
                "currency":      currency,
                "payment_method": method,
                "merchant_id":   merchant_id,
            },
        )

        # ── Fraud check ───────────────────────────────────────────────────
        try:
            risk = fraud_check(amount, user_id)
        except Exception:
            logger.error("Fraud check service unavailable", exc_info=True,
                         extra={"order_id": order_id})
            risk = "UNKNOWN"

        if risk == "HIGH":
            logger.warning(
                "High-risk transaction blocked",
                extra={
                    "order_id":    order_id,
                    "amount":      amount,
                    "currency":    currency,
                    "risk_level":  risk,
                    "merchant_id": merchant_id,
                },
            )
            return

        # ── Payment gateway call ──────────────────────────────────────────
        gateway_latency = random.uniform(0.2, 1.5)
        time.sleep(gateway_latency)

        if random.random() < 0.05:
            logger.error(
                "Payment gateway timeout",
                extra={
                    "order_id":      order_id,
                    "gateway":       "stripe",
                    "latency_ms":    round(gateway_latency * 1000, 2),
                    "payment_method": method,
                },
            )
            return

        if random.random() < 0.04:
            logger.warning(
                "Payment declined",
                extra={
                    "order_id":       order_id,
                    "amount":         amount,
                    "currency":       currency,
                    "decline_reason": random.choice([
                        "insufficient_funds", "card_expired",
                        "do_not_honor",       "invalid_cvv",
                    ]),
                },
            )
            return

        txn_id = f"txn_{uuid.uuid4().hex}"
        logger.info(
            "Payment successful",
            extra={
                "order_id":       order_id,
                "transaction_id": txn_id,
                "amount":         amount,
                "currency":       currency,
                "payment_method": method,
                "merchant_id":    merchant_id,
                "gateway_ms":     round(gateway_latency * 1000, 2),
                "risk_level":     risk,
            },
        )

        # ── Settlement log ────────────────────────────────────────────────
        logger.info(
            "Settlement queued",
            extra={
                "transaction_id":  txn_id,
                "settlement_date": "T+1",
                "amount":          amount,
                "currency":        currency,
            },
        )


def main():
    logger.info("payment-service starting", extra={"env": "prod"})
    users = [f"user-{i:04d}" for i in range(1, 501)]

    iteration = 0
    while True:
        iteration += 1
        process_payment(random.choice(users))
        time.sleep(random.expovariate(3))   # avg ~333 ms

        if iteration % 30 == 0:
            logger.info("payment-service heartbeat", extra={"transactions": iteration})


if __name__ == "__main__":
    main()
