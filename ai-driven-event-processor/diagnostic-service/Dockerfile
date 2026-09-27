# ─── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt

# ─── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="diagnostic-service" \
      org.opencontainers.image.description="GraphRAG diagnostics and playbook generation — REQ-G, REQ-H, REQ-I"

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app/ ./app/

RUN chown -R appuser:appgroup /app

USER appuser

EXPOSE 8082

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODULE_NAME=app.main \
    PORT=8082

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082", "--workers", "1"]
