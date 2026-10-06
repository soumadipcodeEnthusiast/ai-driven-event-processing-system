# Implementation Plan

Binding interfaces: [`CONTRACTS.md`](CONTRACTS.md). Architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md). Decisions: [`adr/`](adr/README.md).

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold | Done |
| 0.5 | Integration contracts, architecture, ADRs, JSON Schemas | Done |
| 1 | Ingestion (REQ-A/B/C) | **In progress** (Data Engineer) |
| 2 | Forecasting (REQ-D/E/F) | **In progress** (AI Engineer) |
| 3 | Diagnostics (REQ-G/H/I) | **In progress** (AI Engineer) |
| 4 | Dashboard & observability (REQ-K) | **In progress** (SRE + DevOps) |
| 5 | Production hardening | Not started |

Phases 1-4 run **in parallel** against the frozen contracts. File ownership is defined in CONTRACTS.md §9.

## Phase 0 — Scaffold

- [x] Repository structure created
- [x] Config and infra files written
- [x] Source stubs with typed signatures and REQ-ID docstrings
- [x] CI pipeline configured

## Phase 0.5 — Contracts & architecture

- [x] `docs/CONTRACTS.md`: topics, HTTP APIs, data models, env vars, metrics, ports, ownership
- [x] `contracts/*.schema.json`: JSON Schema (2020-12) for RawEvent, NormalizedEvent, PredictiveAlert, IncidentPlaybook, graph seed
- [x] `docs/ARCHITECTURE.md`: C4 context and containers, runtime sequences, failure modes, scaling, security
- [x] ADRs 0001-0007

## Phase 1 — Ingestion Service (REQ-A, REQ-B, REQ-C)

### Goals
- [ ] `EventConsumer.consume()`: Kafka listener, `MANUAL_IMMEDIATE` ack after the output/DLT send is confirmed
- [ ] `NormalizationService.validate()`: rules of `contracts/raw-event.schema.json` (strict and lenient)
- [ ] `NormalizationService.normalize()`: mapping to `NormalizedEvent` (UTC, defaults, `schema_version`, `ingested_at`)
- [ ] DLT routing: original bytes plus `x-error-reason` / `x-original-*` headers. Error handler with back-off, then DLT.
- [ ] Micrometer metrics `ingestion_events_total{outcome}` and `ingestion_processing_seconds`
- [ ] Unit tests for normalisation and validation edge cases
- [ ] Integration test (Testcontainers/EmbeddedKafka): valid → `normalized-events`, invalid → DLT
- [ ] `scripts/produce-events.sh`: valid and invalid sample load

### Acceptance Criteria
- Consumer lag < 500 ms at 10 000 events/s (REQ-A). Measured in Phase 5. The partition count is the scaling ceiling (ARCHITECTURE.md §8).
- Malformed events rejected with structured error logs and routed to the DLT (REQ-B)
- All `NormalizedEvent` fields populated correctly, validated against the JSON Schema (REQ-C)

---

## Phase 2 — Forecasting Service (REQ-D, REQ-E, REQ-F)

### Goals
- [ ] `MetricClient`: Prometheus `query_range` with timeouts, PromQL per component and metric from `config/components.yaml`
- [ ] Forecaster interface with a statistical implementation (default) and an optional TFT one (`FORECASTER=auto|tft|statistical`, ADR-0002). `requirements-ml.txt` holds the ML deps.
- [ ] `ForecastingEngine.evaluate_threshold()`: p90 breach, lead-time gate, severity bands
- [ ] In-memory `AlertStore` with the one-OPEN-per-(component, metric) rule and ack/resolve (ADR-0007)
- [ ] Background loop every `FORECAST_INTERVAL_SECONDS`, failures isolated per component
- [ ] HTTP hand-off to diagnostic with retries, storing `playbook_id` (ADR-0001)
- [ ] `/alerts`, `/alerts/{id}`, ack/resolve, `/thresholds` (GET/PUT), hot reload of `components.yaml`
- [ ] Metrics from CONTRACTS.md §7. `model_version` on every alert.
- [ ] `tests/` passing without ML deps or network

### Acceptance Criteria
- Forecast latency < 2 s for a 60-minute window (REQ-D)
- `PredictiveAlert` emitted ≥ 10 minutes before the predicted breach (REQ-E)
- Threshold configurable without redeployment (REQ-F)

---

## Phase 3 — Diagnostic Service (REQ-G, REQ-H, REQ-I)

