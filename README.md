# AI-Driven Event Processing Architecture

An event-processing platform that **ingests and normalises high-volume events through Kafka**, **archives them to Amazon S3**, and runs three AI stages alongside it: **anomaly detection** (scores every event), **GraphRAG root-cause analysis** (Neo4j dependency graph + local LLM → incident playbooks) and **predictive analytics** with a **Temporal Fusion Transformer** (forecasts the platform's own metrics ahead of a breach). Scope decisions: [`docs/adr/0008`](docs/adr/0008-rescope-diagnostic-service-to-anomaly-detection.md) and [`docs/adr/0010`](docs/adr/0010-reinstate-graphrag-and-tft.md).

The system has four paths:

- **Synchronous data path.** A Java / Spring Boot service consumes raw events from Kafka, validates them against a schema, normalises them into a canonical `NormalizedEvent`, and publishes them downstream. Malformed events go to a dead-letter topic. (REQ-001 to REQ-005, REQ-008)
- **Archival.** `event-archiver` streams `raw-events` and `normalized-events` into S3 as partitioned, gzipped NDJSON, committing Kafka offsets only after each object is stored. (REQ-028 to REQ-030)
- **Predictive analytics (TFT).** `forecasting-service` watches the platform's own components (CPU, memory, error-rate, consumer-lag) through Prometheus. It forecasts with a Temporal Fusion Transformer (PyTorch, trained on this platform's history with `make train-tft`), falling back to a trained autoregressive model or damped-trend Holt. When the 90th-percentile forecast is expected to cross a threshold at least 10 minutes out, it raises a `PredictiveAlert`; every forecast is versioned, stored, and confidence-flagged. (REQ-021 to REQ-025)
- **Anomaly detection.** `diagnostic-service` scores every normalized event against per-stream baselines, flags scores above a runtime-adjustable threshold, and records each anomaly to a Kafka audit topic. (REQ-016 to REQ-020)
- **GraphRAG root-cause analysis.** Each anomalous component (batched, with a cooldown) and each new predictive alert is diagnosed: its two-hop neighbourhood is retrieved from **Neo4j**, rendered into a prompt, and a local LLM (**Ollama**, `llama3.2:3b`) writes a ranked remediation playbook. Playbooks go to the `incident-playbooks` topic and `GET /playbooks`; a rule-based playbook is produced whenever the LLM is unavailable.

A **Grafana dashboard** shows live metrics and alerts (REQ-034). APIs are protected with **OAuth 2.0** bearer tokens (REQ-046) and served over **TLS 1.2+** at the ingress (REQ-047). AWS infrastructure is **Terraform** (REQ-044) and CI/CD is **Jenkins**.

---

## How it works

