# Repository Structure

Repository layout aligned to the finalized SRS (`AI_Event_Processing_SRS_V2.pdf`, Version 1.0) — see [`docs/PLAN.md`](PLAN.md) for phase status. Area owners are listed in [`CONTRACTS.md`](CONTRACTS.md) §9.

```
ai-driven-system/
├── README.md                          Root documentation
├── LICENSE                            MIT
├── Makefile                           Build / test / up / down shortcuts               
├── Jenkinsfile                        CI/CD: build, test, promtool, terraform, images, optional load test, push
├── .gitignore
│
├── contracts/                         Machine-readable contracts (JSON Schema 2020-12)
│   ├── README.md
│   ├── raw-event.schema.json          Kafka raw-events value
│   ├── normalized-event.schema.json   Kafka normalized-events value
│   └── predictive-alert.schema.json   PredictiveAlert
│   (incident-playbook.schema.json and dependency-graph-seed.schema.json are
│    removed — docs/adr/0008)
│
├── docs/
│   ├── CONTRACTS.md                   Binding integration spec (source of truth)
│   ├── ARCHITECTURE.md                C4 views, runtime sequences, failure modes, scaling
│   ├── adr/                           Architecture Decision Records (0001-0009)
│   ├── SLO.md                         Service-level objectives                         
│   ├── runbooks/                      Operational runbooks per alert
│   ├── jira/                          Jira backlog import (jira-import.csv)
│   ├── PLAN.md                        Implementation road-map
│   ├── STRUCTURE.md                   (this file)
│   └── sdlc-justification.md          SDLC methodology rationale
│
├── ingestion-service/                 Java 17 / Spring Boot 3 — data path (REQ-001 to REQ-005, REQ-008)
│   ├── pom.xml
│   ├── Dockerfile, .dockerignore
│   └── src/
│       ├── main/java/com/eventproc/ingestion/
│       │   ├── IngestionServiceApplication.java
│       │   ├── config/                Kafka + app properties, error handler / DLT recoverer,
│       │   │                          SecurityConfig.java (OAuth2 resource server, JWKS/issuer, REQ-046)
│       │   ├── consumer/EventConsumer.java       Kafka listener, manual ack
│       │   ├── exception/SchemaValidationException.java
│       │   ├── kafka/DeadLetters.java            DLT record + x-* headers
│       │   ├── metrics/IngestionMetrics.java     ingestion_events_total, ingestion_processing_seconds
│       │   ├── model/NormalizedEvent.java
│       │   └── service/NormalizationService.java validate + normalise
│       ├── main/resources/application.yml        server.ssl driven by SSL_ENABLED (REQ-047), OAuth2 issuer/JWKS
│       └── test/java/...              Unit + Kafka integration tests                   
│
├── forecasting-service/               Python 3.11 / FastAPI — predictive analytics (REQ-021 to REQ-025)
│   ├── requirements.txt               Core runtime (no torch, no tensorflow)
│   ├── requirements-ml.txt            torch (CPU), lightning, pytorch-forecasting, scikit-learn, pandas
│   ├── requirements-dev.txt           pytest, ruff, mypy, respx                        
│   ├── Dockerfile (ARG INSTALL_ML=false), .dockerignore
│   ├── config/components.yaml         Components, PromQL, thresholds (hot-reloaded)    
│   ├── training/train_forecast_model.py  Train + backtest the ar-ridge model (Prometheus or CSV)
│   ├── training/train_tft.py             Train the Temporal Fusion Transformer (make train-tft)
│   ├── models/                        Trained model JSON (git-ignored; mounted at /models)
│   ├── app/
│   │   ├── main.py                    FastAPI app, background forecast loop, routes
│   │   ├── forecasting_engine.py      Forecast + threshold evaluation, PredictiveAlert
│   │   ├── metric_client.py           Prometheus query_range client
│   │   ├── predictive_analytics.py    ar-ridge model, confidence, PredictionStore (REQ-021 to REQ-025)
│   │   ├── auth.py                    OAuth2 bearer-JWT validation (REQ-046)
│   │   └── …                          forecasters (statistical / ML), alert store
│   └── tests/                         Run without ML deps / network                   
│
├── diagnostic-service/                Python 3.11 / FastAPI — anomaly detection + GraphRAG root cause (ADR-0010)
│   ├── requirements.txt               confluent-kafka, PyJWT, networkx, neo4j driver, jinja2, httpx
│   ├── requirements-dev.txt                                                             
│   ├── Dockerfile, .dockerignore
│   ├── README.md                      How the scoring model works
│   ├── app/
│   │   ├── main.py                    FastAPI app: /score, /thresholds, /model, /diagnose, /playbooks, /graph, /incidents
│   │   ├── anomaly_scorer.py          ewma-zscore score_event (REQ-016), with_version swap (REQ-018)
│   │   ├── threshold_manager.py       evaluate/set_threshold (REQ-017, REQ-020)
│   │   ├── audit_publisher.py         publish_anomaly → Kafka anomaly-audit (REQ-019)
│   │   ├── detector.py                score → evaluate → audit pipeline, atomic model swap
│   │   ├── event_consumer.py          normalized-events consumer (REQ-016)
│   │   ├── auth.py                    OAuth2 bearer-JWT validation (REQ-046)
│   │   ├── incident_trigger.py        anomaly → batched GraphRAG diagnosis → incident-playbooks topic
│   │   ├── dependency_graph_client.py Neo4j (default) / networkx 2-hop retrieval, seeds Neo4j
│   │   ├── diagnostic_engine.py       GraphRAG: retrieve_context → prompt → LLM → playbook
│   │   ├── llm_client.py              Ollama / Anthropic / OpenAI over HTTP
│   │   ├── fallback.py                rule-based playbook when the LLM is unavailable
│   │   ├── playbook_generator.py      IncidentPlaybook parsing + JSON-file store
│   │   └── templates/diagnostic_prompt.j2
│   ├── data/dependency_graph.json     Service catalogue: 15 components and their edges
│   └── tests/                         Scorer, thresholds, audit, consumer, API, OAuth2, graph, LLM, incident trigger
│
├── console/                           Demo dashboard on :9099 (FastAPI backend + static HTML/CSS/JS)
│
├── storage/                           event-archiver: Kafka → S3 (REQ-028 to REQ-030, ADR-0009)
│   ├── requirements.txt               boto3, confluent-kafka, prometheus-client
│   ├── Dockerfile, .dockerignore, pyproject.toml
│   ├── archiver.py                    Consumer: batch per partition, upload, commit after upload
│   ├── archive_client/client.py       ArchiveClient: archive_batch / archive (boto3; MinIO or S3)
│   └── tests/                         Fake S3 + consumer
│
├── dashboard/
│   └── README.md                      REQ-034 notes (realised as Grafana, ADR-0006)
│
├── infra/
│   ├── docker-compose.yml             Kafka, ZK, Prometheus, Grafana, MinIO, all services
│   ├── docker-compose.aws.yml         Override: archive to your real S3 bucket
│   ├── prometheus.yml                 Scrape config (adds `service` label per job)
│   ├── prometheus/rules/*.yml         Recording + alert rules → /etc/prometheus/rules
│   ├── grafana/
│   │   ├── provisioning/              Data source (Prometheus), dashboard providers
│   │   └── dashboards/                Dashboard JSON                                   
│   ├── k8s/
│   │   ├── ingestion-deployment.yaml  Namespace, ConfigMap, Deployment, Service
│   │   ├── diagnostic-deployment.yaml ConfigMap, Deployment (2-6 pods, Kafka consumer), Service
│   │   ├── archiver-deployment.yaml   IRSA ServiceAccount, ConfigMap, Deployment, Service
│   │   ├── ingress.yaml               ALB Ingress, TLS 1.2+ (REQ-047)
│   │   └── hpa.yaml                   HorizontalPodAutoscalers (ingestion, diagnostic)
│   └── terraform/                     AWS (REQ-044); azurerm block commented out
│       ├── main.tf, variables.tf, outputs.tf, terraform.tfvars.example
│       ├── eks.tf                     EKS cluster + node group
│       ├── s3.tf                      Archive bucket (lifecycle, SSE, TLS-only) + archiver IRSA role
│       └── ecr.tf                     Image repositories
│
├── loadtest/                          JMeter load test (REQ-001/004): jmeter/*.jmx, run-load-test.sh, analyze.py
│
├── ci/jenkins/                        Local Jenkins in Docker that runs the Jenkinsfile (run-local.sh)
│
└── scripts/
    ├── setup-local.sh                 Bootstrap the local stack
    └── produce-events.sh              Send valid / invalid sample events to raw-events 
```

