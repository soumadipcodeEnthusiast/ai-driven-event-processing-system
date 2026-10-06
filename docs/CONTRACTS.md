# Integration Contracts (source of truth)

Every service, script, dashboard and manifest must agree with this file. Change it only through the Software Architect (and note the change in `docs/adr/`).

## 1. Kafka

| Topic | Partitions | Key | Value |
|---|---|---|---|
| `raw-events` | 6 | any / none | Raw event JSON (§1.1) |
| `normalized-events` | 6 | `event_id` | NormalizedEvent JSON (§1.2) |
| `raw-events.DLT` | 1 | original key | **original raw bytes unchanged**; headers `x-error-reason` (string), `x-original-topic`, `x-original-partition`, `x-original-offset` |

Delivery: at-least-once, manual ack (`MANUAL_IMMEDIATE`). Invalid events are sent to the DLT and **then** acked. Unexpected errors are retried (3 attempts with back-off), then sent to the DLT.

### 1.1 Raw event (input)

```json
{"event_id":"e-123","timestamp":"2026-01-01T12:00:00+02:00","source":"checkout-api",
 "type":"http_request","component_id":"ingestion-service","payload":{"status":500}}
```

- Required: `event_id` (non-blank string), `timestamp` (ISO-8601 with offset or `Z`), `source` (non-blank string).
- Optional: `type` (string), `component_id` (string), `payload` (object).
- `STRICT_VALIDATION=true`: unknown **top-level** keys are rejected.
- Non-JSON or non-object input is invalid.

### 1.2 NormalizedEvent (output)

```json
{"event_id":"e-123","timestamp":"2026-01-01T10:00:00Z","source":"checkout-api","type":"http_request",
 "component_id":"ingestion-service","payload":{"status":500},"schema_version":"1.0","ingested_at":"2026-01-01T10:00:00.123Z"}
```

- `timestamp` is converted to a UTC instant.
- `type` defaults to `"generic"`, `component_id` to `null`, `payload` to `{}`.

## 2. HTTP APIs

### forecasting-service :8081

| Method | Path | Notes |
|---|---|---|
| GET | `/health`, `/ready`, `/metrics` | ops |
| POST | `/forecast` | body `{"component_id": str, "forecast_window_minutes": int=60}` → `{"component_id", "alert_emitted": bool, "alert": PredictiveAlert or null, "details": {...forecast summary}}` |
| GET | `/alerts?status=open&component_id=&limit=100` | newest first → `[PredictiveAlert]` |
| GET | `/alerts/summary` | live view of active (open + acknowledged) alerts → `{"active", "open", "acknowledged", "by_severity": {low, medium, high, critical}, "min_minutes_to_breach" (computed at request time; negative = predicted time passed; null if none), "next_breach_alert_id", "next_breach_component"}` |
| GET | `/alerts/{alert_id}` | 404 if missing |
| POST | `/alerts/{alert_id}/ack`, `/alerts/{alert_id}/resolve` | status transitions |
| GET | `/thresholds` | current thresholds per component/metric |
| PUT | `/thresholds/{component_id}` | body `{"<metric_name>": float, ...}`; takes effect without restart (REQ-F) |

A background loop runs a forecast for every configured component every `FORECAST_INTERVAL_SECONDS` (default 60).

When an alert is created, the service calls `POST {DIAGNOSTIC_URL}/diagnose/{component_id}` with body `{"alert_id": ..., "anomalies": [...]}`, retrying up to 3 times. It stores the returned `playbook_id` on the alert. Only one OPEN alert may exist per (component, metric) at a time.

### diagnostic-service :8082

| Method | Path | Notes |
|---|---|---|
| GET | `/health`, `/ready`, `/metrics` | ops |
| POST | `/diagnose/{component_id}` | optional body `{"alert_id": str, "anomalies": [obj]}` → `{"component_id", "playbook_id", "summary", "details"}`; 404 if the component isn't in the graph |
| GET | `/playbook/{playbook_id}` | JSON IncidentPlaybook; `?format=markdown` returns `text/markdown` from `render()` |
| GET | `/playbooks?alert_id=&component_id=` | list |