### Goals
- [ ] `DependencyGraphClient`: `GraphBackend` port with networkx (default, seed `data/dependency_graph.json` containing all 8 component ids) and optional Neo4j (ADR-0003). 2-hop retrieval.
- [ ] `DiagnosticEngine.retrieve_context()` / `build_prompt()` / `invoke_llm()`: pluggable provider with timeout (ADR-0004)
- [ ] Rule-based fallback playbook generator
- [ ] `PlaybookGenerator` → `IncidentPlaybook` (≥ 3 ranked steps) and `render()` to markdown
- [ ] File-based `PlaybookStore` with atomic writes and id validation (ADR-0005)
- [ ] `/diagnose/{component_id}`, `/playbook/{id}` (JSON and `?format=markdown`), `/playbooks`
- [ ] Metrics from CONTRACTS.md §7
- [ ] `tests/` passing with `LLM_PROVIDER=none` and a mocked LLM

### Acceptance Criteria
- Context retrieved within 1 s for graphs of up to 500 nodes (REQ-G)
- Playbook contains at least 3 ranked remediation steps, even when the LLM fails (REQ-H)
- Playbook saved as structured JSON and linked to `alert_id` (REQ-I)

---

## Phase 4 — Dashboard & Observability (REQ-K)

Decision: Grafana + Infinity data source (ADR-0006). The `ObservabilityDashboard.renderAlert()/renderPlaybook()` operations in `dashboard/README.md` are realised as Grafana panels (alerts table → playbook data link), not as code.

### Goals
- [ ] Grafana provisioning (`infra/grafana/provisioning/`) with Prometheus and Infinity data sources
- [ ] AIOps dashboard: open-alerts table (refresh ≤ 5 s), playbook panel linked by `playbook_id`
- [ ] Service-health dashboard: ingestion throughput/lag/DLT rate, forecast latency, diagnosis latency, LLM fallback rate
- [ ] Prometheus rules in `infra/prometheus/rules/`, SLOs in `docs/SLO.md`, runbooks in `docs/runbooks/`
- [ ] Compose: Grafana, optional `neo4j` and `llm` (Ollama) profiles, healthchecks fixed. Makefile targets.

### Acceptance Criteria
- New alerts shown within 5 s of emission. Each alert links to its playbook (REQ-K).

---

## Phase 5 — Production Hardening

### Goals
- [ ] Load test REQ-A with `scripts/produce-events.sh`. Raise `raw-events` partitions, then scale ingestion on consumer lag (KEDA / lag exporter) instead of CPU.
- [ ] Replace the in-memory alert store and the file playbook store with durable shared stores, then lift the single-replica limits (ADR-0005, ADR-0007)
- [ ] Chaos tests: Kafka broker loss, Prometheus outage, LLM outage, diagnostic outage (ARCHITECTURE.md §7)
- [ ] Security: mTLS between services, auth on mutating endpoints, secret rotation, Grafana auth
- [ ] TFT: train, backtest against the statistical baseline, decide on enabling it

---

## Decisions (formerly open questions)

| Question | Resolution |
|---|---|
| LLM selection (GPT-4o vs. Llama 3) | **Resolved by ADR-0004.** Provider is configuration (`LLM_PROVIDER=ollama\|anthropic\|openai\|none`). Ollama is the local default, and a rule-based fallback guarantees a playbook. Picking the production provider is now a per-environment configuration choice (data-egress constraints, ARCHITECTURE.md §9). |
| Graph database (Neo4j vs. Neptune) | **Resolved by ADR-0003.** networkx seed by default, Neo4j optional behind a port. Neptune is deferred and can be added as a further adapter. |
| Dashboard technology | **Resolved by ADR-0006.** Grafana + Infinity. |
| Forecasting → diagnostic trigger | **Resolved by ADR-0001.** Synchronous HTTP hand-off with retries. |
| Alert/playbook persistence | **Resolved (interim) by ADR-0005 / ADR-0007.** File store for playbooks, in-memory store for alerts. |

## Open Questions

1. **TFT training cadence.** Online learning vs. nightly batch retraining. This is blocked on collecting enough history, and TFT is optional until then (ADR-0002).
2. **Alert routing.** PagerDuty vs. OpsGenie for `PredictiveAlert`. Not in scope today. If added, it is the trigger to move alerts onto a Kafka topic (ADR-0001 revisit condition).
3. **Durable alert store and multi-replica forecasting.** SQLite → Postgres vs. a compacted Kafka topic, plus leader election for the forecast loop (ADR-0007).
4. **Metrics for infrastructure components** (`kafka`, `zookeeper`, `graph-db`, `llm`). Which exporters to add, or whether to forecast on `up`/lag only (ARCHITECTURE.md §10.8).
