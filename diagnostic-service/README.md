# diagnostic-service

Event anomaly detection, per SRS v1.0 §4.4 (REQ-016 to REQ-020).

An earlier design had this service doing GraphRAG-based incident diagnosis
with LLM-generated playbooks. That is **not** SRS v1.0 scope (Appendix C:
future work); see ADR 0008.

## How it works

| Module | Requirement | What it does |
|---|---|---|
| `anomaly_scorer.py` | REQ-016, REQ-018 | `ewma-zscore` model: per stream (component/source × event type), an exponentially weighted mean/variance per numeric payload field. Score = logistic(max \|z\| − 3) in [0, 1]; rare event types for a well-known source also score high. Each event is scored *before* it updates the baseline. `with_version()` creates a swapped-in scorer, optionally warm-started with the current baselines. |
| `threshold_manager.py` | REQ-017, REQ-020 | `score > threshold` (strict). Runtime updates are validated (0–1), logged, and kept in a change history (REQ-053). |
| `audit_publisher.py` | REQ-019 | Idempotent `acks=all` Kafka producer to `anomaly-audit` (`contracts/anomaly-audit.schema.json`). Never raises into scoring; failures are counted and logged. |
| `detector.py` | — | Score → evaluate → audit pipeline, shared by HTTP and Kafka; owns the atomic model swap. |
| `event_consumer.py` | REQ-016 | `EVENT_CONSUMER_ENABLED=true`: consumes `normalized-events` (group `diagnostic-anomaly-scorer`), at-least-once. |
| `auth.py` | REQ-046 | Bearer JWT validation via JWKS when `OAUTH2_ENABLED=true`. |

Tuning: `ANOMALY_THRESHOLD` (0.8 ≈ 4.4σ), `SCORER_ALPHA` (0.05, about a
20-event memory), `SCORER_MIN_SAMPLES` (10 events of warm-up per stream).

## API

`POST /score/{event_id}`, `GET|PUT /thresholds`, `GET|PUT /model`, plus
`/health`, `/ready`, `/metrics`, `/docs`. See docs/CONTRACTS.md §2.

## Develop

```bash
pip install -r requirements-dev.txt
pytest && ruff check . && mypy app
uvicorn app.main:app --reload --port 8082
```

Tests use in-memory fakes for the Kafka producer/consumer and need no broker.
