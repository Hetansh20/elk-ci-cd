# ELK Stack — Multi-product / Multi-service / Multi-environment

Centralised log management for multiple products and services across
`dev` and `prod` environments, using the Elastic stack 8.x.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Architecture                                │
│                                                                     │
│  Services                 Filebeat         Logstash   Elasticsearch │
│  ─────────────────        ─────────        ────────   ───────────── │
│  product-a                                                          │
│    auth-service  ──► log ─┐                                         │
│    api-service   ──► log ─┤                                         │
│  product-b               ├─► harvest ─► parse  ─► index ─► Kibana  │
│    payment-svc   ──► log ─┤    logs     enrich     route            │
│    notification  ──► log ─┘                                         │
│                                                                     │
│  Log path:  ./logs/<env>/<product>/<service>/app.log                │
│  ES index:  logs-<env>-<product>-<service>-YYYY.MM.dd               │
└─────────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone / copy this directory
cd elk-stack

# 2. Adjust passwords in .env (mandatory before production)
cp .env .env.local
# edit .env.local: set ELASTIC_PASSWORD and KIBANA_SYSTEM_PASSWORD

# 3. Start the full stack
docker compose up -d

# 4. Tail logs from the setup container to watch initialisation
docker logs -f elk_setup

# 5. Open Kibana
open http://localhost:5601
# Login: elastic / <ELASTIC_PASSWORD from .env>
```

> **First boot takes ~2 minutes** while Elasticsearch starts and
> the setup container configures users and index templates.

---

## Directory Structure

```
elk-stack/
├── docker-compose.yml              # Full stack definition
├── .env                            # All tuneable settings
│
├── elasticsearch/config/
│   └── elasticsearch.yml
├── kibana/config/
│   └── kibana.yml
├── logstash/
│   ├── config/logstash.yml
│   └── pipeline/logstash.conf      # Parse · enrich · route
├── filebeat/
│   └── filebeat.yml                # Harvest logs, add metadata
│
├── scripts/
│   └── setup.sh                    # ILM policy + index template
│
├── logs/                           # Shared log volume
│   ├── dev/
│   │   └── product-a/
│   │       ├── auth-service/app.log
│   │       └── api-service/app.log
│   └── prod/
│       └── product-b/
│           ├── payment-service/app.log
│           └── notification-service/app.log
│
└── client/                         # Python logger SDK
    ├── elk_logger.py               # Core logger library
    ├── Dockerfile
    ├── requirements.txt
    └── examples/
        ├── product_a_auth.py
        ├── product_a_api.py
        ├── product_b_payment.py
        └── product_b_notification.py
```

---

## Log Format

Every log line is a single JSON object:

```json
{
  "timestamp":   "2024-01-15T08:23:11.042Z",
  "level":       "INFO",
  "environment": "prod",
  "product":     "product-b",
  "service":     "payment-service",
  "message":     "Payment successful",
  "logger":      "product-b.payment-service",
  "module":      "product_b_payment",
  "function":    "process_payment",
  "line":        82,
  "host":        "container-abc",
  "trace_id":    "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "request_id":  "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "user_id":     "user-0042",
  "extra": {
    "order_id":       "ord-a1b2c3d4e5f6",
    "amount":         149.99,
    "currency":       "USD",
    "payment_method": "card",
    "duration_ms":    423.7
  }
}
```

---

## Python Logger — Usage

### Basic setup (reads from environment variables)

```python
from elk_logger import get_logger

# Reads PRODUCT, SERVICE, ENVIRONMENT, LOG_LEVEL, LOGSTASH_HOST from env
logger = get_logger()

logger.info("Service started")
logger.warning("Cache miss", extra={"key": "user:42", "ttl": 300})
logger.error("DB query failed", exc_info=True, extra={"query": "SELECT ..."})
```

### With LogContext (trace propagation)

```python
from elk_logger import get_logger, LogContext
import uuid

logger = get_logger()

