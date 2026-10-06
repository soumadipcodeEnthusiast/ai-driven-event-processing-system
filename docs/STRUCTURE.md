# Repository Structure

Repository layout with phases 1-4 implemented. Area owners are listed in [`CONTRACTS.md`](CONTRACTS.md) §9.

```
ai-driven-system/
├── README.md                          Root documentation
├── LICENSE                            MIT
├── Makefile                           Build / test / up / down shortcuts               
├── .gitignore
│
├── contracts/                         Machine-readable contracts (JSON Schema 2020-12)
│   ├── README.md
│   ├── raw-event.schema.json          Kafka raw-events value
│   ├── normalized-event.schema.json   Kafka normalized-events value
│   ├── predictive-alert.schema.json   PredictiveAlert
│   ├── incident-playbook.schema.json  IncidentPlaybook
│   └── dependency-graph-seed.schema.json  diagnostic-service/data/dependency_graph.json
│
├── docs/
│   ├── CONTRACTS.md                   Binding integration spec (source of truth)
│   ├── ARCHITECTURE.md                C4 views, runtime sequences, failure modes, scaling
│   ├── adr/                           Architecture Decision Records (0001-…)
│   ├── SLO.md                         Service-level objectives                         
│   ├── runbooks/                      Operational runbooks per alert                   
│   ├── PLAN.md                        Implementation road-map
│   ├── STRUCTURE.md                   (this file)
│   └── sdlc-justification.md          SDLC methodology rationale
│
├── ingestion-service/                 Java 17 / Spring Boot 3 — data path (REQ-A/B/C)
│   ├── pom.xml
│   ├── Dockerfile, .dockerignore
│   └── src/
│       ├── main/java/com/eventproc/ingestion/
│       │   ├── IngestionServiceApplication.java
│       │   ├── config/                Kafka + app properties, error handler / DLT recoverer
│       │   ├── consumer/EventConsumer.java       Kafka listener, manual ack
│       │   ├── exception/SchemaValidationException.java
│       │   ├── kafka/DeadLetters.java            DLT record + x-* headers
│       │   ├── metrics/IngestionMetrics.java     ingestion_events_total, ingestion_processing_seconds
│       │   ├── model/NormalizedEvent.java
│       │   └── service/NormalizationService.java validate + normalise
│       ├── main/resources/application.yml
│       └── test/java/...              Unit + Kafka integration tests                   
│
├── forecasting-service/               Python 3.11 / FastAPI — AIOps (REQ-D/E/F)
│   ├── requirements.txt               Core runtime (no torch)
│   ├── requirements-ml.txt            torch, lightning, pytorch-forecasting (optional)  
│   ├── requirements-dev.txt           pytest, ruff, mypy, respx                        
│   ├── Dockerfile (ARG INSTALL_ML=false), .dockerignore
│   ├── config/components.yaml         Components, PromQL, thresholds (hot-reloaded)    
│   ├── app/
│   │   ├── main.py                    FastAPI app, background forecast loop, routes
│   │   ├── forecasting_engine.py      Forecast + threshold evaluation, PredictiveAlert
│   │   ├── metric_client.py           Prometheus query_range client
│   │   └── …                          forecasters (statistical / TFT), alert store, diagnostic client
│   └── tests/                         Run without ML deps / network                   
│
├── diagnostic-service/                Python 3.11 / FastAPI — AIOps (REQ-G/H/I)
│   ├── requirements.txt
│   ├── requirements-dev.txt                                                             
│   ├── Dockerfile, .dockerignore
│   ├── data/dependency_graph.json     Graph seed (schema: contracts/dependency-graph-seed) 
│   ├── app/
│   │   ├── main.py                    FastAPI app, routes
│   │   ├── diagnostic_engine.py       GraphRAG: context → prompt → LLM / fallback
│   │   ├── dependency_graph_client.py networkx | neo4j backends
│   │   ├── playbook_generator.py      IncidentPlaybook, parsing, render(), file store
│   │   └── …                          LLM providers, rule-based fallback
│   └── tests/                         Run with LLM_PROVIDER=none / mocked LLM         
│
├── dashboard/
│   └── README.md                      REQ-K notes (realised as Grafana, ADR-0006)
│
├── infra/
│   ├── docker-compose.yml             Kafka, ZK, Prometheus, Grafana, services; profiles neo4j, llm
│   ├── prometheus.yml                 Scrape config (adds `service` label per job)
│   ├── prometheus/rules/*.yml         Recording + alert rules → /etc/prometheus/rules
│   ├── grafana/
│   │   ├── provisioning/              Data sources (Prometheus, Infinity), dashboard providers
│   │   └── dashboards/                Dashboard JSON                                   
│   └── k8s/
│       ├── ingestion-deployment.yaml  Namespace, ConfigMap, Deployment, Service
│       └── hpa.yaml                   HorizontalPodAutoscalers
│
├── .github/workflows/ci.yml           Maven verify, ruff/mypy/pytest, docker build, kubeconform
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
| Component ids | lowercase kebab-case, e.g. `graph-db` |
| IDs | `al-<12 hex>` (alerts), `pb-<component>-<8 hex>` (playbooks) |
| Prometheus metrics | Fixed names in CONTRACTS.md §7 |
| ADRs | `docs/adr/NNNN-kebab-title.md`, never renumbered |
| Kubernetes labels | `app.kubernetes.io/name`, `app.kubernetes.io/component` |
| Docker image tags | `ghcr.io/<org>/<service>:<tag>` |

## Port Assignments

| Service | Port |
|---|---|
| ingestion-service | 8080 |
| forecasting-service | 8081 |
| diagnostic-service | 8082 |
| Kafka broker | 9092 (host), `kafka:29092` (in compose) |
| Zookeeper | 2181 |
| Prometheus | 9090 |
| Grafana | 3000 |
| Neo4j (profile `neo4j`) | 7474 / 7687 |
| Ollama (profile `llm`) | 11434 |
