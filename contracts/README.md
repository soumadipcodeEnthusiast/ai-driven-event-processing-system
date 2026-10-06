# Contracts (JSON Schema, draft 2020-12)

Machine-readable versions of the data contracts in [`docs/CONTRACTS.md`](../docs/CONTRACTS.md).
`CONTRACTS.md` is the source of truth. If the two ever disagree, `CONTRACTS.md` wins and the schema is the bug.
These schemas add no new fields. They spell out details the prose leaves implicit (patterns, nullability, required keys).

| Schema | Describes | Produced by | Consumed by |
|---|---|---|---|
| [`raw-event.schema.json`](raw-event.schema.json) | Kafka `raw-events` value (§1.1) | external producers, `scripts/produce-events.sh` | ingestion-service |
| [`normalized-event.schema.json`](normalized-event.schema.json) | Kafka `normalized-events` value (§1.2) | ingestion-service | downstream consumers (none yet) |
| [`predictive-alert.schema.json`](predictive-alert.schema.json) | `PredictiveAlert` (§3), items of `GET /alerts`, `GET /alerts/{id}`, `POST /forecast` → `alert` | forecasting-service | Grafana (Infinity), diagnostic hand-off |
| [`incident-playbook.schema.json`](incident-playbook.schema.json) | `IncidentPlaybook` (§3), `GET /playbook/{id}`, files in `PLAYBOOK_STORE_DIR` | diagnostic-service | Grafana (Infinity), operators |
| [`dependency-graph-seed.schema.json`](dependency-graph-seed.schema.json) | `diagnostic-service/data/dependency_graph.json` (§4) | humans / CMDB export | diagnostic-service graph backends |

## Interpretation notes (consistent with CONTRACTS.md)

- **Raw event strictness.** The root schema is the `STRICT_VALIDATION=true` (default) form, so unknown top-level keys are rejected. `#/$defs/lenient` is the `STRICT_VALIDATION=false` form. Keys inside `payload` are never checked in either mode.
- **Timestamps.** Raw `timestamp` must carry an offset (`Z` or `±HH:MM`). Values without one are invalid. Normalised `timestamp`/`ingested_at` are always `...Z` (Java `Instant#toString`). Python-side UTC timestamps may use `Z` or `+00:00`, because `datetime.isoformat()` emits `+00:00`.
- **Nullability.** `NormalizedEvent.component_id`, `PredictiveAlert.playbook_id` and `IncidentPlaybook.alert_id` are **always present** and may be `null`. Serialisers must not drop null fields (Jackson: no `NON_NULL` inclusion on `NormalizedEvent`).
- **Optional raw fields** (`type`, `component_id`, `payload`) must be the stated type when present. An explicit `null` is not accepted by this schema. See the open point in `docs/ARCHITECTURE.md` §10.
- **Playbook steps.** `minItems: 3` and `prefixItems` pin ranks 1, 2, 3. The full "ranks are exactly 1..n in order" rule cannot be expressed in JSON Schema, so service tests must also assert `[s.rank for s in steps] == list(range(1, n+1))`.
- **IDs.** `alert_id` = `al-` + 12 lowercase hex. `playbook_id` = `pb-<component_id>-` + 8 lowercase hex. Component ids are lowercase kebab-case. The playbook id pattern also protects the file store against path traversal, so validate it before any disk access.
- **Component ids** are not an enum. New components come from `config/components.yaml` and the graph seed without a schema change.
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