```
                 Producers (apps, devices, logs)
                              │  JSON events
                              ▼
                    ┌──────────────────┐
                    │ Kafka: raw-events│  (6 partitions)
                    └──┬────────────┬──┘
                       ▼            └──────────────────────────────┐
 ┌────────────────────────────────────────────────┐                │
 │ ingestion-service  (Java 17 / Spring Boot 3)   │  SYNCHRONOUS   │
 │  parse → validate (REQ-005) → normalize (REQ-008)│  PATH        │
 │  batch listener, manual ack after send (REQ-001/004/040)│        │
 └───────┬──────────────────────────────┬─────────┘                │
         │ valid                        │ invalid / retries exhausted
         ▼                              ▼                          │
 ┌─────────────────────────┐   ┌────────────────────────┐          │
 │ Kafka: normalized-events│   │ Kafka: raw-events.DLT  │          │
 └──┬───────────────────┬──┘   └────────────────────────┘          │
    │                   └──────────────────────────────────────────┤
    ▼                                                              ▼
 ┌────────────────────────────────────────┐   ┌─────────────────────────────────┐
 │ diagnostic-service (Python / FastAPI)  │   │ event-archiver (Python)         │
 │  score every event      (REQ-016)      │   │  batch per partition → S3 PUT   │
 │  score > threshold?     (REQ-017/020)  │   │  commit offsets after upload    │
 │  swap model, no downtime (REQ-018)     │   │  (REQ-028 to REQ-030)           │
 └───────────────┬────────────────────────┘   └───────────────┬─────────────────┘
                 ▼                                            ▼
     ┌──────────────────────┐                  ┌──────────────────────────────┐
     │ Kafka: anomaly-audit │ (REQ-019)        │ Amazon S3 (MinIO locally)    │
     └──────────────────────┘                  │ events/<topic>/dt=…/hour=…/  │
                                               └──────────────────────────────┘

 ───────────── metrics: every service ──► Prometheus (+ alert rules) ─────────────

 ┌────────────────────────────────────────────────┐   PREDICTIVE ANALYTICS
 │ forecasting-service  (Python / FastAPI)        │   (independent of anomaly
 │  every 60 s, per component in components.yaml: │    detection — no hand-off)
 │  query_range → forecast (p50 + p90) → thresholds│
 │  → PredictiveAlert + stored Prediction (REQ-021..025)│
 └────────────────────────────────────────────────┘
                         │
                         ▼
             ┌──────────────────────────────┐
             │ Grafana :3000 (REQ-034)      │
             │ throughput + error rates     │
             └──────────────────────────────┘
```

### End-to-end flow

1. A producer publishes a JSON event to `raw-events`. `make produce` generates sample traffic.
2. `ingestion-service` validates the event against [`contracts/raw-event.schema.json`](contracts/raw-event.schema.json). The required fields are `event_id`, `timestamp` (ISO-8601 with offset) and `source`. Strict mode rejects unknown keys.
   - **Valid:** the event is published to `normalized-events`, keyed by `event_id`. It carries a UTC timestamp, defaults, `schema_version` and `ingested_at`.
   - **Invalid:** the original bytes go to `raw-events.DLT`, with `x-error-reason` and `x-original-*` headers.
   - **Unexpected error:** it is retried 3× with back-off, then dead-lettered.
   - Offsets are committed only after the broker acks the sends.
3. `event-archiver` consumes both `raw-events` and `normalized-events` and writes one gzipped NDJSON object per (topic, partition) batch to `s3://<bucket>/events/<topic>/dt=YYYY-MM-DD/hour=HH/`. It commits offsets only after the upload succeeds; on failure it rewinds and retries, and batch keys are deterministic, so a replay overwrites instead of duplicating.
4. `diagnostic-service` consumes `normalized-events` and scores each event (0–1) against an exponentially weighted baseline per (component, event type) and per numeric payload field, plus a rarity signal for unusual event types. Scores above the threshold (default 0.8 ≈ 4.4σ) are logged and published to `anomaly-audit`. `POST /score/{event_id}` gives the same result on demand.
5. Prometheus scrapes every service every 15 s.
6. `forecasting-service` runs a cycle every `FORECAST_INTERVAL_SECONDS`.
   - For each enabled component and metric, it pulls the last 60 min and forecasts the next 60 min, with a confidence score (low-confidence predictions are flagged).
   - If the p90 crosses the threshold, it opens a `PredictiveAlert`, with severity based on the overshoot; the 10-minute lead time is the target.
   - Only one open or acknowledged alert is allowed per (component, metric).
   - Thresholds hot-reload from `config/components.yaml` or can be changed with `PUT /thresholds/{component}` (REQ-052).
   - Every forecast is stored with its `model_version` and is queryable at `GET /predictions` (REQ-023, REQ-024).
7. The Grafana dashboard shows real-time throughput, error rates, anomalies and archival (REQ-034).

Detailed sequence diagrams and failure modes are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The binding interface spec is [`docs/CONTRACTS.md`](docs/CONTRACTS.md).

---

## Services