`POST /diagnose` is rate-limited service-wide (token bucket, `DIAGNOSE_RATE_LIMIT_PER_MINUTE`, default 60). Excess requests get **429** with `Retry-After` (seconds); forecasting's hand-off retries 429 after `Retry-After` (capped at 30 s). Stored playbooks are pruned oldest-first beyond `PLAYBOOK_MAX_COUNT` (default 1000) or `PLAYBOOK_RETENTION_DAYS` (default 30); 0 disables either limit.

## 3. Data models (JSON)

**PredictiveAlert**

```
alert_id ("al-<12 hex>"), component_id, metric_name, predicted_breach_time (UTC ISO),
predicted_value, threshold, lead_time_minutes, severity (low|medium|high|critical),
status (open|acknowledged|resolved), created_at, model_version, playbook_id (nullable)
```

Severity is based on the predicted overshoot of the threshold (p90):

| Overshoot | Severity |
|---|---|
| < 10 % | low |
| < 25 % | medium |
| < 50 % | high |
| ≥ 50 % | critical |

An alert is emitted for any predicted p90 breach in the horizon. `predicted_breach_time` is the first p90 crossing (or *now* if the metric is already over the threshold), `lead_time_minutes` is the real lead (≥ 0), and `predicted_value` is the **peak** p90 in the horizon, which drives severity. `ALERT_MIN_LEAD_MINUTES` (default 10) is the REQ-E *target*: alerts below it are still raised, logged as late, and show up on the dashboard and SLO.

**IncidentPlaybook**

```
playbook_id ("pb-<component>-<8 hex>"), alert_id (nullable), component_id, summary,
root_cause_hypothesis, steps: [{rank, title, command, expected_outcome}] (>= 3, rank 1..n),
generated_at (UTC ISO), generated_by ("<provider>:<model>" or "fallback:rule-based")
```

It is persisted as one JSON file per playbook under `PLAYBOOK_STORE_DIR` (default `/data/playbooks`).

**Dependency node** (returned by the graph client)

```
node_id, node_type (service|datastore|broker|external), health_status (healthy|degraded|down|unknown),
edge_type (calls|depends_on|publishes_to|consumes_from), depth (1|2)
```

Node metadata: `version`, `owner_team`, `sla_tier`, `last_deployed_at`.

## 4. Components monitored and graph seed

The component IDs are: `ingestion-service`, `forecasting-service`, `diagnostic-service`, `kafka`, `zookeeper`, `prometheus`, `graph-db`, `llm`.

The default graph backend is `GRAPH_BACKEND=networkx`, loaded from `diagnostic-service/data/dependency_graph.json`. `neo4j` is optional (behind a compose profile).

## 5. Configuration (env vars)

| Service | Variables |
|---|---|
| ingestion | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_INPUT_TOPIC`, `KAFKA_OUTPUT_TOPIC`, `KAFKA_DLT_TOPIC`, `STRICT_VALIDATION` |
| forecasting | `PROMETHEUS_URL`, `PROMETHEUS_STEP`, `FORECAST_WINDOW_MINUTES`, `FORECAST_INTERVAL_SECONDS`, `ALERT_MIN_LEAD_MINUTES`, `COMPONENTS_CONFIG` (default `config/components.yaml`, hot-reloaded on mtime change), `TFT_MODEL_VERSION`, `TFT_MODEL_PATH`, `DIAGNOSTIC_URL` (default `http://diagnostic-service:8082`), `FORECASTER` (`auto`\|`tft`\|`statistical`, default `auto` = TFT if model + ML deps are present, otherwise statistical) |
| diagnostic | `GRAPH_BACKEND`, `GRAPH_SEED_PATH`, `GRAPH_DB_URL`, `GRAPH_DB_USER`, `GRAPH_DB_PASSWORD`, `LLM_PROVIDER` (`ollama`\|`anthropic`\|`openai`\|`none`), `LLM_API_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_TIMEOUT_SECONDS`, `PLAYBOOK_STORE_DIR`, `PLAYBOOK_MAX_COUNT`, `PLAYBOOK_RETENTION_DAYS`, `DIAGNOSE_RATE_LIMIT_PER_MINUTE` |

