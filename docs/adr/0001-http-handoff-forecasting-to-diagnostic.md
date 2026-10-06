# ADR-0001: Synchronous HTTP hand-off from forecasting to diagnostic

## Status
Accepted (2026-10-04)

## Context
When forecasting-service raises a `PredictiveAlert`, diagnostic-service has to produce a playbook for the at-risk component. The scaffold never defined the trigger (README "Known gaps"). The two candidates were:

1. **HTTP call.** forecasting calls `POST {DIAGNOSTIC_URL}/diagnose/{component_id}` with `{"alert_id", "anomalies"}` and stores the returned `playbook_id` on the alert.
2. **Kafka topic** (e.g. `predictive-alerts`). forecasting publishes and diagnostic consumes.

Volume is tiny: at most one OPEN alert per (component, metric), 8 components, about 3 metrics each, one evaluation per 60 s. So the hand-off carries at most a few dozen messages an hour. REQ-I requires the alert ↔ playbook link to be visible on the alert, and REQ-K expects the dashboard to follow that link.

## Decision
Use a **synchronous HTTP call** with 3 attempts and back-off (CONTRACTS.md §2). forecasting writes the `playbook_id` it receives onto the alert. If every attempt fails, the alert stays OPEN with `playbook_id: null` and the failure is logged and counted. The alert is never dropped.

## Consequences
- + No new topic, consumer group or serialisation contract. It reuses the HTTP API that operators already call on demand.
- + The link is written by the side that owns the alert, so the dashboard can join alert → playbook with no extra lookup.
- + End-to-end flow is easy to test with `respx` and needs no Kafka in Python tests (CONTRACTS.md §6).
- − **Temporal coupling.** If diagnostic is down, playbooks are missing for the alerts raised while it was down. There is no durable retry queue. Mitigation: operators can call `POST /diagnose` manually. Future: a periodic "re-diagnose alerts with null playbook_id" sweep in forecasting.
- − Retries are **not idempotent** by contract. A timed-out attempt that actually succeeded leaves an orphan playbook. Recommended: diagnostic returns the existing playbook when it already has one for the same `alert_id`.
- − The forecast loop must not block on diagnosis. Run the hand-off off the loop (background task) with an overall timeout above `LLM_TIMEOUT_SECONDS`.
- Revisit (move to a Kafka `predictive-alerts` topic) if a second consumer of alerts appears, such as paging or ticketing, or if forecasting is scaled beyond one replica.
