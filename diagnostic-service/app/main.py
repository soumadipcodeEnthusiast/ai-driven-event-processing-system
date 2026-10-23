"""
diagnostic-service — FastAPI application entry-point.

Anomaly detection (SRS v1.0 §4.4) and GraphRAG root-cause analysis (docs/adr/0010).

  GET  /health, /ready, /metrics          ops (unauthenticated)
  POST /score/{event_id}                  score; alert + audit if anomalous (REQ-016/017/019)
  GET  /thresholds                        read the current detection threshold
  PUT  /thresholds                        adjust the detection threshold at runtime (REQ-020)
  GET  /model                             active scoring model version
  PUT  /model                             swap the scoring model without downtime (REQ-018)
  POST /diagnose/{component_id}           GraphRAG: Neo4j 2-hop context → LLM → playbook
  GET  /playbook/{playbook_id}            JSON, or ?format=markdown
  GET  /playbooks?alert_id=&component_id= playbooks, newest first
  GET  /graph/{component_id}              the dependency context GraphRAG retrieves
  GET  /incidents                         recent automatic diagnoses (anomaly-triggered)

When ``EVENT_CONSUMER_ENABLED=true`` every event on ``normalized-events`` is
also scored (app/event_consumer.py), so REQ-016 holds for each processed
event, not only those posted here.

Every anomalous event is also handed to app/incident_trigger.py, which runs
a GraphRAG diagnosis for its component (batched, with a cooldown) and
publishes the playbook to the ``incident-playbooks`` topic. forecasting-service
calls ``POST /diagnose`` for each new predictive alert.

REQ-046: every route below "ops" requires a valid OAuth 2.0 bearer token
when ``OAUTH2_ENABLED=true`` (app/auth.py).
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
from fastapi.responses import RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from app.anomaly_scorer import AnomalyScorer
from app.audit_publisher import AuditPublisher
from app.auth import Claims, subject
from app.config import Settings, get_settings
from app.dependency_graph_client import UnknownNodeError
from app.detector import AnomalyDetector
from app.diagnostic_engine import DiagnosticEngine
from app.event_consumer import EventStreamScorer
from app.incident_trigger import IncidentTrigger, PlaybookPublisher, anomaly_context
from app.metrics import DIAGNOSE_RATE_LIMITED, PrometheusMiddleware, set_model_info
from app.threshold_manager import ThresholdManager

logger = logging.getLogger(__name__)


class ScoreRequest(BaseModel):
    event: dict[str, Any]


class ScoreResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    event_id: str
    score: float
    model_version: str
    threshold: float
    anomalous: bool
    audited: bool
    reason: str | None = None


class ThresholdUpdateRequest(BaseModel):
    threshold: float = Field(ge=0.0, le=1.0)


class ModelSwapRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_version: str = Field(min_length=1, max_length=128)
    warm_start: bool = True


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

    Each /diagnose call may run an LLM and writes a playbook, so it is
    throttled service-wide.
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


def create_app(
    settings: Settings | None = None,
    scorer: AnomalyScorer | None = None,
    thresholds: ThresholdManager | None = None,
    audit: AuditPublisher | None = None,
    stream_scorer: EventStreamScorer | None = None,
    engine: DiagnosticEngine | None = None,
    trigger: IncidentTrigger | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=settings.log_level.upper())
        detector = AnomalyDetector(
            scorer
            or AnomalyScorer(
                settings.model_version,
                alpha=settings.scorer_alpha,
                min_samples=settings.scorer_min_samples,
            ),
            thresholds or ThresholdManager(settings.anomaly_threshold),
            audit or AuditPublisher(settings.kafka_bootstrap_servers, settings.anomaly_audit_topic),
        )
        application.state.detector = detector
        set_model_info(detector.scorer.model_version)

        eng = engine or DiagnosticEngine(settings)
        application.state.engine = eng
        incidents = trigger
        if incidents is None and settings.incident_auto_diagnose:
            incidents = IncidentTrigger(
                eng,
                PlaybookPublisher(
                    settings.kafka_bootstrap_servers, settings.incident_playbook_topic
                ),
                cooldown_seconds=settings.incident_cooldown_seconds,
                batch_seconds=settings.incident_batch_seconds,
            )
        if incidents is not None:
            application.state.incidents = incidents
            detector.on_anomaly = lambda event, score, threshold, reason: incidents.submit(
                event, anomaly_context(event, score, threshold, reason)
            )
            incidents.start()

        consumer = stream_scorer
        if consumer is None and settings.event_consumer_enabled:
            consumer = EventStreamScorer(
                detector,
                settings.kafka_bootstrap_servers,
                settings.event_input_topic,
                settings.event_consumer_group,
            )
        if consumer is not None:
            consumer.detector = detector
            consumer.start()
        logger.info("diagnostic-service started (model_version=%s)", detector.scorer.model_version)
        try:
            yield
        finally:
            if consumer is not None:
                consumer.stop()
            if incidents is not None:
                incidents.stop()
            detector.audit.close()
            eng.close()
            logger.info("diagnostic-service stopped")

    app = FastAPI(
        title="Diagnostic Service",
        description="Event anomaly detection (REQ-016 to REQ-020) and GraphRAG root-cause "
        "analysis: Neo4j dependency context + LLM → incident playbooks",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(PrometheusMiddleware)
    limiter = RateLimiter(settings.diagnose_rate_limit_per_minute)
    app.state.diagnose_limiter = limiter

    def _engine(request: Request) -> DiagnosticEngine:
        eng: DiagnosticEngine | None = getattr(request.app.state, "engine", None)
        if eng is None:
            raise HTTPException(503, "engine not initialised")
        return eng

    def _detector(request: Request) -> AnomalyDetector:
        detector: AnomalyDetector | None = getattr(request.app.state, "detector", None)
        if detector is None:
            raise HTTPException(503, "service not initialised")
        return detector

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        """The bare service URL opens the interactive API docs."""
        return RedirectResponse("/docs")

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready(request: Request) -> dict[str, str]:
        _detector(request)
        return {"status": "ready"}

    @app.get("/metrics", tags=["ops"])
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/score/{event_id}", response_model=ScoreResponse, tags=["anomaly"])
    def score(
        request: Request,
        claims: Claims,
        event_id: str = Path(..., min_length=1),
        body: ScoreRequest = Body(...),
    ) -> ScoreResponse:
        """REQ-016/017/019: score an event, evaluate it against the threshold,
        and publish an audit record if it is anomalous.

        The path ``event_id`` wins over any ``event_id`` inside the body.
        """
        event = {**body.event, "event_id": event_id}
        d = _detector(request).process(event, source="http")
        return ScoreResponse(
            event_id=d.event_id,
            score=d.score,
            model_version=d.model_version,
            threshold=d.threshold,
            anomalous=d.anomalous,
            audited=d.audited,
            reason=d.reason,
        )

    @app.get("/thresholds", tags=["anomaly"])
    def get_threshold(request: Request, claims: Claims) -> dict[str, float]:
        return {"threshold": _detector(request).thresholds.threshold}

    @app.put("/thresholds", tags=["anomaly"])
    def update_threshold(
        request: Request, body: ThresholdUpdateRequest, claims: Claims
    ) -> dict[str, float]:
        """REQ-020: administrators adjust the detection threshold without a redeploy."""
        thresholds = _detector(request).thresholds
        try:
            thresholds.set_threshold(body.threshold, changed_by=subject(claims))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"threshold": thresholds.threshold}

    @app.get("/model", tags=["anomaly"])
    def get_model(request: Request, claims: Claims) -> dict[str, Any]:
        scorer = _detector(request).scorer
        return {"model_version": scorer.model_version, "streams": scorer.stream_count}

    @app.put("/model", tags=["anomaly"])
    def swap_model(request: Request, body: ModelSwapRequest, claims: Claims) -> dict[str, Any]:
        """REQ-018: rotate the scoring model while requests keep being served."""
        scorer = _detector(request).swap_model(body.model_version, warm_start=body.warm_start)
        set_model_info(scorer.model_version)
        logger.info("event=model_swap_requested by=%s", subject(claims) or "-")
        return {"model_version": scorer.model_version, "streams": scorer.stream_count}

    # ── GraphRAG root-cause analysis ──────────────────────────────────────

    @app.post("/diagnose/{component_id}", response_model=DiagnoseResponse, tags=["graphrag"])
    def diagnose(
        request: Request,
        claims: Claims,
        component_id: str = Path(..., description="Component to diagnose"),
        body: DiagnoseRequest | None = Body(default=None),
    ) -> DiagnoseResponse:
        """Neo4j 2-hop context → LLM (or rule-based fallback) → stored playbook.

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

    @app.get("/playbook/{playbook_id}", tags=["graphrag"], response_model=None)
    def get_playbook(
        request: Request,
        claims: Claims,
        playbook_id: str = Path(...),
        format: Literal["json", "markdown"] = Query(default="json"),
    ) -> dict[str, Any] | Response:
        pb = _engine(request).playbooks.get(playbook_id)  # validates the id before disk access
        if pb is None:
            raise HTTPException(404, f"playbook {playbook_id!r} not found")
        if format == "markdown":
            return Response(content=pb.render(), media_type="text/markdown; charset=utf-8")
        return pb.to_dict()

    @app.get("/playbooks", tags=["graphrag"])
    def list_playbooks(
        request: Request,
        claims: Claims,
        alert_id: str | None = None,
        component_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        pbs = _engine(request).playbooks.list(alert_id or None, component_id or None)
        return [p.to_dict() for p in pbs[:limit]]

    @app.get("/graph/{component_id}", tags=["graphrag"])
    def graph_context(
        request: Request, claims: Claims, component_id: str = Path(...)
    ) -> dict[str, Any]:
        """The retrieval half of GraphRAG: the component and its 2-hop neighbourhood."""
        eng = _engine(request)
        try:
            context = eng.retrieve_context(component_id)
        except UnknownNodeError as exc:
            raise HTTPException(404, f"component {component_id!r} not in dependency graph") from exc
        return {"backend": eng.graph.backend, **context}

    @app.get("/incidents", tags=["graphrag"])
    def incidents(request: Request, claims: Claims) -> dict[str, Any]:
        trig: IncidentTrigger | None = getattr(request.app.state, "incidents", None)
        eng = _engine(request)
        return {
            "auto_diagnose": trig is not None,
            "graph_backend": eng.graph.backend,
            "llm": eng.llm.generated_by if eng.llm else "none (rule-based)",
            "recent": list(trig.recent) if trig else [],
        }

    return app


app = create_app()
