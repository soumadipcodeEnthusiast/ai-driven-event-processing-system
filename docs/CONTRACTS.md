# Integration Contracts (source of truth)

Every service, script, dashboard and manifest must agree with this file. Change it only through the Software Architect (and note the change in `docs/adr/`).

## 1. Kafka

| Topic | Partitions | Key | Value |
|---|---|---|---|
| `raw-events` | 6 | any / none | Raw event JSON (§1.1) |
| `normalized-events` | 6 | `event_id` | NormalizedEvent JSON (§1.2) |
| `raw-events.DLT` | 1 | original key | **original raw bytes unchanged**; headers `x-error-reason` (string), `x-original-topic`, `x-original-partition`, `x-original-offset` |
| `anomaly-audit` | 3 | `event_id` | AnomalyAuditRecord JSON (§1.3), produced by diagnostic-service (REQ-019) |

Consumer groups: `ingestion-consumer-group` (raw-events), `diagnostic-anomaly-scorer` (normalized-events, REQ-016), `event-archiver` (raw-events + normalized-events → S3, REQ-028).

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

### 1.3 AnomalyAuditRecord (`anomaly-audit`)

Schema: `contracts/anomaly-audit.schema.json`.

```json
{"audit_id":"6f1c…","event_id":"e-123","component_id":"ingestion-service","source":"checkout-api","type":"http_request",
 "event_timestamp":"2026-01-01T10:00:00Z","score":0.97,"threshold":0.8,"model_version":"ewma-zscore-1.0",
 "reason":"latency_ms","detected_at":"2026-01-01T10:00:00.250Z","schema_version":"1.0"}
```

- `reason`: payload field with the largest deviation, or `"type"` for a rare event type.
- Produced with `acks=all` + idempotence. A publish failure never blocks scoring; it is counted in `anomaly_audit_publish_failures_total` and logged (`event=anomaly_audit_failed`).

### 1.4 S3 archive layout (REQ-028 to REQ-030)

`s3://<S3_ARCHIVE_BUCKET>/<S3_ARCHIVE_PREFIX=events>/<topic>/dt=YYYY-MM-DD/hour=HH/<topic>-p<partition>-o<first offset, 20 digits>.jsonl.gz`

Gzipped NDJSON. Each line: `{"topic", "partition", "offset", "key", "kafka_timestamp_ms", "archived_at", "value"}`, where `value` is the parsed JSON, or `"value_text"` (string) for non-JSON bytes (e.g. a malformed raw event). Keys are deterministic per batch, so a replay overwrites the object instead of duplicating it. Retention is a bucket lifecycle rule (REQ-029; Terraform `archive_retention_days`, default 365).

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
| GET | `/predictions?component_id=&metric_name=` | latest Prediction per (component, metric) (REQ-023) |
| GET | `/predictions/history?component_id=&model_version=&limit=100` | stored Predictions, newest first (REQ-024) |
| GET | `/thresholds` | current thresholds per component/metric |
| PUT | `/thresholds/{component_id}` | body `{"<metric_name>": float, ...}`; takes effect without restart (REQ-052) |

A background loop runs a forecast for every configured component every `FORECAST_INTERVAL_SECONDS` (default 60). `/forecast`'s per-metric `details.metrics.<name>` include `confidence` and `low_confidence` (REQ-025).

Only one OPEN alert may exist per (component, metric) at a time. There is
**no hand-off to diagnostic-service**: that coupling (formerly `POST
{DIAGNOSTIC_URL}/diagnose/{component_id}`, ADR-0001) was removed when
diagnostic-service was rescoped to anomaly detection — see §2's
diagnostic-service entry and `docs/adr/0008`.

### diagnostic-service :8082

Anomaly detection (SRS v1.0 §4.4, REQ-016 to REQ-020); see `docs/adr/0008`.

| Method | Path | Notes |
|---|---|---|
| GET | `/health`, `/ready`, `/metrics` | ops |
| POST | `/score/{event_id}` | body `{"event": NormalizedEvent-like}` → AnomalyScore (§3). The path `event_id` wins over the body's. |
| GET | `/thresholds` | current anomaly-detection threshold → `{"threshold": float}` |
| PUT | `/thresholds` | body `{"threshold": float in [0, 1]}`; takes effect without restart (REQ-020); 422 when out of range |
| GET | `/model` | `{"model_version", "streams"}` |
| PUT | `/model` | body `{"model_version": str, "warm_start": bool=true}`; swaps the scorer without downtime (REQ-018) |

