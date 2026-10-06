# ADR-0007: In-memory alert store in forecasting-service

## Status
Accepted (2026-10-04). Interim; revisit before any multi-replica or production deployment.

## Context
forecasting-service must hold alert state for `GET /alerts`, ack/resolve transitions, the "one OPEN alert per (component, metric)" rule, and the `playbook_id` link. A durable store (DB, Kafka compacted topic) adds infrastructure that the current scope does not need.

## Decision
Keep alerts in a process-local, lock-protected store (dict keyed by `alert_id` plus an index on `(component_id, metric_name)` for OPEN alerts), behind an `AlertStore` interface. Bound it: keep all non-resolved alerts plus the most recent N resolved ones.

## Consequences
- + Simple, fast, and deterministic in tests.
- − **Alerts are lost on restart.** On restart, an at-risk metric re-alerts on the next forecast cycle (within `FORECAST_INTERVAL_SECONDS`) and gets a *new* `alert_id` and a new playbook. Old playbooks keep pointing at the old `alert_id`.
- − **Single replica only.** With N replicas each runs the background loop, creating duplicate alerts, and `GET /alerts` answers differ per pod. The scaffolded HPA (1-5) for forecasting must be capped at 1 replica (or the HPA removed) while this ADR stands.
- − Ack/resolve state is not auditable.
- Future path: (1) SQLite on a volume behind `AlertStore`; (2) Postgres, or a Kafka `predictive-alerts` compacted topic (which also supersedes ADR-0001), plus leader election for the forecast loop, before scaling out.
