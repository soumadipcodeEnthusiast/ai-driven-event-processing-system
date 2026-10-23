# syntax=docker/dockerfile:1
# event-archiver — Kafka → S3 archival (REQ-028)
# Build: docker build -t event-archiver ./storage

ARG PYTHON_IMAGE=python:3.11-slim

# ─── Build stage ──────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="event-archiver" \
      org.opencontainers.image.description="Archives raw and normalized events to Amazon S3 (REQ-028)"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# Imported as the top-level package `storage` (python -m storage.archiver).
COPY --chown=10001:10001 __init__.py archiver.py ./storage/
COPY --chown=10001:10001 archive_client/ ./storage/archive_client/

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    METRICS_PORT=8083 \
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092 \
    ARCHIVE_TOPICS=raw-events,normalized-events

USER 10001:10001

EXPOSE 8083

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8083/metrics', timeout=3)"]

CMD ["python", "-m", "storage.archiver"]