## Naming Conventions

| Layer | Convention |
|---|---|
| Java packages | `com.eventproc.<service>.<layer>` |
| Python modules | `snake_case` |
| JSON fields (all contracts) | `snake_case` |
| Component ids | lowercase kebab-case, e.g. `forecasting-service` |
| IDs | `al-<12 hex>` (alerts) |
| Prometheus metrics | Fixed names in CONTRACTS.md §7 |
| ADRs | `docs/adr/NNNN-kebab-title.md`, never renumbered |
| Kubernetes labels | `app.kubernetes.io/name`, `app.kubernetes.io/component` |
| Docker image tags | `ghcr.io/<org>/<service>:<tag>` |

## Port Assignments

| Service | Host port (compose) | Service port (in-network, k8s) |
|---|---|---|
| ingestion-service | 9080 | 8080 |
| forecasting-service | 9081 | 8081 |
| diagnostic-service | 9082 | 8082 |
| event-archiver metrics | 9083 | 8083 |
| Grafana | 9030 | 3000 |
| Prometheus | 9090 | 9090 |
| Kafka broker | 9092 | `kafka:29092` |
| Zookeeper | — | 2181 |
| MinIO (REQ-028 local S3 stand-in) | 9000 (API) / 9001 (console) | same |
| Jenkins (local CI) | 9095 | 8080 |
