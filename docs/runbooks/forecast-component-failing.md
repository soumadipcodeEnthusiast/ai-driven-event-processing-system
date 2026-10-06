# ForecastComponentFailing

| | |
|---|---|
| **Alert** | `ForecastComponentFailing` |
| **Severity** | warning |
| **Service** | forecasting-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

More than 50 % of forecast runs for one component failed over 15 minutes.

## Symptoms

That component's `error` series on *Forecast runs by component / outcome*.

## Impact

No predictive coverage for that component.

## Diagnosis

```promql
sum by (component, outcome) (rate(forecast_runs_total{component="<component>"}[15m]))
```
```bash
docker compose -f infra/docker-compose.yml logs --since=15m forecasting-service | grep "<component>"
curl -s localhost:8081/thresholds | jq '."<component>"'
cat forecasting-service/config/components.yaml
```
Usually the component's metric queries return no series (renamed metric, target down) or the
threshold config is malformed after a `PUT /thresholds`.

## Mitigation

1. Fix the PromQL in `components.yaml` (hot-reloaded on mtime change, no restart needed).
2. Re-apply a valid threshold: `curl -XPUT localhost:8081/thresholds/<component> -d '{...}'`.
3. If the component's own scrape is down, follow [service-down](service-down.md).

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
