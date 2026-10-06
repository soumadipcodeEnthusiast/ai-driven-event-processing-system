"""
diagnostic-service — FastAPI application entry-point (docs/CONTRACTS.md §2).

  GET  /health, /ready, /metrics          ops
  POST /diagnose/{component_id}           GraphRAG diagnosis → playbook (REQ-G/H/I)
  GET  /playbook/{playbook_id}            JSON, or ?format=markdown (REQ-I)
  GET  /playbooks?alert_id=&component_id= full IncidentPlaybook objects, newest first
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Path, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependency_graph_client import UnknownNodeError
from app.diagnostic_engine import DiagnosticEngine
from app.metrics import DIAGNOSE_RATE_LIMITED, PrometheusMiddleware

logger = logging.getLogger(__name__)


class DiagnoseRequest(BaseModel):
    alert_id: str | None = Field(default=None, pattern=r"^al-[0-9a-f]{12}$")
    anomalies: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class DiagnoseResponse(BaseModel):
    component_id: str
    playbook_id: str
    summary: str
    details: dict[str, Any] = {}


class RateLimiter:
    """Token bucket: ``per_minute`` sustained, bursts up to ``per_minute``; 0 disables.

    /diagnose is unauthenticated and each call may hit a paid LLM and write a
    playbook, so it is throttled service-wide (one replica, see ADR 0005).
    """

    def __init__(self, per_minute: int, clock: Any = time.monotonic) -> None:
        self.capacity = float(max(0, per_minute))
        self._rate = self.capacity / 60.0
        self._tokens = self.capacity
        self._clock = clock
        self._updated = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Take a token. Returns 0 on success, else seconds until one is available."""
        if self.capacity == 0:
            return 0.0
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self._rate)
            self._updated = now
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            return (1 - self._tokens) / self._rate


def create_app(settings: Settings | None = None, engine: DiagnosticEngine | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=settings.log_level.upper())
        eng = engine or DiagnosticEngine(settings)
        application.state.engine = eng
        logger.info("diagnostic-service started")
        try:
            yield
        finally:
            eng.close()
            logger.info("diagnostic-service stopped")

    app = FastAPI(
        title="Diagnostic Service",
        description="GraphRAG diagnostics and incident playbooks (REQ-G, REQ-H, REQ-I)",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(PrometheusMiddleware)
    limiter = RateLimiter(settings.diagnose_rate_limit_per_minute)
    app.state.diagnose_limiter = limiter

    def _engine(request: Request) -> DiagnosticEngine:
        eng: DiagnosticEngine | None = getattr(request.app.state, "engine", None)
        if eng is None:
            raise HTTPException(503, "engine not initialised")
        return eng

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready(request: Request) -> dict[str, str]:
        _engine(request)
        return {"status": "ready"}

    @app.get("/metrics", tags=["ops"])
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/diagnose/{component_id}", response_model=DiagnoseResponse, tags=["diagnostics"])
    def diagnose(
        request: Request,
        component_id: str = Path(..., description="Component identifier to diagnose"),
        body: DiagnoseRequest | None = Body(default=None),
    ) -> DiagnoseResponse:
        """REQ-G/H/I: graph context → LLM (or rule-based fallback) → persisted playbook.

        Idempotent per ``alert_id``: an existing playbook for the alert is returned.
        """
        wait = limiter.acquire()
        if wait > 0:
            DIAGNOSE_RATE_LIMITED.inc()
            raise HTTPException(
                429,
                "rate limit exceeded for /diagnose",
                headers={"Retry-After": str(math.ceil(wait))},
            )
        body = body or DiagnoseRequest()
        try:
            result = _engine(request).run(component_id, body.alert_id, body.anomalies)
        except UnknownNodeError as exc:
            raise HTTPException(404, f"component {component_id!r} not in dependency graph") from exc
        except Exception as exc:
            logger.exception("diagnosis of %s failed", component_id)
            raise HTTPException(500, "diagnosis failed; no playbook produced") from exc
        pb = result.playbook
        return DiagnoseResponse(
            component_id=component_id,
            playbook_id=pb.playbook_id,
            summary=pb.summary,
            details={
                "alert_id": pb.alert_id,
                "outcome": result.outcome,
                "generated_by": pb.generated_by,
                "root_cause_hypothesis": pb.root_cause_hypothesis,
                "steps": pb.to_dict()["steps"],
                **result.details,
            },
        )

    @app.get("/playbook/{playbook_id}", tags=["diagnostics"], response_model=None)
    def get_playbook(
        request: Request,
        playbook_id: str = Path(...),
        format: Literal["json", "markdown"] = Query(default="json"),
    ) -> dict[str, Any] | Response:
        pb = _engine(request).playbooks.get(playbook_id)  # validates the id before disk access
        if pb is None:
            raise HTTPException(404, f"playbook {playbook_id!r} not found")
        if format == "markdown":
            return Response(content=pb.render(), media_type="text/markdown; charset=utf-8")
        return pb.to_dict()

    @app.get("/playbooks", tags=["diagnostics"])
    def list_playbooks(
        request: Request,
        alert_id: str | None = None,
        component_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            p.to_dict()
            for p in _engine(request).playbooks.list(alert_id or None, component_id or None)
        ]

    return app


app = create_app()
