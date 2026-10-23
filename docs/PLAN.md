# Implementation Plan

Binding interfaces: [`CONTRACTS.md`](CONTRACTS.md). Architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md). Decisions: [`adr/`](adr/README.md).

REQ-IDs below follow the finalized SRS (`AI_Event_Processing_SRS_V2.pdf`,
Version 1.0), not the earlier scaffold's REQ-A..REQ-K letters. Terraform is
core work (Phase 1.5), not a Phase 5 stretch goal — see docs/adr/0008 and
the SRS's §6 Cloud requirements.

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold | Done |
| 0.5 | Integration contracts, architecture, ADRs, JSON Schemas | Done |
| 1 | Ingestion (REQ-001 to REQ-005, REQ-008) | Done |
| 1.5 | Terraform / Infrastructure-as-Code (REQ-044) | Done (`validate`; `apply` needs the AWS account) |
| 2 | Forecasting / Predictive Analytics (REQ-021 to REQ-025) | Done |
| 3 | Anomaly Detection (REQ-016 to REQ-020) | Done |
| 4 | Dashboard & observability (REQ-034) | Done |
| 4.5 | Security: OAuth 2.0 + TLS (REQ-046, REQ-047), S3 archival (REQ-028 to REQ-030) | Done |
| 5 | Production hardening | Not started |

File ownership is defined in CONTRACTS.md §9.

## Phase 0 — Scaffold

- [x] Repository structure created
- [x] Config and infra files written
- [x] Source stubs with typed signatures and REQ-ID docstrings
- [x] CI pipeline configured (Jenkins — see `Jenkinsfile`; the earlier GitHub Actions workflow is removed)

## Phase 0.5 — Contracts & architecture

- [x] `docs/CONTRACTS.md`: topics, HTTP APIs, data models, env vars, metrics, ports, ownership
- [x] `contracts/*.schema.json`: JSON Schema (2020-12) for RawEvent, NormalizedEvent, PredictiveAlert (IncidentPlaybook and the graph seed schemas were written here too, then removed per docs/adr/0008)
- [x] `docs/ARCHITECTURE.md`: C4 context and containers, runtime sequences, failure modes, scaling, security
- [x] ADRs 0001-0008

## Phase 1 — Ingestion Service (REQ-001 to REQ-005, REQ-008)

### Goals
- [x] `EventConsumer.consume()`: Kafka listener, `MANUAL_IMMEDIATE` ack after the output/DLT send is confirmed
- [x] `NormalizationService.validate()`: rules of `contracts/raw-event.schema.json` (strict and lenient)
- [x] `NormalizationService.normalize()`: mapping to `NormalizedEvent` (UTC, defaults, `schema_version`, `ingested_at`)
- [x] DLT routing: original bytes plus `x-error-reason` / `x-original-*` headers. Error handler with back-off, then DLT.
- [x] Micrometer metrics `ingestion_events_total{outcome}` and `ingestion_processing_seconds`
- [x] Unit tests for normalisation and validation edge cases
- [x] Integration test (Testcontainers/EmbeddedKafka): valid → `normalized-events`, invalid → DLT
- [x] `scripts/produce-events.sh`: valid and invalid sample load
- [x] OAuth 2.0 resource-server wiring (REQ-046) and an env-driven `server.ssl` TLS 1.2+ block (REQ-047) — see Phase 4.5

### Acceptance Criteria
- Consumer lag < 500 ms at 10 000 events/s (REQ-001, REQ-004). Measured in Phase 5. The partition count is the scaling ceiling (ARCHITECTURE.md §8).
- Malformed events rejected with structured error logs and routed to the DLT (REQ-005)
- All `NormalizedEvent` fields populated correctly, validated against the JSON Schema (REQ-008 — this is a Stream Preprocessing requirement, §4.2, not Event Ingestion §4.1; the ingestion-service implements both features in one deployable)

---

## Phase 1.5 — Terraform / Infrastructure-as-Code (REQ-044)

Core work per the finalized SRS §6 ("Cloud requirements include ... Infrastructure-as-Code provisioning via Terraform"), not a Phase 5 stretch goal.

### Goals
- [x] `infra/terraform/main.tf`, `variables.tf`, `outputs.tf`: AWS provider (EKS-style managed Kubernetes cluster, node group), active
- [x] `s3.tf`: archive bucket (SSE, versioning, public-access block, TLS-only policy, REQ-029 lifecycle) or bring-your-own (`create_archive_bucket = false`); IRSA role for the archiver
- [x] `ecr.tf`: one repository per image, for the Jenkins push
- [x] Azure provider block present but commented out (NFR portability, SRS §5)
- [x] `terraform validate` passing without cloud credentials (`make tf-validate`, also a Jenkins stage)

