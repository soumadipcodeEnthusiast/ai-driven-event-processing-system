# ServiceDown

| | |
|---|---|
| **Alert** | `ServiceDown` |
| **Severity** | critical |
| **Service** | any application service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

Prometheus cannot scrape ingestion-, forecasting- or diagnostic-service for > 1 min.

## Symptoms

`up{job="<service>"} == 0`. Panels for that service on *AIOps Service Health* go blank; the *Service up* stat shows DOWN.

## Impact

- **ingestion-service**: no events are normalised; Kafka lag grows; downstream forecasts run on stale data.
- **forecasting-service**: no PredictiveAlerts are emitted (REQ-E) and the REQ-K live feed is empty.
- **diagnostic-service**: alerts get no playbook (`playbook_id = null`), REQ-H/REQ-I violated.

## Diagnosis

```promql
up{job=~"ingestion-service|forecasting-service|diagnostic-service"}
changes(process_start_time_seconds[30m])          # restart loop?
```
```bash
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml logs --tail=200 <service>
curl -fsS localhost:8080/actuator/health   # ingestion
curl -fsS localhost:8081/health            # forecasting
curl -fsS localhost:8082/health            # diagnostic
# Kubernetes
kubectl -n event-processor get pods -l app.kubernetes.io/name=<service>
kubectl -n event-processor describe pod <pod>          # OOMKilled? CrashLoopBackOff? failing probes?
kubectl -n event-processor logs <pod> --previous
```
If the service answers on its port but `up == 0`, the problem is the scrape path
(`/actuator/prometheus` or `/metrics`) or Prometheus networking — check
http://localhost:9090/targets.

## Mitigation

1. Restart the container: `docker compose -f infra/docker-compose.yml restart <service>` (k8s: `kubectl -n event-processor rollout restart deploy/<service>`).
2. If it crashes on start, check recent config changes (env vars in CONTRACTS §5, `components.yaml`)
   and roll back: `kubectl -n event-processor rollout undo deploy/<service>`.
3. OOMKilled → raise memory limit or see [ingestion-jvm-heap-high](ingestion-jvm-heap-high.md).
4. Dependency down (Kafka for ingestion, Prometheus for forecasting): fix that first.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
