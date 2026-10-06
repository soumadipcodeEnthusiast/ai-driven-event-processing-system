# ADR-0006: Grafana + Infinity data source as the REQ-K dashboard

## Status
Accepted (2026-10-04)

## Context
REQ-K: show new alerts within 5 s, and let operators get from an alert to its playbook. The candidates were Grafana, a React SPA, or htmx (dashboard/README.md). Grafana is already needed for metric and service-health panels.

## Decision
Use Grafana (port 3000), provisioned from `infra/grafana/provisioning/` with dashboards in `infra/grafana/dashboards/`. The `yesoreyeram-infinity-datasource` plugin reads JSON from `forecasting-service:8081/alerts` and `diagnostic-service:8082/playbooks`, and a Prometheus data source covers the metric panels. The alerts table refreshes every 5 s or faster. A data link on `playbook_id` opens the playbook panel (dashboard variable) or `GET /playbook/{id}?format=markdown`.

## Consequences
- + No front-end code. One tool for both AIOps state and service health, and dashboards live in git.
- + Meets the 5 s requirement through polling: about 1 request per 5 s per open dashboard, which is negligible.
- − "Within 5 s" depends on refresh interval plus query latency. Grafana's `min_refresh_interval` must allow 5 s, and the `/alerts` latency must stay well under 1 s.
- − Limited UX: no ack/resolve buttons by default. Operators use the API or a later plugin.
- − Grafana reaches the services over the internal network without authentication. Acceptable only on an internal network (see ARCHITECTURE.md §9).