### Acceptance Criteria
- `terraform validate` succeeds locally with no credentials. `plan`/`apply` run against the project's AWS account (`terraform.tfvars.example`).

---

## Phase 2 — Forecasting Service / Predictive Analytics (REQ-021 to REQ-025)

### Goals
- [x] `MetricClient`: Prometheus `query_range` with timeouts, PromQL per component and metric from `config/components.yaml`
- [x] Forecaster interface with a statistical implementation (default) and an optional ML one (`FORECASTER=auto|ml|statistical`, ADR-0002). `requirements-ml.txt` holds tensorflow/scikit-learn.
- [x] `predictive_analytics.py`: `ar-ridge` model — `train()` (scikit-learn Ridge, numpy fallback), `predict()`, `evaluate_confidence()`, JSON `save()`/`load()` (REQ-021, REQ-025); `PredictionStore` (REQ-024)
- [x] `MLForecaster` serves the trained model (numpy-only inference); `training/train_forecast_model.py` trains from Prometheus or CSV and backtests against the statistical baseline
- [x] `GET /predictions`, `GET /predictions/history` (REQ-023, REQ-024); `confidence`/`low_confidence` per forecast (REQ-025)
- [x] `ForecastingEngine.evaluate_threshold()`: p90 breach, lead-time gate, severity bands
- [x] In-memory `AlertStore` with the one-OPEN-per-(component, metric) rule and ack/resolve (ADR-0007)
- [x] Background loop every `FORECAST_INTERVAL_SECONDS`, failures isolated per component
- [x] `/alerts`, `/alerts/{id}`, ack/resolve, `/thresholds` (GET/PUT), hot reload of `components.yaml`
- [x] Metrics from CONTRACTS.md §7. `model_version` on every alert.
- [x] `tests/` passing without ML deps or network

### Acceptance Criteria
- Forecast latency < 2 s for a 60-minute window (REQ-021)
- `PredictiveAlert` emitted ≥ 10 minutes before the predicted breach (REQ-025 — closest SRS fit; §4.5 has no requirement phrased as lead-time alerting specifically)
- Threshold configurable without redeployment (REQ-052 — Administration, not §4.5; §4.5 has no threshold-configuration requirement)

Note: the HTTP hand-off to diagnostic-service (ADR-0001) is removed — see Phase 3.

---

## Phase 3 — Diagnostic Service / Anomaly Detection (REQ-016 to REQ-020)

Rescoped per docs/adr/0008: the earlier GraphRAG/Neo4j dependency-graph diagnosis and LLM/rule-based playbook generation are not in SRS v1.0 (Appendix C: future work). This service is anomaly detection.

### Goals
- [x] `AnomalyScorer.score_event()`: `ewma-zscore` score per event (REQ-016); `with_version()` + `PUT /model` for no-downtime swaps (REQ-018)
- [x] `ThresholdManager.evaluate()` / `set_threshold()`: validated, audited runtime threshold (REQ-017, REQ-020)
- [x] `AuditPublisher.publish_anomaly()`: idempotent producer to `anomaly-audit` (REQ-019), `contracts/anomaly-audit.schema.json`
- [x] `normalized-events` consumer so every processed event is scored (REQ-016)
- [x] `/score/{event_id}`, `/thresholds` (GET/PUT), `/model` (GET/PUT)
- [x] Metrics: `anomaly_score`, `anomalies_detected_total`, `anomaly_audit_publish_failures_total` (replacing the retired `graph_context_seconds`/`llm_request_seconds`/`playbooks_stored_total`)
- [x] `tests/test_anomaly_detection.py`: scorer, thresholds, audit (schema-validated), consumer, API, OAuth2

### Acceptance Criteria
- Anomaly score computed for each event (REQ-016), alert triggered above threshold (REQ-017), anomalies recorded to the audit topic (REQ-019), threshold and model changeable at runtime (REQ-018, REQ-020) — all covered by tests.

---

## Phase 4 — Dashboard & Observability (REQ-034)

Decision: Grafana + Infinity data source (ADR-0006).

### Goals
- [x] Grafana provisioning (`infra/grafana/provisioning/`) with Prometheus and Infinity data sources
- [x] Service-health dashboard: ingestion throughput/lag/DLT rate, forecast latency, anomaly-detection rate
- [x] Prometheus rules in `infra/prometheus/rules/`, SLOs in `docs/SLO.md`, runbooks in `docs/runbooks/`
- [x] Compose: Grafana, MinIO (REQ-028 local S3 stand-in). Makefile targets.
- [x] Dashboard panels, Prometheus rules (promtool-tested) and runbooks match the anomaly-detection and archiver metrics; `EventArchiveUploadFailing` / `EventArchiveStalled` added

