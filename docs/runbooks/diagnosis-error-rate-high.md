# DiagnosisErrorRateHigh

| | |
|---|---|
| **Alert** | `DiagnosisErrorRateHigh` |
| **Severity** | critical |
| **Service** | diagnostic-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

More than 5 % of diagnoses (with ≥ 3 in 30 min) ended in `outcome="error"` — no playbook at all.

## Symptoms

Alerts on the REQ-K dashboard have empty `playbook_id`; *Diagnoses by outcome* shows `error`.

## Impact

REQ-H/REQ-I violated: on-call gets predictive alerts without remediation steps.

## Diagnosis

```promql
sum by (outcome) (increase(diagnoses_total[30m]))
sum(increase(playbooks_stored_total[30m]))
```
```bash
docker compose -f infra/docker-compose.yml logs --since=30m diagnostic-service | grep -iE "error|exception|traceback"
docker compose -f infra/docker-compose.yml exec diagnostic-service ls -la /data/playbooks | tail      # PLAYBOOK_STORE_DIR writable? disk full?
curl -s -XPOST localhost:8082/diagnose/ingestion-service -H 'content-type: application/json' -d '{}'
```
Causes: graph seed missing/invalid (`GRAPH_SEED_PATH`), Neo4j unreachable (`GRAPH_BACKEND=neo4j`),
playbook store not writable, a bug in the fallback path.

## Mitigation

1. Graph backend: switch to `GRAPH_BACKEND=networkx` (default seed) and restart.
2. Store not writable / full → fix the `playbooks-data` volume.
3. Re-diagnose affected alerts: `curl -XPOST localhost:8082/diagnose/<component> -d '{"alert_id":"<id>"}'`.
4. Escalate to the AI Engineer — the fallback path should never error.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
