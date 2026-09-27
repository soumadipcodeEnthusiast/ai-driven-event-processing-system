# Dashboard

## Overview

The **Observability Dashboard** (REQ-K) provides a real-time view of:
- Active `PredictiveAlert` instances emitted by the forecasting-service
- Generated `IncidentPlaybook` documents produced by the diagnostic-service
- Service health metrics (Kafka consumer lag, forecast latency, LLM latency)

## Planned Implementation

The dashboard is a read-only front-end that polls the REST APIs of the
forecasting-service and diagnostic-service. It is intentionally decoupled
from the data path.

### `ObservabilityDashboard` class (REQ-K)

| Operation | Description |
|---|---|
| `renderAlert(alertId)` | Fetches and renders a `PredictiveAlert` by ID |
| `renderPlaybook(playbookId)` | Fetches and renders an `IncidentPlaybook` by ID |

### Candidate technology choices

| Option | Trade-offs |
|---|---|
| Grafana (panels + data sources) | Zero front-end code; limited custom layout |
| React + Recharts | Full control; requires build toolchain |
| Plain HTML + htmx | Minimal dependencies; good for internal tools |

> **Decision pending** — select a technology and scaffold the dashboard in Phase 4.

## Grafana Quick-Start (Recommended for Phase 4)

Add to `infra/docker-compose.yml`:

```yaml
grafana:
  image: grafana/grafana:10.4.2
  ports:
    - "3000:3000"
  environment:
    GF_SECURITY_ADMIN_PASSWORD: admin
  volumes:
    - grafana-data:/var/lib/grafana
  depends_on:
    - prometheus
  networks:
    - event-net
```

Provision dashboards via `infra/grafana/dashboards/` and a
`provisioning/` directory.

## REQ-K Acceptance Criteria

- Dashboard displays a live feed of `PredictiveAlert` objects within 5 s of emission.
- Clicking an alert navigates to the linked `IncidentPlaybook`.
- Dashboard is accessible without authentication on the internal network
  (add auth before exposing externally).