### Acceptance Criteria
- Dashboard shows real-time throughput and error rates (REQ-034).

---

## Phase 4.5 — Security: OAuth 2.0 + TLS, S3 Archival

### Goals
- [x] `ingestion-service`: `SecurityConfig.java` — JWKS / issuer-discovery `JwtDecoder` with issuer + audience validation, deny-by-default when unconfigured (REQ-046)
- [x] `application.yml`: `server.ssl` driven by `SSL_ENABLED`, TLS 1.2/1.3 only (REQ-047); ALB Ingress with a TLS 1.2+ policy in k8s; S3 bucket denies non-TLS
- [x] FastAPI services: `app/auth.py` validates bearer JWTs (JWKS, `exp`, `iss`, `aud`) when `OAUTH2_ENABLED=true` (REQ-046)
- [x] `storage/`: `ArchiveClient` (boto3) + `event-archiver` Kafka → S3 service (REQ-028, REQ-030, docs/adr/0009); retention via lifecycle (REQ-029); MinIO locally, real S3 via `docker-compose.aws.yml` or IRSA on EKS

### Acceptance Criteria
- Invalid/missing/expired/forged tokens → 401 (tests in all three services). Archived batches land in S3 and offsets are committed only afterwards (storage tests; verified end-to-end against MinIO).

---

## Phase 5 — Production Hardening

### Goals
- [ ] Load test REQ-001/REQ-004 with `scripts/produce-events.sh`. Raise `raw-events` partitions, then scale ingestion on consumer lag (KEDA / lag exporter) instead of CPU.
- [ ] Replace the in-memory alert store with a durable shared store, then lift forecasting-service's single-replica limit (ADR-0007). diagnostic-service is already stateless and multi-replica (docs/adr/0008).
- [ ] Chaos tests: Kafka broker loss, Prometheus outage, diagnostic outage (ARCHITECTURE.md §7)
- [ ] Security: mTLS between services, secret rotation, Grafana SSO (API auth is done — REQ-046)
- [ ] Schedule forecasting-model retraining (the training job + backtest gate exist; cadence is Open Question 1)

---

## Decisions (formerly open questions)

| Question | Resolution |
|---|---|
| GraphRAG/LLM diagnosis scope | **Resolved by docs/adr/0008.** Not in SRS v1.0 (Appendix C: future work). diagnostic-service is anomaly detection (REQ-016 to REQ-020) only; ADR-0001, 0003, 0004, 0005 are superseded. |
| Graph database (Neo4j vs. Neptune) | **Moot — superseded by docs/adr/0008.** No graph backend remains in scope. |
| Dashboard technology | **Resolved by ADR-0006.** Grafana + Infinity. |
| Forecasting → diagnostic trigger | **Moot — superseded by docs/adr/0008.** No hand-off: Predictive Analytics (§4.5) and Anomaly Detection (§4.4) are independent SRS features. |
| Alert persistence | **Resolved (interim) by ADR-0007.** In-memory store for alerts, single forecasting-service replica. |
| ML framework | **Resolved by SRS v1.0 §2.5/§3.3.** TensorFlow + Scikit-learn, not PyTorch/pytorch-forecasting. |
| CI/CD | **Resolved by SRS v1.0 §6.** Jenkins, not GitHub Actions. |
| IaC | **Resolved by SRS v1.0 §6.** Terraform is core (Phase 1.5), AWS active / Azure commented out for portability. |

## Open Questions

1. **ML model training cadence.** Online learning vs. nightly batch retraining, once `predictive_analytics.py`/`MLForecaster` are implemented (ADR-0002).
2. **Alert routing.** PagerDuty vs. OpsGenie for `PredictiveAlert`. Not in scope today.
3. **Durable alert store and multi-replica forecasting.** SQLite → Postgres vs. a compacted Kafka topic, plus leader election for the forecast loop (ADR-0007).
4. **Metrics for infrastructure components** (`kafka`, `zookeeper`). Which exporters to add, or whether to forecast on `up`/lag only (ARCHITECTURE.md §10.8). (`graph-db`/`llm` entries are removed — those components no longer exist.)
5. ~~Dashboards/Prometheus rules/runbooks referencing retired diagnostic-service metrics~~ — resolved: all updated to the anomaly-detection and archiver metrics.
