# syntax=docker/dockerfile:1
# forecasting-service — FastAPI (REQ-021, REQ-022, REQ-025)
# Build with the TFT (PyTorch, CPU):   docker build --build-arg INSTALL_ML=true -t forecasting-service ./forecasting-service
# Build without ML deps (Holt only):   docker build -t forecasting-service:slim ./forecasting-service

ARG PYTHON_IMAGE=python:3.11-slim

# ─── Build stage ──────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS builder

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
    && if [ "${INSTALL_ML}" = "true" ]; then \
         pip install -r requirements-ml.txt; \
       fi

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="forecasting-service" \
      org.opencontainers.image.description="Predictive analytics and time-series forecasting (REQ-021 to REQ-025)"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=10001:10001 app/ ./app/
COPY --chown=10001:10001 config/ ./config/
# TFT / ar-ridge training jobs (make train-tft runs them in this image).
COPY --chown=10001:10001 training/ ./training/
# REQ-024 prediction log (PREDICTIONS_PATH) and the mount point for a trained
# model (ML_MODEL_PATH); owned by the app user so named volumes inherit it.
RUN mkdir -p /data /models && chown 10001:10001 /data /models

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8081 \
    COMPONENTS_CONFIG=/app/config/components.yaml

USER 10001:10001

EXPOSE 8081

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081", "--workers", "1"]