| Service | Stack | Host port | Responsibility |
|---|---|---|---|
| `ingestion-service` | Java 17, Spring Boot 3.2, Spring Kafka, Micrometer, Spring Security (OAuth2 resource server) | 9080 | Kafka consumer, schema validation, normalisation, DLT routing |
| `forecasting-service` | Python 3.11, FastAPI, numpy; scikit-learn for training | 9081 | Prometheus polling, forecasting, confidence flagging, prediction store, alert store |
| `diagnostic-service` | Python 3.11, FastAPI, confluent-kafka | 9082 | Per-event anomaly scoring, threshold evaluation, Kafka audit publishing, model swaps |
| `event-archiver` | Python 3.11, confluent-kafka, boto3 | 9083 (metrics) | Kafka → S3 archival |
| `demo-console` | Python 3.11, FastAPI, vanilla JS | 9099 | Local only: live pipeline dashboard (Kafka, GraphRAG, TFT, health) and a click-through demo |
| Neo4j | neo4j:5.26 community | 9074 (browser), 9687 (bolt) | Dependency graph for GraphRAG |
| Ollama | ollama/ollama, llama3.2:3b | 9434 | Local LLM that writes GraphRAG playbooks |
| Grafana | Grafana 11 | 9030 | REQ-034 dashboard, service health, SLOs |
| Prometheus | Prometheus 2.52 | 9090 | Scraping, recording and alert rules |
| MinIO | minio/minio | 9000 (API), 9001 (console) | Local S3 stand-in (replaced by your real bucket in AWS) |

Host ports are in the 9000 range so they don't clash with common dev tools; inside Docker and Kubernetes each service keeps its own port (8080–8083, Grafana 3000). See docs/CONTRACTS.md §8.

### HTTP APIs

With `OAUTH2_ENABLED=true`, every route except `/health`, `/ready`, `/metrics` needs `Authorization: Bearer <JWT>`.

| Service | Method & path | Purpose |
|---|---|---|
| ingestion | `GET /actuator/health/{liveness,readiness}`, `/actuator/prometheus` | Probes, metrics (unauthenticated; every other endpoint needs a valid JWT) |
| forecasting | `POST /forecast` `{"component_id": "...", "forecast_window_minutes": 60}` | Run one forecast cycle now |
| forecasting | `GET /alerts?status=&component_id=&limit=` · `GET /alerts/{id}` | List or get alerts |
| forecasting | `GET /alerts/summary` | Live counts and minutes to the soonest predicted breach |
| forecasting | `POST /alerts/{id}/ack` · `POST /alerts/{id}/resolve` | Alert lifecycle |
| forecasting | `GET /predictions` · `GET /predictions/history` | Latest / versioned predictions with confidence (REQ-023, REQ-024, REQ-025) |
| forecasting | `GET /thresholds` · `PUT /thresholds/{component_id}` | Read or override thresholds at runtime (REQ-052) |
| diagnostic | `POST /score/{event_id}` `{"event": {...}}` | Score, evaluate, and audit-publish one event (REQ-016/017/019) |
| diagnostic | `GET /thresholds` · `PUT /thresholds` | Read or override the detection threshold (REQ-020) |
| diagnostic | `GET /model` · `PUT /model` `{"model_version": "...", "warm_start": true}` | Swap the scoring model without downtime (REQ-018) |
| both Python | `GET /health`, `/ready`, `/metrics`, `/docs` | Ops + OpenAPI UI |

### Kafka topics

| Topic | Partitions | Producer → Consumer |
|---|---|---|
| `raw-events` | 6 | external producers → `ingestion-service`, `event-archiver` |
| `normalized-events` | 6 | `ingestion-service` → `diagnostic-service`, `event-archiver` |
| `raw-events.DLT` | 1 | `ingestion-service` → manual inspection / replay |
| `anomaly-audit` | 3 | `diagnostic-service` → audit consumers |

### Data models

