# PredictiveAlertCritical

| | |
|---|---|
| **Alert** | `PredictiveAlertCritical` |
| **Severity** | critical |
| **Service** | forecasting-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

The forecasting service emitted a **critical** PredictiveAlert (predicted p90 overshoot ≥ 50 %) for a component. This is the product working: a breach is predicted, not yet happening.

## Symptoms

New red row in *Live predictive alerts* on the AIOps Overview dashboard.

## Impact

If no action is taken, the component is predicted to breach its threshold at `predicted_breach_time` (≥ 10 min away).

## Diagnosis

1. Open http://localhost:3000/d/aiops-overview, find the alert row (component, metric,
   `predicted_breach_time`, `lead_time_minutes`).
2. Click the row → the linked IncidentPlaybook (summary, root-cause hypothesis, ranked steps).
```bash
curl -s 'localhost:8081/alerts?status=open&component_id=<component>' | jq
curl -s 'localhost:8082/playbook/<playbook_id>?format=markdown'
```
3. Validate against current reality on *AIOps Service Health* (is the metric actually trending?).

## Mitigation

1. Acknowledge so others know it's handled: `curl -XPOST localhost:8081/alerts/<alert_id>/ack`.
2. Execute the playbook steps in rank order, checking each `expected_outcome`.
3. When the risk is gone: `curl -XPOST localhost:8081/alerts/<alert_id>/resolve`.
4. No playbook (`playbook_id` null) → `curl -XPOST localhost:8082/diagnose/<component> -d '{"alert_id":"<id>"}'`
   and check [diagnosis-error-rate-high](diagnosis-error-rate-high.md).
5. False positive → tune the threshold at runtime (REQ-F): `PUT /thresholds/<component>`; record it
   for model review.

## Escalation

Owner of the affected component first; SRE on-call if the playbook does not resolve the trend before `predicted_breach_time`.
