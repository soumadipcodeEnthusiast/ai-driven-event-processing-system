# Dashboard (REQ-K)

## Decision: Grafana + Infinity datasource

The REQ-K observability dashboard is a **provisioned Grafana dashboard**, not a custom
front-end. Grafana reads:

- **PredictiveAlerts** and **IncidentPlaybooks** as JSON straight from the service
  REST APIs, via the [`yesoreyeram-infinity-datasource`](https://grafana.com/grafana/plugins/yesoreyeram-infinity-datasource/)
  plugin (CONTRACTS §2, §8);
- **service metrics** from Prometheus (CONTRACTS §7).

| Option considered | Outcome |
|---|---|
| **Grafana + Infinity** | **Chosen.** Zero front-end code, versioned as JSON, same tool as the metrics dashboards, built-in refresh, data links and auth. |
| React + Recharts | Rejected: build toolchain and a new service to operate for a read-only view. |
| Plain HTML + htmx | Rejected: would duplicate Grafana's table/link/refresh features. |

Everything is config-as-code:

| Path | Purpose |
|---|---|
| `infra/grafana/provisioning/datasources/datasources.yml` | Prometheus (uid `prometheus`, default) and Infinity (uid `infinity`) |
| `infra/grafana/provisioning/dashboards/dashboards.yml` | File provider → `/var/lib/grafana/dashboards`, folder **AIOps** |
| `infra/grafana/dashboards/aiops-overview.json` | **The REQ-K dashboard** |
| `infra/grafana/dashboards/service-health.json` | Golden signals per service |
| `infra/grafana/dashboards/slo.json` | SLO compliance and error budgets ([docs/SLO.md](../docs/SLO.md)) |

## How to open it

```bash
docker compose -f infra/docker-compose.yml up -d
open http://localhost:3000/d/aiops-overview      # Dashboards → AIOps → "AIOps Overview"
```

Anonymous read-only (Viewer) access is enabled for the local stack; admin login is
`admin` / `admin` unless `GRAFANA_ADMIN_PASSWORD` is set. Add auth (or disable anonymous
access) before exposing Grafana outside the internal network.

Infinity queries run **server-side in Grafana**, so they use the compose DNS names
`forecasting-service:8081` and `diagnostic-service:8082`. The only browser-side link is the
"Open playbook (Markdown)" link, which uses `http://localhost:8082` (the published port).

## AIOps Overview — panels

Refresh: **5 s**. Time range has no effect on the API tables (they always show the
current state); it only affects Prometheus panels.

| Panel | Source | What it shows |
|---|---|---|
| Open predictive alerts | Prometheus `predictive_alerts_open` | Number of open alerts; orange ≥ 1, red ≥ 5 |
| Alerts emitted (24h) by severity | `predictive_alerts_total{severity}` | low / medium / high / critical counts, colour-coded |
| Diagnoses (24h) by outcome | `diagnoses_total{outcome}` | ok (LLM) / fallback (rule-based) / error (no playbook) |
| Diagnostic fallback ratio (30m) | recording rule `service:diagnoses_fallback:ratio_rate30m` | Share of playbooks from the rule-based fallback — LLM health |
| Time to next predicted breach | Infinity `/alerts/summary` | Minutes until the soonest predicted breach among open/acknowledged alerts, computed live (negative = predicted time passed). Red below 10 min, orange below 30 (REQ-E) |
| Forecaster | `forecaster_info{forecaster,model_version}` | TFT or statistical fallback, and model version |
| **Live predictive alerts** | Infinity `GET forecasting-service:8081/alerts?status=$alert_status&limit=100` | Every PredictiveAlert field from CONTRACTS §3: `alert_id`, `component_id`, `metric_name`, `severity` (blue/yellow/orange/red), `status`, `predicted_breach_time`, `lead_time_minutes`, `predicted_value`, `threshold`, `created_at`, `model_version`, `playbook_id`. Newest first. Every cell has data links to the alert's playbook. |
| Incident playbooks | Infinity `GET diagnostic-service:8082/playbooks` | `playbook_id`, `alert_id`, `component_id`, `summary`, `root_cause_hypothesis`, `generated_by`, `generated_at`; rows link to the playbook too |
| Playbook `${playbook_id}` | Infinity `GET /playbook/{id}` | Header of the selected playbook |
| Ranked remediation steps | Infinity `GET /playbook/{id}` (root `steps`) | `rank`, `title`, `command`, `expected_outcome` (REQ-H: ≥ 3 steps) |

Variables: `alert_status` (open / acknowledged / resolved) filters the live feed;
`playbook_id` selects the playbook shown at the bottom (set automatically by clicking a row).

Clicking any cell of an alert (or playbook) row offers two links:

1. **Open playbook in this dashboard** → sets `var-playbook_id=<playbook_id>` and scrolls
   to the playbook header and its ranked steps.
2. **Open playbook (Markdown, diagnostic-service)** →
   `http://localhost:8082/playbook/<playbook_id>?format=markdown` in a new tab (the
   `render()` output).

Navigation links at the top lead to **Service Health**, **SLOs** and the
[runbooks](../docs/runbooks/README.md).

## Other dashboards

- **AIOps Service Health** (`/d/aiops-service-health`): service `up`, firing Prometheus
  alerts, ingestion throughput by outcome, Kafka consumer lag (5 000-record REQ-A line),
  processing latency p95/p99 (500 ms line), actuator HTTP rate/latency, JVM memory,
  forecast runs by component/outcome, forecast latency p95 (2 s line), predictive alerts by
  severity, diagnoses by outcome, diagnosis / LLM latency, LLM failures, graph-context
  latency p95 (1 s line, REQ-G), playbooks stored, process memory/CPU.
- **AIOps SLOs & Error Budgets** (`/d/aiops-slo`): error budget remaining per SLO, 30-day
  SLI vs. objective, burn rates (1h / 6h / 3d) with page/ticket thresholds, service
  availability, firing burn-rate alerts.

## REQ-K acceptance criteria

| Criterion | How it is met |
|---|---|
| Live feed of `PredictiveAlert` objects visible **within 5 s** of emission | The alerts table queries `/alerts` directly (no cache or intermediate store) and the dashboard auto-refreshes every 5 s, so a new alert appears on the next refresh: ≤ 5 s + one HTTP round-trip. Alerts are newest-first. |
| **Clicking an alert navigates to the linked `IncidentPlaybook`** | Data links on every alert-row cell use `${__data.fields.playbook_id}` to open the playbook in-dashboard (summary + ranked steps) or as Markdown from `GET /playbook/{id}?format=markdown`. If the playbook isn't ready yet, `playbook_id` is empty until diagnosis finishes. |
| Accessible without authentication on the internal network | Anonymous Viewer access in compose (`GF_AUTH_ANONYMOUS_ENABLED=true`, role Viewer); dashboards are read-only (`editable: false`, `allowUiUpdates: false`). |

Reliability of the dashboard itself is monitored: Prometheus scrapes Grafana and
`GrafanaDown` fires after 5 min ([runbook](../docs/runbooks/grafana-down.md)).

## Editing dashboards

Provisioned dashboards can't be saved from the UI. Edit the JSON under
`infra/grafana/dashboards/` (or export from the UI and replace the file), keep the `uid`s
stable (`aiops-overview`, `aiops-service-health`, `aiops-slo`) and reference datasources
by uid (`prometheus`, `infinity`). Grafana picks up changes within 30 s.