With `EVENT_CONSUMER_ENABLED=true` every event on `normalized-events` is scored too. Anomalies (score strictly greater than the threshold) are published to `ANOMALY_AUDIT_TOPIC` (§1.3, REQ-019).

### Authentication (REQ-046)

Every non-ops route of forecasting- and diagnostic-service requires `Authorization: Bearer <JWT>` when `OAUTH2_ENABLED=true`: signature checked against `OAUTH2_JWKS_URL` (RS*/ES*), `exp` required, `iss`/`aud` checked when `OAUTH2_ISSUER`/`OAUTH2_AUDIENCE` are set. ingestion-service applies the same rules to all non-health actuator endpoints (`OAUTH2_ISSUER_URI` / `OAUTH2_JWKS_URL` / `OAUTH2_AUDIENCE`; neither URI set → every token rejected). `/health`, `/ready`, `/metrics`, `/actuator/health/**` and `/actuator/prometheus` stay open for probes and scraping.

## 3. Data models (JSON)

**PredictiveAlert**

```
alert_id ("al-<12 hex>"), component_id, metric_name, predicted_breach_time (UTC ISO),
predicted_value, threshold, lead_time_minutes, severity (low|medium|high|critical),
status (open|acknowledged|resolved), created_at, model_version
```

Severity is based on the predicted overshoot of the threshold (p90):

| Overshoot | Severity |
|---|---|
| < 10 % | low |
| < 25 % | medium |
| < 50 % | high |
| ≥ 50 % | critical |

An alert is emitted for any predicted p90 breach in the horizon. `predicted_breach_time` is the first p90 crossing (or *now* if the metric is already over the threshold), `lead_time_minutes` is the real lead (≥ 0), and `predicted_value` is the **peak** p90 in the horizon, which drives severity. `ALERT_MIN_LEAD_MINUTES` (default 10) is the REQ-025 *target* (closest SRS v1.0 §4.5 fit; no requirement there is phrased as lead-time alerting verbatim): alerts below it are still raised, logged as late, and show up on the dashboard and SLO.

There is no `playbook_id` field any more (ADR-0001 superseded, docs/adr/0008) — see §2.

**AnomalyScore** (diagnostic-service `POST /score`, REQ-016 to REQ-020)

```
event_id, score (float in [0, 1], higher = more anomalous), model_version, threshold,
anomalous (bool, score > threshold), audited (bool, audit record handed to the producer),
reason (payload field | "type" | null during warm-up)
```

Scoring model `ewma-zscore`: per (component_id or source, type) stream, an exponentially weighted mean/variance per numeric payload field; score = logistic(max |z| − 3), so 0.5 ≈ 3σ and the default 0.8 threshold ≈ 4.4σ. Streams score 0 until they have 10 observations.

**Prediction** (forecasting-service `GET /predictions`, REQ-021 to REQ-025)

```
prediction_id, component_id, metric_name, model_version, forecaster (ml|statistical), created_at,
step_seconds, timestamps[], point[], upper_90[], confidence (0..1), low_confidence (bool)
```

`confidence = 1 / (1 + w)` with `w` the p90 half-width at the horizon end relative to the predicted level; `low_confidence` when below `PREDICTION_MIN_CONFIDENCE` (default 0.5).

`IncidentPlaybook` and the dependency-graph node model are retired along with GraphRAG/LLM diagnosis — see `docs/adr/0008`.

## 4. Components monitored

The component IDs are: `ingestion-service`, `forecasting-service`, `diagnostic-service`, `kafka`, `zookeeper`, `prometheus`. (`graph-db` and `llm` are removed — those components no longer exist; see `docs/adr/0008`.)

## 5. Configuration (env vars)

