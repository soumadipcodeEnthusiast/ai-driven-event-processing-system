# syntax=docker/dockerfile:1
# diagnostic-service — FastAPI anomaly detection (REQ-016 to REQ-020)
# Build: docker build -t diagnostic-service ./diagnostic-service

ARG PYTHON_IMAGE=python:3.11-slim

# ─── Build stage ──────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build
COPY requirements*.txt ./

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="diagnostic-service" \
      org.opencontainers.image.description="Event anomaly detection (REQ-016 to REQ-020) and GraphRAG root-cause analysis"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# pyproject.toml is tool config only (ruff/mypy/pytest) — not needed at runtime.
COPY --chown=10001:10001 app/ ./app/
# Dependency-graph seed: loads an empty Neo4j and is the in-memory fallback.
COPY --chown=10001:10001 data/ ./data/
# Playbook store (PLAYBOOK_STORE_DIR); owned by the app user so a named volume inherits it.
RUN mkdir -p /data/playbooks && chown -R 10001:10001 /data

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8082 \
    MODEL_VERSION=unset \
    ANOMALY_THRESHOLD=0.8 \
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    ANOMALY_AUDIT_TOPIC=anomaly-audit

USER 10001:10001

EXPOSE 8082

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082", "--workers", "1"]
