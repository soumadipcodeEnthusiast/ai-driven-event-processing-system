# GrafanaDown

| | |
|---|---|
| **Alert** | `GrafanaDown` |
| **Severity** | warning |
| **Service** | grafana |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

Grafana (the REQ-K dashboard) has not been scrapeable for 5 min.

## Symptoms

http://localhost:3000 does not load; `up{job="grafana"} == 0`.

## Impact

Operators cannot see the live PredictiveAlert feed or open playbooks from the dashboard (REQ-K). Alerting itself (Prometheus rules, forecasting, diagnostics) keeps working; alerts and playbooks remain available via `GET :8081/alerts` and `GET :8082/playbooks`.

## Diagnosis

```bash
docker compose -f infra/docker-compose.yml ps grafana
docker compose -f infra/docker-compose.yml logs --tail=200 grafana     # plugin install failures (Infinity), provisioning errors
curl -fsS localhost:3000/api/health
```
Common causes: `GF_INSTALL_PLUGINS` download failed (no internet), invalid provisioning YAML under
`infra/grafana/provisioning/`, broken dashboard JSON under `infra/grafana/dashboards/`.

## Mitigation

1. `docker compose -f infra/docker-compose.yml restart grafana`.
2. Provisioning error → validate files: `python3 -m json.tool infra/grafana/dashboards/*.json`, fix, restart.
3. Plugin cannot be downloaded → pre-bake the plugin into the image or mount it into `/var/lib/grafana/plugins`.
4. Meanwhile, read alerts directly: `curl -s localhost:8081/alerts | jq`.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