These are JSON Schemas in [`contracts/`](contracts/): `RawEvent`, `NormalizedEvent`, `PredictiveAlert` and `AnomalyAuditRecord`. The Python tests validate the services' output against them.

---

## Requirements

REQ-IDs follow the finalized SRS (`AI_Event_Processing_SRS_V2.pdf`, Version
1.0). A few mappings are the closest reasonable fit rather than an exact
textual match — see the inline notes, and [`docs/PLAN.md`](docs/PLAN.md).

| ID | Requirement | Acceptance criterion | Where it's implemented / verified |
|---|---|---|---|
| REQ-001 | Accept concurrent producer publications | Consumer lag < 500 ms at 10 000 events/s | `EventConsumer`, `KafkaConfig` · lag alert + SLO |
| REQ-004 | Ack writes within 50 ms | Measured in Phase 5 load test | `EventConsumer` |
| REQ-005 | Reject malformed events | Rejected with structured logs, sent to DLT | `NormalizationService.validate`, `DeadLetters` |
| REQ-008 | Enrich/normalise events (§4.2 Stream Preprocessing) | All fields populated, UTC, schema version | `NormalizationService.normalize` |
| REQ-040 | Idempotent, at-least-once consumer | No data loss on redelivery | `EventConsumer`, `KafkaConfig` (manual ack after send) |
| REQ-016 | Compute an anomaly score per event | Every normalized event scored | `anomaly_scorer.py`, `event_consumer.py` · `anomaly_events_scored_total` |
| REQ-017 | Alert when score exceeds threshold | `score > threshold` → anomaly | `threshold_manager.py`, `detector.py` |
| REQ-018 | Swap models without downtime | `PUT /model` while serving | `detector.swap_model`, `AnomalyScorer.with_version` |
| REQ-019 | Record anomalies to a Kafka audit topic | Audit record per anomaly | `audit_publisher.py` → `anomaly-audit` · `AnomalyAuditPublishFailing` alert |
| REQ-020 | Runtime-adjustable detection threshold | No redeploy | `PUT /thresholds` (validated, audited) |
| REQ-021 | Time-series forecasting from historical data | Forecast latency < 2 s per 60-min window | `forecasters.py`, `predictive_analytics.py` · `forecast_latency_seconds` SLO |
| REQ-022 | Refresh predictions at a configurable interval | `FORECAST_INTERVAL_SECONDS` | `main.py` background loop |
| REQ-023 | Query API for predictions | `GET /predictions` | `main.py`, `PredictionStore` |
| REQ-024 | Version and store prediction outputs | Each prediction tagged with `model_version`, persisted | `PredictionStore` (memory + JSONL) |
| REQ-025 | Flag low-confidence predictions; alert ahead of breach | `low_confidence` flag; alert ≥ 10 min before breach | `band_confidence`, `forecasting_engine.py` |
| REQ-028 | Archive raw and processed events in S3 | Both topics land in S3 | `storage/archiver.py`, `storage/archive_client/` · `EventArchiveStalled` alert |
| REQ-029 | Retention policy | Lifecycle: Glacier IR after 30 d, delete after 365 d | `infra/terraform/s3.tf` (MinIO: `minio-init`) |
| REQ-030 | Atomic writes | One `PutObject` per batch, deterministic keys | `ArchiveClient.archive_batch` |
| REQ-034 | Observability dashboard, real-time throughput/error rates | Visible within 5 s | `infra/grafana/dashboards/service-health.json` |
| REQ-044 | Infrastructure-as-Code provisioning | `terraform validate` | `infra/terraform/` (EKS, S3, IRSA, ECR) |
| REQ-046 | OAuth 2.0 on all API requests | Invalid/missing tokens → 401 | `SecurityConfig.java`, `app/auth.py` (both Python services) |
| REQ-047 | TLS 1.2+ in transit | ALB policy TLS 1.2/1.3; S3 denies non-TLS | `infra/k8s/ingress.yaml`, `server.ssl` (`SSL_ENABLED`), `s3.tf` bucket policy |
| REQ-052 | Runtime-configurable thresholds (Administration §4.11) | No redeploy needed | `components.py` hot reload, `PUT /thresholds` |

