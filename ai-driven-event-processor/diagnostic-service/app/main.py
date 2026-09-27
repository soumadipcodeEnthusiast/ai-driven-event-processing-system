"""
diagnostic-service — FastAPI application entry-point.

Exposes:
  GET  /health                        liveness probe
  GET  /ready                         readiness probe
  POST /diagnose/{component_id}       run full diagnostic cycle (REQ-G, REQ-H, REQ-I)
  GET  /playbook/{playbook_id}        retrieve a rendered playbook (REQ-I)
  GET  /metrics                       Prometheus metrics
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Path, status
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from app.diagnostic_engine import DiagnosticEngine

logger = logging.getLogger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialise resources on startup; tear down on shutdown."""
    logger.info("diagnostic-service starting up")
    application.state.engine = DiagnosticEngine()
    yield
    logger.info("diagnostic-service shutting down")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Diagnostic Service",
    description="GraphRAG root-cause diagnostics and playbook generation — REQ-G, REQ-H, REQ-I",
    version="0.1.0",
    lifespan=lifespan,
)


# ─── Request / Response models ────────────────────────────────────────────────

class DiagnoseResponse(BaseModel):
    component_id: str
    playbook_id: str
    summary: str
    details: dict[str, Any] = {}


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", status_code=status.HTTP_200_OK, tags=["ops"])
def health() -> dict[str, str]:
    """Kubernetes liveness probe."""
    return {"status": "ok"}


@app.get("/ready", status_code=status.HTTP_200_OK, tags=["ops"])
def ready() -> dict[str, str]:
    """Kubernetes readiness probe — checks engine is initialised."""
    engine: DiagnosticEngine = app.state.engine
    if engine is None:
        raise HTTPException(status_code=503, detail="engine not initialised")
    return {"status": "ready"}


@app.post(
    "/diagnose/{component_id}",
    response_model=DiagnoseResponse,
    tags=["diagnostics"],
)
def diagnose(
    component_id: str = Path(..., description="Component identifier to diagnose"),
) -> DiagnoseResponse:
    """
    Execute a full diagnostic cycle: retrieve graph context, build prompt,
    invoke LLM, and persist the resulting playbook.

    REQ-G: Retrieve dependency context for the component from the graph.
    REQ-H: Build a structured prompt and invoke the LLM.
    REQ-I: Persist and return the generated IncidentPlaybook.
    """
    # TODO: implement — REQ-G, REQ-H, REQ-I
    raise NotImplementedError("TODO: implement /diagnose endpoint — REQ-G, REQ-H, REQ-I")


@app.get("/playbook/{playbook_id}", tags=["diagnostics"])
def get_playbook(
    playbook_id: str = Path(..., description="Playbook identifier to retrieve"),
) -> JSONResponse:
    """
    Retrieve and render a previously generated IncidentPlaybook.

    REQ-I: The system shall allow retrieval of any stored playbook by its ID.
    """
    # TODO: implement — REQ-I
    raise NotImplementedError("TODO: implement /playbook endpoint — REQ-I")


@app.get("/metrics", response_class=PlainTextResponse, tags=["ops"])
def metrics() -> str:
    """Expose Prometheus metrics for scraping."""
    return PlainTextResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )
