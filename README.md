# AI-Driven Event Processing Architecture

An event-processing platform that **ingests and normalises high-volume events through Kafka**, and runs an **AIOps layer alongside it that predicts component failures before they happen and drafts the remediation playbook automatically**.

The system has two paths:

- **Synchronous data path.** A Java / Spring Boot service consumes raw events from Kafka, validates them against a schema, normalises them into a canonical `NormalizedEvent`, and publishes them downstream. Malformed events go to a dead-letter topic.
- **Asynchronous AIOps path.** Two Python services watch the platform's own components through Prometheus metrics:
  1. **Forecasting** projects CPU, memory, error-rate and consumer-lag series ahead. It uses a damped-trend statistical forecaster by default, or a **Temporal Fusion Transformer (TFT)** when a trained model is supplied. When the 90th-percentile forecast is expected to cross a threshold at least 10 minutes out, it raises a `PredictiveAlert`.
  2. **Diagnostics** takes the at-risk component, pulls its **2-hop dependency subgraph**, gives that context to an **LLM (GraphRAG)**, and turns the answer into a ranked, stored `IncidentPlaybook`. If no LLM is configured, or the LLM fails, a rule-based playbook is built from the graph instead.

A **Grafana dashboard** shows live alerts and links each one to its playbook.

> **Status:** Phases 1–4 are implemented. Ingestion, forecasting, diagnostics, the Grafana dashboard, alerting and SLOs all work, and all three test suites pass (**58 Java, 45 + 44 Python**). Phase 5 (load and chaos testing, mTLS) remains. See [Project status](#project-status).

---

## How it works

```
                 Producers (apps, devices, logs)
                              │  JSON events
                              ▼
                    ┌──────────────────┐
                    │ Kafka: raw-events│  (6 partitions)
                    └────────┬─────────┘
                             ▼
 ┌────────────────────────────────────────────────┐
 │ ingestion-service  (Java 17 / Spring Boot 3)   │   SYNCHRONOUS PATH
 │  parse → validate (REQ-B) → normalize (REQ-C)  │
 │  batch listener, manual ack after send (REQ-A) │
 └───────┬──────────────────────────────┬─────────┘
         │ valid                        │ invalid / retries exhausted
         ▼                              ▼
 ┌─────────────────────────┐   ┌────────────────────────┐
 │ Kafka: normalized-events│   │ Kafka: raw-events.DLT  │
 └─────────────────────────┘   └────────────────────────┘

 ───────────── metrics: every service ──► Prometheus (+ alert rules) ─────────────

 ┌────────────────────────────────────────────────┐
 │ forecasting-service  (Python / FastAPI)        │   ASYNC AIOps PATH
 │  every 60 s, per component in components.yaml: │
 │  query_range → forecast (p50 + p90) → thresholds│
 │  → PredictiveAlert (REQ-D/E/F)                 │
 └───────────────────────┬────────────────────────┘
                         │ POST /diagnose/{component} {alert_id, anomalies}
                         ▼
 ┌────────────────────────────────────────────────┐
 │ diagnostic-service  (Python / FastAPI)         │
 │  2-hop subgraph (networkx | Neo4j)   (REQ-G)   │
 │  Jinja2 prompt → LLM | rule fallback (REQ-H)   │
 │  IncidentPlaybook → JSON file store  (REQ-I)   │
 └───────────────────────┬────────────────────────┘
                         ▼
             ┌──────────────────────────────┐
             │ Grafana :3000 (REQ-K)        │
             │ alerts (5 s refresh) → playbook│
             └──────────────────────────────┘
```

### End-to-end flow

1. A producer publishes a JSON event to `raw-events`. `make produce` generates sample traffic.
2. `ingestion-service` validates the event against [`contracts/raw-event.schema.json`](contracts/raw-event.schema.json). The required fields are `event_id`, `timestamp` (ISO-8601 with offset) and `source`. Strict mode rejects unknown keys.
   - **Valid:** the event is published to `normalized-events`, keyed by `event_id`. It carries a UTC timestamp, defaults, `schema_version` and `ingested_at`.
   - **Invalid:** the original bytes go to `raw-events.DLT`, with `x-error-reason` and `x-original-*` headers.
   - **Unexpected error:** it is retried 3× with back-off, then dead-lettered.
   - Offsets are committed only after the broker acks the sends.
3. Prometheus scrapes every service every 15 s.
4. `forecasting-service` runs a cycle every `FORECAST_INTERVAL_SECONDS`.
   - For each enabled component and metric, it pulls the last 60 min and forecasts the next 60 min.
   - If the p90 crosses the threshold at least `ALERT_MIN_LEAD_MINUTES` ahead, it opens a `PredictiveAlert`, with severity based on the overshoot.
   - Only one open or acknowledged alert is allowed per (component, metric).
   - Thresholds hot-reload from `config/components.yaml` or can be changed with `PUT /thresholds/{component}` (REQ-F).
5. The new alert is handed to `diagnostic-service`, which:
   - loads the component's 2-hop neighbourhood and node metadata;
   - prompts the configured LLM for strict JSON and parses it into at least 3 ranked steps;
   - otherwise builds a rule-based playbook;
   - stores the playbook as JSON under `PLAYBOOK_STORE_DIR`.
   This step is idempotent per `alert_id`. The alert then records its `playbook_id`.
6. The Grafana **AIOps Overview** dashboard refreshes every 5 s. Clicking an alert opens its playbook (`GET /playbook/{id}?format=markdown`).

Detailed sequence diagrams and failure modes are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The binding interface spec is [`docs/CONTRACTS.md`](docs/CONTRACTS.md).

---

## Services

| Service | Stack | Port | Responsibility |
|---|---|---|---|
| `ingestion-service` | Java 17, Spring Boot 3.2, Spring Kafka, Micrometer | 8080 | Kafka consumer, schema validation, normalisation, DLT routing |
| `forecasting-service` | Python 3.11, FastAPI, numpy/pandas, optional PyTorch TFT | 8081 | Prometheus polling, forecasting, threshold evaluation, alert store |
| `diagnostic-service` | Python 3.11, FastAPI, networkx / Neo4j, Jinja2 | 8082 | Dependency graph, LLM / rule-based diagnosis, playbook store |
| Grafana | Grafana 11 + Infinity plugin | 3000 | REQ-K dashboard, service health, SLOs |
| Prometheus | Prometheus 2.52 | 9090 | Scraping, recording and alert rules |

### HTTP APIs

| Service | Method & path | Purpose |
|---|---|---|
| ingestion | `GET /actuator/health/{liveness,readiness}`, `/actuator/prometheus` | Probes, metrics |
| forecasting | `POST /forecast` `{"component_id": "...", "forecast_window_minutes": 60}` | Run one forecast cycle now |
| forecasting | `GET /alerts?status=&component_id=&limit=` · `GET /alerts/{id}` | List or get alerts |
| forecasting | `GET /alerts/summary` | Live counts and minutes to the soonest predicted breach |
| forecasting | `POST /alerts/{id}/ack` · `POST /alerts/{id}/resolve` | Alert lifecycle |
| forecasting | `GET /thresholds` · `PUT /thresholds/{component_id}` | Read or override thresholds at runtime |
| diagnostic | `POST /diagnose/{component_id}` `{"alert_id": ..., "anomalies": [...]}` | Run a diagnosis → `{playbook_id, summary, ...}`; rate-limited (429 + `Retry-After`) |
| diagnostic | `GET /playbook/{id}` (`?format=markdown`) · `GET /playbooks?alert_id=&component_id=` | Retrieve playbooks |
| both Python | `GET /health`, `/ready`, `/metrics`, `/docs` | Ops + OpenAPI UI |

### Kafka topics

| Topic | Partitions | Producer → Consumer |
|---|---|---|
| `raw-events` | 6 | external producers → `ingestion-service` |
| `normalized-events` | 6 | `ingestion-service` → downstream consumers |
| `raw-events.DLT` | 1 | `ingestion-service` → manual inspection / replay |

### Data models

These are JSON Schemas in [`contracts/`](contracts/): `RawEvent`, `NormalizedEvent`, `PredictiveAlert`, `IncidentPlaybook` and the dependency-graph seed. The Python tests validate the services' output against them.

---

## Requirements

| ID | Requirement | Acceptance criterion | Where it's implemented / measured |
|---|---|---|---|
| REQ-A | At-least-once Kafka ingestion, manual commit | Consumer lag < 500 ms at 10 000 events/s | `EventConsumer`, `KafkaConfig` · lag alert + SLO |
| REQ-B | Validate before processing | Malformed events rejected with structured logs, sent to DLT | `NormalizationService.validate`, `DeadLetters` |
| REQ-C | Normalise to `NormalizedEvent` | All fields populated, UTC, schema version | `NormalizationService.normalize` |
| REQ-D | Poll Prometheus and forecast | Forecast latency < 2 s per 60-min window | `metric_client.py`, `forecasters.py` · `forecast_latency_seconds` SLO |
| REQ-E | Alert ahead of breach | Alert emitted ≥ 10 min before predicted breach | `forecasting_engine.py` |
| REQ-F | Runtime-configurable thresholds | No redeploy needed | `components.py` hot reload, `PUT /thresholds` |
| REQ-G | 2-hop dependency context | < 1 s for graphs ≤ 500 nodes | `dependency_graph_client.py` · `graph_context_seconds` |
| REQ-H | Prompt + LLM → ranked steps | ≥ 3 ranked remediation steps | `diagnostic_engine.py`, `llm_client.py`, `fallback.py` |
| REQ-I | Persist playbooks, link to alert | Stored as JSON, linked to `alert_id` | `playbook_generator.py` |
| REQ-K | Observability dashboard | Alerts visible within 5 s, click through to playbook | `infra/grafana/dashboards/aiops-overview.json` |

There is no REQ-J. SLOs and error budgets for each requirement are in [`docs/SLO.md`](docs/SLO.md).

---

## Quick start

**Prerequisites:** Docker with Compose v2. For running tests outside containers you also need JDK 17 and Python 3.11.

```bash
git clone <repo-url> && cd ai-driven-system

make up                      # build + start everything, wait until healthy
make produce ARGS="-n 2000 -i 0.1 -r 200"   # 2000 events, 10 % invalid, 200/s
make logs SERVICE=ingestion-service
make down                    # stop and delete volumes
```

`./scripts/setup-local.sh` does the same as `make up`, with flags `--profile llm|neo4j`, `--no-build`, `--logs` and `--down`.

| URL | What |
|---|---|
| http://localhost:3000 | Grafana, opens on the **AIOps Overview** (alerts → playbooks) |
| http://localhost:9090 | Prometheus (targets, rules, alerts) |
| http://localhost:8081/docs | forecasting-service OpenAPI |
| http://localhost:8082/docs | diagnostic-service OpenAPI |
| http://localhost:8080/actuator/health | ingestion-service |

Try the AIOps path by hand:

```bash
curl -s -XPOST localhost:8082/diagnose/kafka -H 'content-type: application/json' -d '{}' | jq
curl -s localhost:8082/playbooks | jq '.[0].steps'
curl -s -XPUT localhost:8081/thresholds/ingestion-service -H 'content-type: application/json' \
     -d '{"cpu_utilisation": 0.01}'          # force an alert on the next cycle
curl -s localhost:8081/alerts | jq
```

### Using a real LLM

By default (`LLM_PROVIDER=none`), playbooks are rule-based. To use an LLM:

```bash
# local model through Ollama (pulls llama3.1 on first start)
LLM_PROVIDER=ollama make up PROFILES="llm"

# hosted model
LLM_PROVIDER=anthropic LLM_API_KEY=sk-... make up   # default model claude-sonnet-5-5
```

Any LLM failure or malformed answer falls back to the rule-based playbook. You can tell which one produced a playbook from its `generated_by` field.

### Optional components

- **Neo4j graph backend:** `GRAPH_BACKEND=neo4j make up PROFILES="neo4j"`. The empty database is seeded from `diagnostic-service/data/dependency_graph.json`.
- **TFT forecaster:**
  - Build with `make build INSTALL_ML=true`.
  - Train with `forecasting-service/training/train_tft.py`.
  - Mount the checkpoint at `TFT_MODEL_PATH`.
  - With `FORECASTER=auto`, the TFT is used when it is available.

---

## Development

```bash
make test        # ingestion (Maven verify incl. embedded-Kafka IT) + both pytest suites
make lint        # ruff + mypy + shellcheck + compose validation
make k8s-validate
```

Python services, from inside the service directory:

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest && ruff check . && mypy app
uvicorn app.main:app --reload --port 8081   # 8082 for diagnostic
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
| `ALERT_MIN_LEAD_MINUTES` | forecasting | `10` | Minimum lead time for an alert |
| `COMPONENTS_CONFIG` | forecasting | `config/components.yaml` | PromQL + thresholds per component, hot-reloaded |
| `FORECASTER` | forecasting | `auto` | `auto` / `tft` / `statistical` |
| `GRAPH_BACKEND` | diagnostic | `networkx` | `networkx` (seed JSON) or `neo4j` |
| `LLM_PROVIDER` | diagnostic | `none` | `none` / `ollama` / `anthropic` / `openai` |
| `LLM_API_URL`, `LLM_MODEL`, `LLM_API_KEY` | diagnostic | per-provider defaults | LLM endpoint |
| `PLAYBOOK_STORE_DIR` | diagnostic | `/data/playbooks` | Playbook JSON store (volume) |
| `PLAYBOOK_MAX_COUNT` / `PLAYBOOK_RETENTION_DAYS` | diagnostic | `1000` / `30` | Retention; oldest playbooks are deleted first (0 disables) |
| `DIAGNOSE_RATE_LIMIT_PER_MINUTE` | diagnostic | `60` | `/diagnose` token bucket (0 disables) |

---

## Observability

- **Dashboards** (`infra/grafana/dashboards/`), described in [`dashboard/README.md`](dashboard/README.md):
  - `aiops-overview` (REQ-K)
  - `service-health`
  - `slo`
- **Alert rules** (`infra/prometheus/rules/`):
  - 16 service alerts, plus multi-window burn-rate SLO alerts.
  - Each alert has a `runbook_url` that points into [`docs/runbooks/`](docs/runbooks/).
  - The rules are unit-tested with `promtool test rules`.
- **SLOs:** [`docs/SLO.md`](docs/SLO.md).

## Deployment & CI

- **Kubernetes** (`infra/k8s/`, apply with `kubectl apply -k infra`):
  - Namespace with restricted Pod Security.
  - Deployments, Services and ConfigMaps for all three services, plus a PVC for playbooks.
  - An in-namespace Prometheus that discovers annotated pods and loads the same alert rules as compose. Forecasting reads from it.
  - Ingestion autoscales from 2 to 6 pods; it can't usefully scale past the partition count.
  - Forecasting and diagnostic are pinned to a single replica, because the alert store is in memory and the playbook store is file-based (see ADR-0005 and ADR-0007).
- **CI** (`.github/workflows/ci.yml`) runs:
  - `./mvnw verify`
  - ruff, mypy and pytest for both Python services
  - hadolint and shellcheck, and validates the compose file
  - Docker builds of all images
  - kubeconform on raw and kustomized manifests

---

## Repository layout

```
.
├── ingestion-service/      Spring Boot Kafka consumer (REQ-A/B/C) + tests
├── forecasting-service/    FastAPI forecasting + alerts (REQ-D/E/F), config/, training/, tests/
├── diagnostic-service/     FastAPI GraphRAG playbooks (REQ-G/H/I), data/, templates, tests/
├── contracts/              JSON Schemas for every message / model
├── dashboard/              REQ-K dashboard documentation
├── infra/                  docker-compose, Prometheus (+ rules), Grafana, k8s
├── scripts/                setup-local.sh, produce-events.sh
├── docs/                   ARCHITECTURE, CONTRACTS, SLO, PLAN, STRUCTURE, adr/, runbooks/
├── Makefile
└── .github/workflows/      CI
```

See [`docs/STRUCTURE.md`](docs/STRUCTURE.md) for the full tree.

---

## Project status

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold | ✅ |
| 1 | Ingestion (REQ-A/B/C) | ✅ |
| 2 | Forecasting (REQ-D/E/F) | ✅ statistical forecaster; TFT path implemented, needs a trained model |
| 3 | Diagnostics (REQ-G/H/I) | ✅ |
| 4 | Dashboard + observability (REQ-K) | ✅ |
| 5 | Hardening: load test for REQ-A at 10k ev/s, chaos tests, mTLS, secret rotation | ⬜ |

The design decisions are recorded in [`docs/adr/`](docs/adr/). Still open:
- TFT retraining cadence
- alert routing (PagerDuty vs OpsGenie)
- a durable alert store that works with multiple replicas
- metric exporters for Kafka, ZooKeeper, Neo4j and the LLM

### Known limitations

- **Alerts live in memory** in forecasting-service and are lost on restart. Playbooks persist.
- **No forecasts for ZooKeeper, the graph DB or the LLM.** They have no metric exporters yet, so they're disabled in `components.yaml`. They still appear in the dependency graph for diagnosis.
- **Ingestion may produce duplicates.** It processes in batches with at-least-once delivery, so a failed send can re-send earlier records in the batch.
- **No authentication.** The services are meant for an internal network; Grafana allows anonymous viewing locally. `/diagnose` is rate-limited and playbook storage is bounded by retention, which limits the cost of abuse but doesn't replace auth.
- **Kubernetes thresholds aren't hot-reloadable from a file.** `components.yaml` is baked into the image there; use `PUT /thresholds` (resets on restart) or mount a ConfigMap at `COMPONENTS_CONFIG`.

---

## License

MIT — see [`LICENSE`](LICENSE).
