# Contracts (JSON Schema, draft 2020-12)

Machine-readable versions of the data contracts in [`docs/CONTRACTS.md`](../docs/CONTRACTS.md).
`CONTRACTS.md` is the source of truth. If the two ever disagree, `CONTRACTS.md` wins and the schema is the bug.
These schemas add no new fields. They spell out details the prose leaves implicit (patterns, nullability, required keys).

| Schema | Describes | Produced by | Consumed by |
|---|---|---|---|
| [`raw-event.schema.json`](raw-event.schema.json) | Kafka `raw-events` value (§1.1) | external producers, `scripts/produce-events.sh` | ingestion-service |
| [`normalized-event.schema.json`](normalized-event.schema.json) | Kafka `normalized-events` value (§1.2) | ingestion-service | diagnostic-service (anomaly scoring), event-archiver |
| [`anomaly-audit.schema.json`](anomaly-audit.schema.json) | Kafka `anomaly-audit` value (§1.3) | diagnostic-service | audit consumers / SIEM |
| [`predictive-alert.schema.json`](predictive-alert.schema.json) | `PredictiveAlert` (§3), items of `GET /alerts`, `GET /alerts/{id}`, `POST /forecast` → `alert` | forecasting-service | Grafana |

`incident-playbook.schema.json` and `dependency-graph-seed.schema.json` are
removed along with the GraphRAG/LLM/playbook design — see
[`docs/adr/0008`](../docs/adr/0008-rescope-diagnostic-service-to-anomaly-detection.md).

## Interpretation notes (consistent with CONTRACTS.md)

- **Raw event strictness.** The root schema is the `STRICT_VALIDATION=true` (default) form, so unknown top-level keys are rejected. `#/$defs/lenient` is the `STRICT_VALIDATION=false` form. Keys inside `payload` are never checked in either mode.
- **Timestamps.** Raw `timestamp` must carry an offset (`Z` or `±HH:MM`). Values without one are invalid. Normalised `timestamp`/`ingested_at` are always `...Z` (Java `Instant#toString`). Python-side UTC timestamps may use `Z` or `+00:00`, because `datetime.isoformat()` emits `+00:00`.
- **Nullability.** `NormalizedEvent.component_id` is **always present** and may be `null`. Serialisers must not drop null fields (Jackson: no `NON_NULL` inclusion on `NormalizedEvent`). `PredictiveAlert` no longer has a `playbook_id` field (docs/adr/0008).
- **Optional raw fields** (`type`, `component_id`, `payload`) must be the stated type when present. An explicit `null` is not accepted by this schema. See the open point in `docs/ARCHITECTURE.md` §10.
- **IDs.** `alert_id` = `al-` + 12 lowercase hex. Component ids are lowercase kebab-case.
- **Component ids** are not an enum. New components come from `config/components.yaml` without a schema change.
- **`additionalProperties: false`** on the models: the field lists in CONTRACTS.md are exhaustive. Adding a field means changing CONTRACTS.md (through the Architect) and recording an ADR.
- `format: date-time` is an annotation in draft 2020-12, so every timestamp also has a `pattern` that validators enforce.

## Validating

```bash
pip install 'jsonschema>=4.18'
python3 - <<'PY'
import json
from jsonschema import Draft202012Validator
schema = json.load(open("contracts/raw-event.schema.json"))
Draft202012Validator.check_schema(schema)
Draft202012Validator(schema).validate(
    {"event_id": "e-123", "timestamp": "2026-01-01T12:00:00+02:00", "source": "checkout-api"})
print("ok")
PY
```

Java (ingestion) may use these files with `com.networknt:json-schema-validator` (it supports 2020-12) or hand-written checks that behave the same way. Either way, its unit tests should cover the same valid and invalid cases.
