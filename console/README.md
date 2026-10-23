# Demo console

A single page at **http://localhost:9099** that shows the local stack working and walks through the demo. It is part of `infra/docker-compose.yml` (`demo-console`) and starts with `make up`. It is not deployed to Kubernetes.

| Section | What it shows | Where the data comes from |
|---|---|---|
| A. Event flow | Producers → `raw-events` → ingestion → `normalized-events` / DLT → diagnostic → `anomaly-audit`, plus archiver → S3 and Prometheus → forecasting → alerts. Links animate and counters show `+N` while messages move | Kafka end offsets (exact), each service's `/metrics` |
| B. Services | Health of all 9 containers, probe latency | Each service's health endpoint, from inside the Docker network |
| C. Kafka | Topics, partitions, message totals, live rate, consumer-group lag, 30-minute ingestion rate | Kafka AdminClient; Prometheus `ingestion_events_total` |
| D. Run the demo | Six steps with buttons and a line to say for each | The services' own APIs (nothing is mocked) |
| E. Activity | What you clicked and what happened | |

Demo steps: **01** publish events (some broken on purpose) · **02** read the latest messages on any topic, DLT reasons included · **03** score a request against a fresh 30-sample baseline and change the threshold live · **04** forecast 60 minutes ahead, or simulate a breach (lowers one threshold, forecasts, restores it) and ack/resolve the alert · **05** list the S3 archive · **06** links to Grafana, Prometheus and the API docs.

Port 9092 is Kafka's binary protocol, not a web page. Opening it in a browser only logs `InvalidReceiveException (size = 1195725856)` in Kafka, which is the bytes `GET `. Use this console to see Kafka instead.

Run it outside Docker (from the repo root, stack up):

```bash
pip install -r console/requirements.txt
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 INGESTION_URL=http://localhost:9080 DIAGNOSTIC_URL=http://localhost:9082 \
FORECASTING_URL=http://localhost:9081 ARCHIVER_URL=http://localhost:9083 PROMETHEUS_URL=http://localhost:9090 \
GRAFANA_URL=http://localhost:9030 ZOOKEEPER_ADDR=localhost:2181 S3_ENDPOINT_URL=http://localhost:9000 \
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
uvicorn app:app --app-dir console --port 9099
```

(ZooKeeper isn't published to the host, so its row shows down when the console runs outside Docker.)