GraphRAG root-cause analysis and the TFT forecaster are in scope by the
project owner's decision ([`docs/adr/0010`](docs/adr/0010-reinstate-graphrag-and-tft.md));
SRS v1.0 lists GraphRAG under Appendix C and should be updated to match.
SLOs and error budgets are in [`docs/SLO.md`](docs/SLO.md).

---

## Quick start

**Prerequisites:** Docker with Compose v2. For running tests outside containers you also need JDK 17 and Python 3.11.

```bash
git clone <repo-url> && cd ai-driven-system

make up                      # build + start everything, wait until healthy
make produce ARGS="-n 2000 -i 0.1 -r 200"   # 2000 events, 10 % invalid, 200/s
make logs SERVICE=diagnostic-service
make down                    # stop and delete volumes
```

`./scripts/setup-local.sh` does the same as `make up`, with flags `--no-build`, `--logs` and `--down`.

| URL | What |
|---|---|
| http://localhost:9099 | **Demo console**: live event flow, Kafka topics and consumer lag, service health, guided demo (`make console`) |
| http://localhost:9074 | Neo4j browser (`neo4j` / `localdevpassword`): `MATCH (c:Component)-[r]->(d) RETURN c,r,d` |
| http://localhost:9030 | Grafana (service health, SLOs) |
| http://localhost:9090 | Prometheus (targets, rules, alerts) |
| http://localhost:9081/docs | forecasting-service OpenAPI |
| http://localhost:9082/docs | diagnostic-service OpenAPI |
| http://localhost:9080/actuator/health | ingestion-service |
| http://localhost:9083/metrics | event-archiver metrics |
| http://localhost:9001 | MinIO console (login `minioadmin` / `minioadmin`) — browse `event-archive/events/` |

Try it by hand:

```bash
# Predictive analytics: force an alert on the next cycle, then look at alerts and predictions
curl -s -XPUT localhost:9081/thresholds/ingestion-service -H 'content-type: application/json' \
     -d '{"cpu_utilisation": 0.01}'
curl -s localhost:9081/alerts | jq
curl -s 'localhost:9081/predictions?component_id=ingestion-service' | jq '.[] | {metric_name, confidence, low_confidence}'

# Anomaly detection: build a baseline, then send an outlier
for i in $(seq 1 30); do
  curl -s -XPOST localhost:9082/score/e-$i -H 'content-type: application/json' \
       -d "{\"event\":{\"source\":\"checkout\",\"type\":\"request\",\"payload\":{\"latency_ms\":$((100 + i % 5))}}}" >/dev/null
done
curl -s -XPOST localhost:9082/score/spike -H 'content-type: application/json' \
     -d '{"event":{"source":"checkout","type":"request","payload":{"latency_ms":900}}}' | jq
# → "anomalous": true, "audited": true, "reason": "latency_ms"
docker compose -f infra/docker-compose.yml exec kafka \
  kafka-console-consumer --bootstrap-server kafka:29092 --topic anomaly-audit --from-beginning --max-messages 1
```

### Training the ML forecaster

```bash
cd forecasting-service
pip install -r requirements.txt -r requirements-ml.txt
python -m training.train_forecast_model --prometheus-url http://localhost:9090 --days 7 \
    --output models/forecast_model.json --version forecast-2026.10.20 --require-better
# or: make train VERSION=forecast-2026.10.20
```

