#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# ELK one-time setup:
#   1. Wait for Elasticsearch to be ready
#   2. Set kibana_system password
#   3. Create ILM policy
#   4. Create index template
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ES_URL="${ELASTICSEARCH_PROTOCOL}://${ELASTICSEARCH_HOST}:${ES_PORT}"
ELASTIC_PASSWORD="${ELASTIC_PASSWORD}"
KIBANA_SYSTEM_PASSWORD="${KIBANA_SYSTEM_PASSWORD}"

echo "==> Waiting for Elasticsearch..."
until curl -s -u "elastic:${ELASTIC_PASSWORD}" "${ES_URL}/_cluster/health" \
      | grep -vq '"status":"red"'; do
  sleep 5
done
echo "==> Elasticsearch is ready."

# ── 1. Set kibana_system password ────────────────────────────────────────────
echo "==> Setting kibana_system password..."
curl -s -X POST -u "elastic:${ELASTIC_PASSWORD}" \
  -H "Content-Type: application/json" \
  "${ES_URL}/_security/user/kibana_system/_password" \
  -d "{\"password\": \"${KIBANA_SYSTEM_PASSWORD}\"}"
echo ""

# ── 2. Create ILM policy ──────────────────────────────────────────────────────
echo "==> Creating ILM policy: elk-logs-policy..."
curl -s -X PUT -u "elastic:${ELASTIC_PASSWORD}" \
  -H "Content-Type: application/json" \
  "${ES_URL}/_ilm/policy/elk-logs-policy" \
  -d '{
    "policy": {
      "phases": {
        "hot": {
          "min_age": "0ms",
          "actions": {
            "rollover": {
              "max_age":  "1d",
              "max_size": "10gb"
            },
            "set_priority": { "priority": 100 }
          }
        },
        "warm": {
          "min_age": "7d",
          "actions": {
            "shrink":   { "number_of_shards": 1 },
            "forcemerge": { "max_num_segments": 1 },
            "set_priority": { "priority": 50 }
          }
        },
        "cold": {
          "min_age": "30d",
          "actions": {
            "freeze": {},
            "set_priority": { "priority": 0 }
          }
        },
        "delete": {
          "min_age": "90d",
          "actions": {
            "delete": {}
          }
        }
      }
    }
  }'
echo ""

# ── 3. Create index template ───────────────────────────────────────────────────
echo "==> Creating index template: elk-logs-template..."
curl -s -X PUT -u "elastic:${ELASTIC_PASSWORD}" \
  -H "Content-Type: application/json" \
  "${ES_URL}/_index_template/elk-logs-template" \
  -d '{
    "index_patterns": ["logs-*"],
    "priority": 500,
    "template": {
      "settings": {
        "number_of_shards":   1,
        "number_of_replicas": 0,
        "index.lifecycle.name":           "elk-logs-policy",
        "index.lifecycle.rollover_alias": "logs",
        "refresh_interval": "5s",
        "index.search.slowlog.threshold.query.warn":   "10s",
        "index.search.slowlog.threshold.fetch.warn":   "1s",
        "index.indexing.slowlog.threshold.index.warn": "10s"
      },
      "mappings": {
        "dynamic_templates": [
          {
            "extra_as_keyword": {
              "path_match":    "extra.*",
              "mapping": { "type": "keyword", "ignore_above": 512 }
            }
          }
        ],
        "properties": {
          "@timestamp":   { "type": "date" },
          "timestamp":    { "type": "date" },
          "environment":  { "type": "keyword" },
          "product":      { "type": "keyword" },
          "service":      { "type": "keyword" },
          "level":        { "type": "keyword" },
          "severity":     { "type": "integer" },
          "message":      { "type": "text", "analyzer": "standard",
                            "fields": { "keyword": { "type": "keyword", "ignore_above": 1024 } } },
          "trace_id":     { "type": "keyword" },
          "span_id":      { "type": "keyword" },
          "request_id":   { "type": "keyword" },
          "user_id":      { "type": "keyword" },
          "session_id":   { "type": "keyword" },
          "host":         { "type": "keyword" },
          "logger":       { "type": "keyword" },
          "module":       { "type": "keyword" },
          "function":     { "type": "keyword" },
          "line":         { "type": "integer" },
          "duration_ms":  { "type": "float" },
          "status_code":  { "type": "integer" },
          "exception": {
            "type": "object",
            "properties": {
              "type":       { "type": "keyword" },
              "message":    { "type": "text" },
              "stacktrace": { "type": "text" }
            }
          },
          "geoip": {
            "type": "object",
            "properties": {
              "location": { "type": "geo_point" }
            }
          }
        }
      }
    }
  }'
echo ""

echo "==> ELK setup complete."