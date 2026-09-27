# Repository Structure

```
ai-driven-event-processor/
├── README.md                          Root documentation
├── LICENSE                            MIT
├── .gitignore
├── docs/
│   ├── STRUCTURE.md                   (this file)
│   ├── sdlc-justification.md          SDLC methodology rationale
│   └── PLAN.md                        Implementation road-map
│
├── ingestion-service/                 Java 17 / Spring Boot 3 — synchronous path
│   ├── pom.xml                        Maven project descriptor
│   ├── Dockerfile
│   └── src/
│       ├── main/
│       │   ├── java/com/eventproc/ingestion/
│       │   │   ├── IngestionServiceApplication.java   Spring Boot entry-point
│       │   │   ├── consumer/
│       │   │   │   └── EventConsumer.java             Kafka listener (REQ-A, REQ-B)
│       │   │   ├── model/
│       │   │   │   └── NormalizedEvent.java           Domain model
│       │   │   └── service/
│       │   │       └── NormalizationService.java      Validation + normalisation (REQ-B, REQ-C)
│       │   └── resources/
│       │       └── application.yml                    Spring + Kafka configuration
│
├── forecasting-service/               Python 3.11 / FastAPI — async AIOps path
│   ├── requirements.txt
│   ├── Dockerfile
│   └── app/
│       ├── main.py                    FastAPI app + lifespan
│       ├── forecasting_engine.py      TFT engine stub (REQ-D, REQ-E, REQ-F)
│       └── metric_client.py           Prometheus polling stub
│
├── diagnostic-service/                Python 3.11 / FastAPI — async AIOps path
│   ├── requirements.txt
│   ├── Dockerfile
│   └── app/
│       ├── main.py                    FastAPI app + lifespan
│       ├── diagnostic_engine.py       GraphRAG engine stub (REQ-G, REQ-H, REQ-I)
│       ├── dependency_graph_client.py Graph query stub (REQ-G)
│       └── playbook_generator.py      Playbook render stub (REQ-H, REQ-I)
│
├── dashboard/
│   └── README.md                      Dashboard design notes (REQ-K)
│
├── infra/
│   ├── docker-compose.yml             Full local stack (Kafka, ZK, Prometheus, services)
│   ├── prometheus.yml                 Prometheus scrape config
│   └── k8s/
│       ├── ingestion-deployment.yaml  Kubernetes Deployment + Service
│       └── hpa.yaml                   HorizontalPodAutoscaler
│
├── .github/
│   └── workflows/
│       └── ci.yml                     GitHub Actions CI pipeline
│
└── scripts/
    └── setup-local.sh                 Bootstrap helper
```

## Naming Conventions

| Layer | Convention |
|---|---|
| Java packages | `com.eventproc.<service>.<layer>` |
| Python modules | `snake_case` |
| Kubernetes labels | `app.kubernetes.io/name`, `app.kubernetes.io/component` |
| Docker image tags | `ghcr.io/<org>/<service>:latest` |

## Port Assignments

| Service | Internal Port | Docker host Port |
|---|---|---|
| ingestion-service | 8080 | 8080 |
| forecasting-service | 8081 | 8081 |
| diagnostic-service | 8082 | 8082 |
| Kafka broker | 9092 | 9092 |
| Zookeeper | 2181 | 2181 |
| Prometheus | 9090 | 9090 |