The script backtests the model against the statistical baseline on held-out data (`--require-better` fails the run if it doesn't win) and writes a JSON model. Compose mounts `forecasting-service/models/` at `/models`, so with `FORECASTER=auto` the service picks it up on restart; otherwise it uses the statistical forecaster. A history CSV (`--csv`) works instead of Prometheus.

---

## AWS: attaching your S3 bucket

Locally the archiver writes to MinIO. To write to your own S3 bucket:

**From your laptop (compose):**

```bash
export S3_ARCHIVE_BUCKET=<your-bucket>  S3_ARCHIVE_REGION=ap-south-1
eval "$(aws configure export-credentials --format env)"   # or export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d --build
aws s3 ls s3://$S3_ARCHIVE_BUCKET/events/ --recursive | head   # objects appear within ARCHIVE_FLUSH_SECONDS (30 s)
```

The identity needs `s3:PutObject` on `arn:aws:s3:::<bucket>/events/*` (plus `kms:GenerateDataKey` if you set `S3_ARCHIVE_KMS_KEY_ID`).

**On EKS (Terraform + k8s):**

1. `cd infra/terraform && cp terraform.tfvars.example terraform.tfvars`. Set your VPC/subnets. If you already created the bucket, set `create_archive_bucket = false` and `archive_bucket_name = "<your-bucket>"`; otherwise Terraform creates it with encryption, versioning, public-access block, a TLS-only policy and the retention lifecycle.
2. `terraform init && terraform plan && terraform apply`.
3. Put `terraform output -raw archive_bucket_name` into `S3_ARCHIVE_BUCKET` in `infra/k8s/archiver-deployment.yaml`, and `terraform output -raw archiver_role_arn` into the `event-archiver` ServiceAccount's `eks.amazonaws.com/role-arn` annotation. IRSA provides the credentials, so there are no static keys.
4. Point the `images:` in `infra/k8s/kustomization.yaml` at `terraform output ecr_repositories`, then run `kubectl apply -k infra`.

---

## Development

```bash
make test        # ingestion (Maven verify incl. embedded-Kafka IT) + forecasting, diagnostic, storage pytest suites
make lint        # ruff + mypy + shellcheck + compose validation
make prom-test   # promtool check + unit tests for alert/recording rules
make tf-validate # terraform fmt -check + validate (Docker, no AWS credentials)
make k8s-validate
```

**Load test (JMeter):** `JMETER_HOME=… loadtest/run-load-test.sh --rate 2000 --duration 120` drives Kafka and the APIs, then prints a PASS/FAIL report against REQ-001/004 and the end-to-end flow. See [`loadtest/README.md`](loadtest/README.md).

**Jenkins locally:** `ci/jenkins/run-local.sh` starts Jenkins in Docker (http://127.0.0.1:9095), runs the Jenkinsfile against a snapshot of your working tree, and prints the result of each stage. Add `--load-test` to include the JMeter stage. See [`ci/jenkins/README.md`](ci/jenkins/README.md).

**Jira:** [`docs/jira/jira-import.csv`](docs/jira/) has the backlog (epics, a story per requirement with acceptance criteria, bugs, and Phase 5 work), ready for Jira's CSV import.

Python services, from inside the service directory (`storage/` for the archiver):

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest && ruff check . && mypy app          # storage: mypy archive_client archiver.py
uvicorn app.main:app --reload --port 9081   # 9082 for diagnostic
```

The ingestion service runs with `cd ingestion-service && ./mvnw verify`, and needs JDK 17 (`JAVA_HOME`). To run it against the compose Kafka: `KAFKA_BOOTSTRAP_SERVERS=localhost:9092 ./mvnw spring-boot:run`.

---

## Configuration

The full list is in [`docs/CONTRACTS.md` §5](docs/CONTRACTS.md). The most useful variables:

| Variable | Service | Default | Meaning |
|---|---|---|---|
| `STRICT_VALIDATION` | ingestion | `true` | Reject unknown top-level fields |
| `FORECAST_INTERVAL_SECONDS` | forecasting | `60` | Background cycle period (≤ 0 disables it) |
| `FORECAST_WINDOW_MINUTES` | forecasting | `60` | History and horizon window |
| `ALERT_MIN_LEAD_MINUTES` | forecasting | `10` | Target lead time for an alert |
| `COMPONENTS_CONFIG` | forecasting | `config/components.yaml` | PromQL + thresholds per component, hot-reloaded |
| `FORECASTER` / `ML_MODEL_PATH` | forecasting | `auto` / `/models/forecast_model.json` | `auto` uses the trained model when the file exists |
| `PREDICTION_MIN_CONFIDENCE` | forecasting | `0.5` | Below this, predictions are flagged `low_confidence` |
| `MODEL_VERSION` | diagnostic | `ewma-zscore-1.0` | Anomaly-scoring model version (REQ-018) |
| `ANOMALY_THRESHOLD` | diagnostic | `0.8` | Initial detection threshold (REQ-017, REQ-020) |
| `EVENT_CONSUMER_ENABLED` | diagnostic | `false` (`true` in compose/k8s) | Score every event on `normalized-events` |
| `S3_ARCHIVE_BUCKET` / `S3_ARCHIVE_REGION` | event-archiver | — | Target bucket (REQ-028) |
| `S3_ENDPOINT_URL` | event-archiver | MinIO in compose | Empty = real AWS S3 |
| `OAUTH2_ENABLED` / `OAUTH2_JWKS_URL` / `OAUTH2_ISSUER` / `OAUTH2_AUDIENCE` | forecasting, diagnostic | off | JWT validation (REQ-046); e.g. a Cognito user pool's JWKS |
| `OAUTH2_ISSUER_URI` / `OAUTH2_JWKS_URL` / `OAUTH2_AUDIENCE` | ingestion | unset (all tokens rejected) | Spring resource server (REQ-046) |
| `SSL_ENABLED` / `SSL_KEYSTORE_PATH` | ingestion | `false` | In-process TLS (REQ-047); normally TLS terminates at the ALB |

---

## Observability

- **Dashboards** (`infra/grafana/dashboards/`, generated by `infra/grafana/gen_dashboards.py`), described in [`dashboard/README.md`](dashboard/README.md): ingestion throughput/latency/lag/DLT, forecasting, anomaly detection (scores vs. threshold, events scored, anomalies, audit failures), archival (records/s, upload failures, time since the last upload), process resources, and SLOs.
- **Alert rules** (`infra/prometheus/rules/`):
  - Service alerts, multi-window burn-rate SLO alerts, `AnomalyAuditPublishFailing`, `EventArchiveUploadFailing` and `EventArchiveStalled`.
  - Each alert has a `runbook_url` that points into [`docs/runbooks/`](docs/runbooks/).
  - The rules are unit-tested with `promtool test rules` (`make prom-test`).
- **SLOs:** [`docs/SLO.md`](docs/SLO.md).

## Deployment & CI

- **Kubernetes** (`infra/k8s/`, apply with `kubectl apply -k infra`):
  - Namespace with restricted Pod Security.
  - Deployments, Services and ConfigMaps for all four services; `event-archiver` uses an IRSA ServiceAccount.
  - An ALB Ingress with TLS 1.2+ (ACM certificate) in front of the forecasting and diagnostic APIs.
  - An in-namespace Prometheus that discovers annotated pods and loads the same alert rules as compose. Forecasting reads from it.
  - Ingestion and diagnostic both autoscale (2 to 6 pods). Forecasting is pinned to a single replica, because its alert store is in memory (ADR-0007).
- **Terraform** (`infra/terraform/`): EKS cluster + managed node group, S3 archive bucket (encryption, versioning, lifecycle, TLS-only policy), IRSA role for the archiver, ECR repositories. The Azure provider block is present but commented out (portability NFR).
- **CI** (`Jenkinsfile`) runs, in parallel: Build (`mvn package` / `pip install`), Test (`mvn verify`, ruff/mypy/pytest for all three Python packages, promtool, `terraform validate`), Docker Build for the four images, an optional JMeter load test (`RUN_LOAD_TEST`), then Push to ECR (main branch only; needs the `ECR_REGISTRY` variable and the `aws-ecr-credentials` credential). Run it locally with `ci/jenkins/run-local.sh`.

---

## Repository layout

```
.
├── ingestion-service/      Spring Boot Kafka consumer (REQ-001 to REQ-005, REQ-008) + OAuth2 + tests
├── forecasting-service/    FastAPI forecasting, predictions, alerts (REQ-021 to REQ-025), config/, training/, models/, tests/
├── diagnostic-service/     FastAPI anomaly detection (REQ-016 to REQ-020) + Kafka consumer/producer, tests/
├── storage/                event-archiver: Kafka → S3 (REQ-028 to REQ-030), archive_client/, tests/
├── contracts/              JSON Schemas for every message / model
├── dashboard/              REQ-034 dashboard documentation
├── infra/                  docker-compose (+ AWS override), Prometheus (+ rules), Grafana, k8s, terraform
├── loadtest/               JMeter plan, runner, PASS/FAIL analyzer (REQ-001/004)
├── ci/jenkins/             Local Jenkins (Docker) that runs the Jenkinsfile
├── scripts/                setup-local.sh, produce-events.sh
├── docs/                   ARCHITECTURE, CONTRACTS, SLO, PLAN, STRUCTURE, adr/, runbooks/, jira/
├── Makefile
└── Jenkinsfile             CI/CD
```

See [`docs/STRUCTURE.md`](docs/STRUCTURE.md) for the full tree.

---

## Project status

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold | ✅ |
| 1 | Ingestion (REQ-001 to REQ-005, REQ-008) | ✅ |
| 1.5 | Terraform (REQ-044) | ✅ `validate` passes; `apply` needs your AWS account |
| 2 | Forecasting / Predictive Analytics (REQ-021 to REQ-025) | ✅ statistical + trained AR model, confidence flags, prediction store/API |
| 3 | Anomaly Detection (REQ-016 to REQ-020) | ✅ |
| 4 | Dashboard + observability (REQ-034) | ✅ |
| 4.5 | OAuth2 + TLS (REQ-046, REQ-047), S3 archival (REQ-028 to REQ-030) | ✅ (S3: MinIO locally; your bucket via `docker-compose.aws.yml` / Terraform) |
| 5 | Hardening: load test for REQ-001/004 at 10k ev/s, chaos tests, mTLS, secret rotation | ⬜ |

The design decisions are recorded in [`docs/adr/`](docs/adr/). Still open:
- alert routing (PagerDuty vs OpsGenie)
- a durable alert store that works with multiple replicas
- metric exporters for Kafka and ZooKeeper
- forecasting model retraining cadence (the training job exists; scheduling it is open)

### Known limitations

- **Alerts live in memory** in forecasting-service and are lost on restart. Predictions persist to `PREDICTIONS_PATH`.
- **Anomaly baselines live in memory.** After a restart (or a cold `PUT /model`), each stream needs 10 events before it scores again.
- **No forecasts for ZooKeeper.** It has no metric exporter yet, so it's disabled in `components.yaml`.
- **At-least-once everywhere.** Ingestion may re-send part of a batch, and the archiver may re-upload a batch after a failure (same key, so it overwrites rather than duplicates).
- **OAuth2 is off by default in compose** (`OAUTH2_ENABLED=false`) so the stack runs without an identity provider. Turn it on with your IdP's JWKS URL before exposing the APIs.
- **Kubernetes thresholds aren't hot-reloadable from a file.** `components.yaml` is baked into the image there; use `PUT /thresholds` (resets on restart) or mount a ConfigMap at `COMPONENTS_CONFIG`.

---

## License

MIT — see [`LICENSE`](LICENSE).
