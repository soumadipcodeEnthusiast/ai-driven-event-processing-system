"""
forecasting-service — FastAPI application entry-point.

Exposes:
  GET  /health          liveness probe
  GET  /ready           readiness probe
  POST /forecast        trigger a forecast cycle (REQ-D, REQ-E, REQ-F)
  GET  /metrics         Prometheus metrics
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from app.forecasting_engine import ForecastingEngine

logger = logging.getLogger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialise resources on startup; tear down on shutdown."""
    logger.info("forecasting-service starting up")
    application.state.engine = ForecastingEngine()
    yield
    logger.info("forecasting-service shutting down")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Forecasting Service",
    description="TFT-based metric forecasting — REQ-D, REQ-E, REQ-F",
    version="0.1.0",
    lifespan=lifespan,
)


# ─── Request / Response models ────────────────────────────────────────────────

class ForecastRequest(BaseModel):
    component_id: str
    forecast_window_minutes: int = 60


class ForecastResponse(BaseModel):
    component_id: str
    alert_emitted: bool
    details: dict[str, Any] = {}


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", status_code=status.HTTP_200_OK, tags=["ops"])
def health() -> dict[str, str]:
    """Kubernetes liveness probe — always returns 200 if process is alive."""
    return {"status": "ok"}


@app.get("/ready", status_code=status.HTTP_200_OK, tags=["ops"])
def ready() -> dict[str, str]:
    """Kubernetes readiness probe — checks engine is initialised."""
    engine: ForecastingEngine = app.state.engine
    if engine is None:
        raise HTTPException(status_code=503, detail="engine not initialised")
    return {"status": "ready"}


@app.post("/forecast", response_model=ForecastResponse, tags=["forecasting"])
def forecast(request: ForecastRequest) -> ForecastResponse:
    """
    Trigger a forecasting cycle for the given component.

    REQ-D: Poll Prometheus metrics for the component.
    REQ-E: Run TFT inference and evaluate threshold.
    REQ-F: Emit a PredictiveAlert if a breach is forecast.
    """
    # TODO: implement — delegate to ForecastingEngine (REQ-D, REQ-E, REQ-F)
    raise NotImplementedError("TODO: implement /forecast endpoint — REQ-D, REQ-E, REQ-F")


@app.get("/metrics", response_class=PlainTextResponse, tags=["ops"])
def metrics() -> str:
    """Expose Prometheus metrics for scraping."""
    return PlainTextResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )
