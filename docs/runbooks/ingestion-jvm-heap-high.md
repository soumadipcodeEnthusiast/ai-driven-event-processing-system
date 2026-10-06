# IngestionJvmHeapHigh

| | |
|---|---|
| **Alert** | `IngestionJvmHeapHigh` |
| **Severity** | warning |
| **Service** | ingestion-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

JVM heap usage of an ingestion instance has been > 90 % for 10 minutes.

## Symptoms

*JVM heap / non-heap* panel near max; GC pauses increase; latency and lag follow.

## Impact

Risk of `OutOfMemoryError` / OOMKill → consumer restarts → rebalances → lag.

## Diagnosis

```promql
sum by (instance) (jvm_memory_used_bytes{service="ingestion-service", area="heap"})
rate(jvm_gc_pause_seconds_sum{service="ingestion-service"}[5m])
jvm_threads_live_threads{service="ingestion-service"}
```
```bash
kubectl -n event-processor describe pod <pod> | grep -A3 "Last State"     # OOMKilled?
curl -s localhost:8080/actuator/metrics/jvm.memory.used
```

## Mitigation

1. Short term: restart the instance (`kubectl -n event-processor delete pod <pod>`); HPA/replicas keep consuming.
2. Raise the container memory limit and `-XX:MaxRAMPercentage` together.
3. If heap grows steadily after restart → leak: capture a heap dump and escalate to the Data Engineer.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