with LogContext(
    trace_id=str(uuid.uuid4()),   # shared across all log lines in this block
    user_id="user-0042",
    order_id="ord-abc123",
):
    logger.info("Processing order")      # → includes trace_id + user_id + order_id
    logger.info("Payment captured")      # → same trace_id automatically
```

### Performance timing decorator

```python
from elk_logger import get_logger, log_duration

logger = get_logger()

@log_duration(logger, operation="fetch_inventory")
def fetch_inventory(product_id: str) -> dict:
    ...  # duration_ms logged automatically on success or failure
```

### HTTP request decorator

```python
from elk_logger import get_logger, log_request

logger = get_logger()

@app.route("/checkout", methods=["POST"])
@log_request(logger)          # auto trace_id + request_id + timing
def checkout():
    ...
```

### Explicit logger configuration (no env vars)

```python
logger = get_logger(
    "my-service",
    environment   = "prod",
    product       = "product-a",
    service       = "checkout-service",
    log_dir       = "/var/log/myapp",
    log_level     = "WARNING",
    logstash_host = "logstash",      # also enables direct TCP shipping
    logstash_port = 5000,
    enable_console= True,
)
```

---

## Adding a New Product / Service

1. **Create the log directory** (Filebeat auto-discovers it):
   ```bash
   mkdir -p logs/prod/product-c/my-service
   ```

2. **Add a service** to `docker-compose.yml`:
   ```yaml
   product-c-my-service:
     build:
       context: ./client
     environment:
       - PRODUCT=product-c
       - SERVICE=my-service
       - ENVIRONMENT=prod
       - LOGSTASH_HOST=logstash
     volumes:
       - ./logs/prod/product-c/my-service:/app/logs
   ```

3. **Use the logger** in your Python code:
   ```python
   from elk_logger import get_logger
   logger = get_logger()   # picks up env vars automatically
   ```

No Filebeat or Logstash changes required — both are configured with
wildcards that pick up any path matching `logs/*/*/*/**.log`.

---

## Kibana — Useful Queries (KQL)

```kql
# All errors across all services
level: "ERROR"

# One product, prod only
product: "product-b" AND environment: "prod"

# Payment failures in the last hour
service: "payment-service" AND level: ("ERROR" OR "WARNING")

# Trace a single request end-to-end
trace_id: "f47ac10b-58cc-4372-a567-0e02b2c3d479"

# Slow requests (>500 ms)
extra.duration_ms > 500

# High-risk transactions blocked
message: "High-risk transaction blocked"
```

---

## Elasticsearch Index Pattern

| Index                              | Content                              |
|------------------------------------|--------------------------------------|
| `logs-dev-product-a-auth-service-*`   | product-a auth service, dev env   |
| `logs-prod-product-b-payment-service-*` | product-b payments, prod env    |
| `logs-*`                           | All logs (use in Kibana data view)   |

---

## Production Checklist

- [ ] Change `ELASTIC_PASSWORD` and `KIBANA_SYSTEM_PASSWORD` in `.env`
- [ ] Increase `ES_JVM_HEAP` to ≥ 4g (half of available RAM)
- [ ] Set `number_of_replicas: 1` in setup.sh for multi-node clusters
- [ ] Enable TLS (`xpack.security.http.ssl.enabled: true`)
- [ ] Mount Elasticsearch data to a fast SSD volume
- [ ] Configure ILM retention via `ILM_DELETE_DAYS` in `.env`
- [ ] Set up Kibana alerting for `level: CRITICAL` events
- [ ] Remove `stdout { codec => rubydebug }` from logstash.conf

---

## Ports

| Service         | Port  | Purpose                      |
|-----------------|-------|------------------------------|
| Elasticsearch   | 9200  | REST API                     |
| Kibana          | 5601  | Dashboard UI                 |
| Logstash Beats  | 5044  | Filebeat → Logstash          |
| Logstash TCP    | 5000  | Direct Python TCP shipping   |
| Logstash HTTP   | 8080  | HTTP log endpoint            |
| Logstash API    | 9600  | Logstash metrics             |