If the LLM fails or is unset, diagnostics fall back to a rule-based playbook built from the graph, so the system always produces a playbook.

## 6. Python packaging

- `requirements.txt`: core runtime, **no torch**.
- `requirements-ml.txt` (forecasting only): torch, lightning, pytorch-forecasting.
- `requirements-dev.txt`: pytest, ruff, mypy, respx/httpx test helpers.
- Dockerfiles take `ARG INSTALL_ML=false`.
- Target is Python 3.11.
- Tests live in `<service>/tests/` and must pass **without** ML deps, network, Kafka or an LLM.

## 7. Prometheus metrics (names are fixed — dashboards and alert rules depend on them)

**ingestion** (Micrometer → Prometheus naming):

| Metric | Type / labels |
|---|---|
| `ingestion_events_total{outcome="valid\|invalid\|error"}` | counter |
| `ingestion_processing_seconds` | timer, histogram enabled |
| `kafka_consumer_fetch_manager_records_lag_max` | built-in |

**forecasting:**

| Metric | Type / labels |
|---|---|
| `forecast_runs_total{component,outcome="ok\|error"}` | counter |
| `forecast_latency_seconds` | histogram |
| `predictive_alerts_total{component,severity}` | counter |
| `predictive_alerts_open` | gauge |
| `forecaster_info{forecaster,model_version}` | gauge = 1 |

**diagnostic:**

| Metric | Type / labels |
|---|---|
| `diagnoses_total{outcome="ok\|fallback\|error"}` | counter |
| `diagnosis_latency_seconds` | histogram |
| `graph_context_seconds` | histogram |
| `llm_request_seconds{provider}` | histogram |
| `llm_failures_total{provider}` | counter |
| `playbooks_stored_total` | counter |
| `playbooks_pruned_total` | counter |
| `diagnose_rate_limited_total` | counter |

Prometheus adds a `service` label per scrape job (`ingestion-service`, `forecasting-service`, `diagnostic-service`).

## 8. Ports & infra

| Port | Service |
|---|---|
| 8080 | ingestion |
| 8081 | forecasting |
| 8082 | diagnostic |
| 9092 | Kafka (host), `kafka:29092` inside compose |
| 9090 | Prometheus |
| 3000 | Grafana |
| 7474 / 7687 | Neo4j (profile `neo4j`) |
| 11434 | Ollama (profile `llm`) |

Paths:

- Prometheus alert rules: `infra/prometheus/rules/*.yml`, mounted at `/etc/prometheus/rules`.
- Grafana provisioning: `infra/grafana/provisioning/`; dashboards in `infra/grafana/dashboards/`.
- Grafana uses the `yesoreyeram-infinity-datasource` plugin to read `/alerts` and `/playbooks` JSON. This is the REQ-K dashboard.

## 9. Area ownership

| Owner | Files |
|---|---|
| Software Architect | `docs/ARCHITECTURE.md`, `docs/adr/**`, `contracts/**` (JSON Schemas), `docs/PLAN.md`, `docs/STRUCTURE.md`, `docs/CONTRACTS.md` |
| Data Engineer | `ingestion-service/**` (except `Dockerfile`), `scripts/produce-events.sh` |
| AI Engineer | `forecasting-service/**`, `diagnostic-service/**` (except `Dockerfile`s) |
| DevOps Automator | all `Dockerfile`s, `.dockerignore`s, `infra/docker-compose.yml`, `infra/k8s/**`, `.github/**`, `scripts/setup-local.sh`, `Makefile`, `.gitignore` |
| SRE | `infra/prometheus.yml`, `infra/prometheus/**`, `infra/grafana/**`, `dashboard/**`, `docs/SLO.md`, `docs/runbooks/**` |

`README.md` and `LICENSE` are maintained by the lead.
