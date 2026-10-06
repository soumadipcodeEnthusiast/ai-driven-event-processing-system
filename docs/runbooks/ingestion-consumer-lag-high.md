# IngestionConsumerLagHigh

| | |
|---|---|
| **Alert** | `IngestionConsumerLagHigh` |
| **Severity** | critical (> 5000 for 5m) / warning (> 1000 for 15m) |
| **Service** | ingestion-service |
| **Rule file** | `infra/prometheus/rules/aiops-alerts.yml` |
| **Dashboards** | [AIOps Overview](http://localhost:3000/d/aiops-overview) · [Service Health](http://localhost:3000/d/aiops-service-health) · [SLOs](http://localhost:3000/d/aiops-slo) |

The ingestion consumer is falling behind `raw-events`. At 10 000 ev/s, 5 000 records ≈ 500 ms (REQ-A).

## Symptoms

*Kafka consumer lag* panel above the threshold line.

## Impact

Normalised events — and therefore forecasts and predictive alerts — are delayed. REQ-A is violated while lag > 5 000.

## Diagnosis

```promql
service:kafka_consumer_records_lag:max
max by (topic, partition) (kafka_consumer_fetch_manager_records_lag_max)
sum(rate(ingestion_events_total[1m]))      # consume rate
```
```bash
docker exec kafka kafka-consumer-groups --bootstrap-server localhost:9092 \
  --describe --group ingestion-consumer-group
docker compose -f infra/docker-compose.yml logs --since=10m ingestion-service | grep -i "rebalanc\|retry"
```
Check whether one partition is hot (skewed keys) or all partitions lag (capacity), and whether the
consumer is rebalancing repeatedly.

## Mitigation

1. Capacity: scale out to ≤ 3 consumers in total (3 partitions); then consider more partitions
   (architect change).
2. Rebalance storm: check `max.poll.interval.ms` vs. retry back-off; restart the deployment.
3. Slow processing: see [ingestion-latency-budget-burn](ingestion-latency-budget-burn.md).
4. Producer burst (load test) that will drain on its own: acknowledge and watch the trend.

## Escalation

1. Service owner (see table in [README](README.md#ownership)).
2. If not acknowledged within 15 min for `critical`, escalate to the SRE on-call.
3. If customer-visible for > 30 min, declare an incident (SEV-2) and open a postmortem.
