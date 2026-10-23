# PredictiveAlertsUnacknowledged

| | |
|---|---|
| **Alert** | `PredictiveAlertsUnacknowledged` |
| **Severity** | warning |
| **Service** | forecasting-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [Service Health](http://localhost:9030/d/aiops-service-health) · [SLOs](http://localhost:9030/d/aiops-slo) |

At least one PredictiveAlert has stayed in status `open` for 30 minutes.

## Symptoms

*Open predictive alerts* stat > 0 for a long time.

## Impact

Lead time is being consumed without anyone working the prediction; the breach may occur unhandled.

## Diagnosis

```bash
curl -s 'localhost:9081/alerts?status=open' | jq '.[] | {alert_id, component_id, severity, predicted_breach_time}'
```

## Mitigation

1. Triage each open alert manually (see [predictive-alert-critical](predictive-alert-critical.md)).
2. Acknowledge (`POST /alerts/<id>/ack`) or resolve (`POST /alerts/<id>/resolve`) it.
3. Recurrent noisy alerts for the same component/metric → threshold or model tuning ticket.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the DevOps Engineer on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
