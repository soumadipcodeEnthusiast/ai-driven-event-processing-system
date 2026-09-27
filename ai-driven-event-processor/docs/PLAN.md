# Implementation Plan

## Phase 0 — Scaffold (Current)

- [x] Repository structure created
- [x] All config / infra files written
- [x] All source stubs created with typed signatures and REQ-ID docstrings
- [x] CI pipeline configured

## Phase 1 — Ingestion Service (REQ-A, REQ-B, REQ-C)

### Goals
- Implement `EventConsumer.consume()` — Kafka listener, offset commit strategy
- Implement `NormalizationService.validate()` — JSON Schema validation
- Implement `NormalizationService.normalize()` — field mapping to `NormalizedEvent`
- Unit tests for normalization edge cases
- Integration test: publish to local Kafka, assert `NormalizedEvent` on output topic

### Acceptance Criteria
- Consumer lag stays < 500 ms at 10 000 events/second (REQ-A)
- Schema validation rejects malformed events with structured error logs (REQ-B)
- All `NormalizedEvent` fields populated correctly (REQ-C)

---

## Phase 2 — Forecasting Service (REQ-D, REQ-E, REQ-F)

### Goals
- Implement `MetricClient.poll()` — Prometheus HTTP API queries
- Implement `ForecastingEngine.forecast()` — TFT sliding-window inference
- Implement `ForecastingEngine.evaluateThreshold()` — emit `PredictiveAlert`
- Model artefact versioning via `modelVersion` field

### Acceptance Criteria
- Forecast latency < 2 s for a 60-minute window (REQ-D)
- `PredictiveAlert` emitted ≥ 10 minutes before predicted breach (REQ-E)
- Threshold configurable without redeployment (REQ-F)

---

## Phase 3 — Diagnostic Service (REQ-G, REQ-H, REQ-I)

### Goals
- Implement `DependencyGraphClient.queryDependencies()` — graph DB query
- Implement `DiagnosticEngine.retrieveContext()` — GraphRAG retrieval
- Implement `DiagnosticEngine.buildPrompt()` — prompt template assembly
- Implement `DiagnosticEngine.invokeLLM()` — LLM API call + response parsing
- Implement `PlaybookGenerator` → `IncidentPlaybook.render()`

### Acceptance Criteria
- Context retrieved within 1 s for graphs up to 500 nodes (REQ-G)
- Playbook contains at least 3 ranked remediation steps (REQ-H)
- Playbook saved as structured JSON and linked to `alertId` (REQ-I)

---

## Phase 4 — Dashboard & Observability (REQ-K)

### Goals
- Implement `ObservabilityDashboard.renderAlert()`
- Implement `ObservabilityDashboard.renderPlaybook()`
- Integrate with Prometheus metrics endpoint
- Grafana dashboards for service health

---

## Phase 5 — Production Hardening

### Goals
- HPA load testing — validate scale-out triggers
- Chaos engineering — Kafka broker failure, service restarts
- Security review — secret rotation, mTLS between services
- Documentation finalization

---

## Open Questions

1. **LLM selection** — OpenAI GPT-4o vs. self-hosted Llama 3 for playbook generation?
2. **Graph database** — Neo4j vs. AWS Neptune for dependency graph?
3. **TFT training cadence** — online learning vs. nightly batch retraining?
4. **Alert routing** — PagerDuty vs. OpsGenie integration for `PredictiveAlert`?
