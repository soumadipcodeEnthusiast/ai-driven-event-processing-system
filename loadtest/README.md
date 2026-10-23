# Load test (JMeter)

[`jmeter/event-processing-load.jmx`](jmeter/event-processing-load.jmx) drives the running stack:

| Thread group | Load | Measures |
|---|---|---|
| Kafka producers | JSR223/Groovy sampler with the real Kafka client. Each sample sends a batch (default 100 events) with `acks=all` and waits for every ack. A Constant Throughput Timer holds the target events/s. ~0.2 % of events are latency outliers. | REQ-004 broker-ack latency, REQ-001 throughput |
| API | `POST /score/{id}` (diagnostic), `GET /predictions`, `GET /alerts/summary` (forecasting) | p95 latency, error rate (HTTP 200 + response-time assertions) |

[`run-load-test.sh`](run-load-test.sh) runs JMeter in non-GUI mode, waits for the pipeline to drain, then [`analyze.py`](analyze.py) combines the JMeter results with Prometheus metrics into a PASS/FAIL report:

| Check | Threshold |
|---|---|
| REQ-004 batch ack p95 | ≤ 50 ms |
| REQ-001 throughput | ≥ 95 % of target |
| REQ-001 ingestion consumer lag | < 5 000 records (= 500 ms at 10 000 ev/s) |
| Ingestion processing p95 (SLO) | < 500 ms |
| End to end | every produced event ingested (±3 %), anomaly-scored (±3 %), archived to S3 (≥ 90 %) |
| API | p95 ≤ `--api-max-ms` (500), errors < 1 % |

## Run

```bash
make up
JMETER_HOME=/path/to/apache-jmeter-5.6.3 loadtest/run-load-test.sh --rate 2000 --duration 120
# REQ-001 target:
JMETER_HOME=… loadtest/run-load-test.sh --rate 10000 --duration 120 --threads 16
```

Options: `--rate` (events/s), `--duration` (s), `--threads` (producer threads), `--batch` (events per sample), `--api-rate` (req/s), `--api-max-ms`.

Output goes to `loadtest/results/<run>/` (git-ignored): `summary.md`, `results.jtl`, and the JMeter HTML dashboard in `report/index.html`. The script exits 1 if any check fails, and the Jenkins `Load test (JMeter)` stage gates on that (`RUN_LOAD_TEST=true`).

To edit the plan in the GUI: `jmeter -t loadtest/jmeter/event-processing-load.jmx`. Copy `loadtest/.lib/*.jar` into JMeter's `lib/` first so the Groovy producer compiles; `run-load-test.sh` creates `.lib/` on its first run.

Laptop numbers are bounded by Docker Desktop: a single Kafka broker with RF=1 and one ingestion container. Treat a local 10k ev/s result as indicative. The real REQ-001 measurement belongs on the EKS deployment (docs/PLAN.md Phase 5).