| Service | Variables |
|---|---|
| ingestion | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_INPUT_TOPIC`, `KAFKA_OUTPUT_TOPIC`, `KAFKA_DLT_TOPIC`, `STRICT_VALIDATION`, `OAUTH2_ISSUER_URI` / `OAUTH2_JWKS_URL` / `OAUTH2_AUDIENCE` (REQ-046), `SSL_ENABLED` / `SSL_KEYSTORE_PATH` / `SSL_KEYSTORE_PASSWORD` (REQ-047, TLS off by default) |
| forecasting | `PROMETHEUS_URL`, `PROMETHEUS_STEP`, `FORECAST_WINDOW_MINUTES`, `FORECAST_INTERVAL_SECONDS`, `ALERT_MIN_LEAD_MINUTES`, `COMPONENTS_CONFIG` (default `config/components.yaml`, hot-reloaded on mtime change), `FORECASTER` (`auto`\|`ml`\|`statistical`, default `auto` = the trained model at `ML_MODEL_PATH` if present, else statistical), `ML_MODEL_PATH` (default `/models/forecast_model.json`), `ML_MODEL_VERSION` (empty = version stored in the model file), `PREDICTION_MIN_CONFIDENCE` (0.5), `PREDICTIONS_PATH` (JSONL; empty = memory only), `OAUTH2_ENABLED` / `OAUTH2_JWKS_URL` / `OAUTH2_ISSUER` / `OAUTH2_AUDIENCE` |
| diagnostic | `MODEL_VERSION` (`ewma-zscore-1.0`), `SCORER_ALPHA` (0.05), `SCORER_MIN_SAMPLES` (10), `ANOMALY_THRESHOLD` (0.8), `KAFKA_BOOTSTRAP_SERVERS`, `ANOMALY_AUDIT_TOPIC`, `EVENT_CONSUMER_ENABLED` (false), `EVENT_INPUT_TOPIC`, `EVENT_CONSUMER_GROUP`, `OAUTH2_ENABLED` / `OAUTH2_JWKS_URL` / `OAUTH2_ISSUER` / `OAUTH2_AUDIENCE` |
| event-archiver (`storage/`) | `KAFKA_BOOTSTRAP_SERVERS`, `ARCHIVE_TOPICS` (`raw-events,normalized-events`), `ARCHIVE_GROUP_ID`, `ARCHIVE_BATCH_SIZE` (1000), `ARCHIVE_FLUSH_SECONDS` (60), `S3_ARCHIVE_BUCKET`, `S3_ARCHIVE_REGION`, `S3_ARCHIVE_PREFIX` (`events`), `S3_ARCHIVE_KMS_KEY_ID` (optional SSE-KMS), `S3_ENDPOINT_URL` (MinIO locally; empty = real AWS S3), `METRICS_PORT` (8083); AWS credentials via the standard chain (IRSA in EKS) |

`GRAPH_BACKEND`, `GRAPH_SEED_PATH`, `GRAPH_DB_URL`/`USER`/`PASSWORD`, `LLM_*` and `PLAYBOOK_*` are all removed (docs/adr/0008).

## 6. Python packaging

- `requirements.txt`: core runtime, **no torch, no tensorflow**.
- `requirements-ml.txt` (forecasting only): tensorflow-cpu, scikit-learn, pandas — needed by `training/`, **not** for serving (the trained model is JSON, inference is numpy). diagnostic-service's `requirements.txt` adds `confluent-kafka` (REQ-019 producer, REQ-016 consumer); both API services add `PyJWT[crypto]` (REQ-046).
- `requirements-dev.txt`: pytest, ruff, mypy (+ respx/httpx test helpers for forecasting).
- Dockerfiles take `ARG INSTALL_ML=false`.
- Target is Python 3.11.
- Tests live in `<service>/tests/` (and `storage/tests/`) and must pass **without** ML deps, network, Kafka or S3 (fakes are injected).

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
| `predictions_low_confidence_total{component}` | counter (REQ-025) |

**diagnostic** (rescoped to anomaly detection, docs/adr/0008 — `graph_context_seconds`, `llm_request_seconds`, `llm_failures_total`, `playbooks_stored_total`, `playbooks_pruned_total`, `diagnoses_total` and `diagnosis_latency_seconds` are retired):

| Metric | Type / labels |
|---|---|
| `anomaly_score` | histogram (REQ-016) |
| `anomalies_detected_total` | counter (REQ-017) |
| `anomaly_audit_publish_failures_total` | counter (REQ-019) |
| `anomaly_scoring_latency_seconds` | histogram |
| `anomaly_audit_published_total` | counter (broker-acknowledged audit records) |
| `anomaly_events_scored_total{source="http\|kafka"}` | counter |
| `anomaly_consumer_errors_total{reason="kafka\|malformed\|unexpected"}` | counter |
| `anomaly_threshold` | gauge (REQ-020) |
| `anomaly_model_info{model_version}` | gauge = 1 (REQ-018) |

**event-archiver** (REQ-028):

| Metric | Type / labels |
|---|---|
| `archive_records_total{topic}` | counter |
| `archive_objects_total{topic}` | counter |
| `archive_failures_total{topic}` | counter (batch rewound and retried) |
| `archive_flush_seconds` | histogram |
| `archive_last_success_timestamp_seconds` | gauge |

Prometheus adds a `service` label per scrape job (`ingestion-service`, `forecasting-service`, `diagnostic-service`, `event-archiver`).

## 8. Ports & infra

Services listen on their own port inside the compose network and in Kubernetes; compose publishes them to the host in the 9000 range (override with `*_HOST_PORT`) so they don't collide with common dev tools on 3000/8080/8081.

| Service | Service port (in-network, k8s) | Host port (compose) |
|---|---|---|
| ingestion | 8080 | 9080 |
| forecasting | 8081 | 9081 |
| diagnostic | 8082 | 9082 |
| event-archiver metrics | 8083 | 9083 |
| Grafana | 3000 | 9030 |
| Prometheus | 9090 | 9090 |
| Kafka | `kafka:29092` | 9092 |
| MinIO API / console (REQ-028 local S3 stand-in) | 9000 / 9001 | 9000 / 9001 |
| Jenkins (local CI, `ci/jenkins/run-local.sh`) | 8080 | 9095 |
| Demo console (local only, `console/`) | 8099 | 9099 |
| Neo4j browser / bolt (GraphRAG) | 7474 / 7687 | 9074 / 9687 |
| Ollama (GraphRAG LLM) | 11434 | 9434 |

(Neo4j and Ollama were removed by docs/adr/0008 and reinstated by docs/adr/0010. Kafka topic `incident-playbooks` (3 partitions, key = component id) carries every GraphRAG playbook: the `contracts/incident-playbook.schema.json` fields plus `trigger`, `anomalies`, `outcome`, `graph_backend`, `unhealthy_dependencies`, `latency_seconds`.)

Paths:

- Prometheus alert rules: `infra/prometheus/rules/*.yml`, mounted at `/etc/prometheus/rules`.
- Grafana provisioning: `infra/grafana/provisioning/`; dashboards in `infra/grafana/dashboards/`.
- The REQ-034 dashboard (`service-health`) reads only the Prometheus data source now. The `yesoreyeram-infinity-datasource` plugin, previously used to read `/alerts` and `/playbooks` JSON directly, is removed along with the `aiops-overview` dashboard (docs/adr/0008).

## 9. Area ownership

| Owner | Files |
|---|---|
| Software Architect | `docs/ARCHITECTURE.md`, `docs/adr/**`, `contracts/**` (JSON Schemas), `docs/PLAN.md`, `docs/STRUCTURE.md`, `docs/CONTRACTS.md` |
| Data Engineer | `ingestion-service/**`, `storage/**` (event-archiver) (except `Dockerfile`s), `scripts/produce-events.sh` |
| AI Engineer | `forecasting-service/**`, `diagnostic-service/**` (except `Dockerfile`s) |
| DevOps Automator | all `Dockerfile`s, `.dockerignore`s, `infra/docker-compose.yml`, `infra/k8s/**`, `infra/terraform/**`, `Jenkinsfile`, `scripts/setup-local.sh`, `Makefile`, `.gitignore` |
| DevOps Engineer | `infra/prometheus.yml`, `infra/prometheus/**`, `infra/grafana/**`, `dashboard/**`, `docs/SLO.md`, `docs/runbooks/**` |

`README.md` and `LICENSE` are maintained by the lead. "DevOps Engineer" here
(and in the table above) is the closest SRS v1.0 §2.3 user class to the old
"SRE" label; it is not one of the SRS actors verbatim, since the SRS defines
no separate SRE role.
