# PredictiveAlertCritical

| | |
|---|---|
| **Alert** | `PredictiveAlertCritical` |
| **Severity** | critical |
| **Service** | forecasting-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [Service Health](http://localhost:9030/d/aiops-service-health) · [SLOs](http://localhost:9030/d/aiops-slo) |

The forecasting service emitted a **critical** PredictiveAlert (predicted p90 overshoot ≥ 50 %) for a component. This is the product working: a breach is predicted, not yet happening.

## Symptoms

`predictive_alerts_total{severity="critical"}` increasing; red entries in `GET /alerts`.

## Impact

If no action is taken, the component is predicted to breach its threshold at `predicted_breach_time` (≥ 10 min away).

## Diagnosis

```bash
curl -s 'localhost:9081/alerts?status=open&component_id=<component>' | jq
```

Check the alert's `metric_name`, `predicted_breach_time`, `lead_time_minutes` and `predicted_value`/`threshold` fields. Validate against current reality on *AIOps Service Health* (is the metric actually trending?). There is no linked playbook any more — root-cause investigation is manual (GraphRAG-based diagnosis is Appendix C future work, see docs/adr/0008).

## Mitigation

1. Acknowledge so others know it's handled: `curl -XPOST localhost:9081/alerts/<alert_id>/ack`.
2. Investigate the component manually (check its dashboards, logs, recent deploys) and remediate.
3. When the risk is gone: `curl -XPOST localhost:9081/alerts/<alert_id>/resolve`.
4. False positive → tune the threshold at runtime (REQ-052): `PUT /thresholds/<component>`; record it
   for model review.

## Escalation

Owner of the affected component first; DevOps Engineer on-call if mitigation does not resolve the trend before `predicted_breach_time`.
