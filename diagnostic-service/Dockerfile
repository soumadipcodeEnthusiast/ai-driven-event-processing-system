# syntax=docker/dockerfile:1
# diagnostic-service — FastAPI GraphRAG diagnostics + playbooks (REQ-G, REQ-H, REQ-I)
# Build: docker build -t diagnostic-service ./diagnostic-service

ARG PYTHON_IMAGE=python:3.11-slim

# ─── Build stage ──────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS builder

# Accepted for parity with forecasting-service (docs/CONTRACTS.md §6);
# only installs anything if a requirements-ml.txt exists.
ARG INSTALL_ML=false

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
    && pip install -r requirements.txt \
    && if [ "${INSTALL_ML}" = "true" ] && [ -f requirements-ml.txt ]; then \
         pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-ml.txt; \
       fi

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="diagnostic-service" \
      org.opencontainers.image.description="GraphRAG diagnostics and playbook generation (REQ-G, REQ-H, REQ-I)"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/playbooks \
    && chown -R 10001:10001 /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# app/ carries app/templates/ (Jinja2 prompt/playbook templates); copied
# explicitly as well so the image fails to build if templates go missing.
# pyproject.toml is tool config only (ruff/mypy/pytest) — not needed at runtime.
COPY --chown=10001:10001 app/ ./app/
COPY --chown=10001:10001 app/templates/ ./app/templates/
COPY --chown=10001:10001 data/ ./data/

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8082 \
    GRAPH_BACKEND=networkx \
    GRAPH_SEED_PATH=/app/data/dependency_graph.json \
    PLAYBOOK_STORE_DIR=/data/playbooks

VOLUME ["/data/playbooks"]

USER 10001:10001

EXPOSE 8082

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082", "--workers", "1"]
