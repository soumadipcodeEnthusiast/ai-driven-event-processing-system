"""
forecasting-service — FastAPI application entry-point (docs/CONTRACTS.md §2).

  GET  /health, /ready, /metrics             ops
  POST /forecast                             run a forecast cycle (REQ-D, REQ-E)
  GET  /alerts, /alerts/{alert_id}           predictive alerts
  GET  /alerts/summary                       live counts + minutes to the soonest breach
  POST /alerts/{alert_id}/ack|resolve        status transitions
  GET  /thresholds                           effective thresholds
  PUT  /thresholds/{component_id}            runtime override (REQ-F)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app.alerts import AlertStatus, InvalidTransitionError
from app.components import UnknownComponentError, UnknownMetricError
from app.config import Settings, get_settings
from app.forecasting_engine import ForecastCycleError, ForecastingEngine
from app.metrics import PrometheusMiddleware

logger = logging.getLogger(__name__)


class ForecastRequest(BaseModel):
    component_id: str = Field(min_length=1)
    forecast_window_minutes: int = Field(default=60, ge=1, le=24 * 60)


class ForecastResponse(BaseModel):
    component_id: str
    alert_emitted: bool
    alert: dict[str, Any] | None = None
    details: dict[str, Any] = {}


async def _forecast_loop(engine: ForecastingEngine, interval: float) -> None:
    """REQ-D: forecast every configured component every FORECAST_INTERVAL_SECONDS."""
    while True:
        try:
            await asyncio.to_thread(engine.run_all)
        except Exception:
            logger.exception("background forecast iteration failed")
        await asyncio.sleep(interval)


def create_app(
    settings: Settings | None = None, engine: ForecastingEngine | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=settings.log_level.upper())
        eng = engine or ForecastingEngine(settings)
        application.state.engine = eng
        task: asyncio.Task[None] | None = None
        if settings.forecast_interval_seconds > 0:
            task = asyncio.create_task(_forecast_loop(eng, settings.forecast_interval_seconds))
        logger.info("forecasting-service started")
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            eng.close()
            logger.info("forecasting-service stopped")

    app = FastAPI(
        title="Forecasting Service",
        description="Metric forecasting and predictive alerting (REQ-D, REQ-E, REQ-F)",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.add_middleware(PrometheusMiddleware)

    def _engine(request: Request) -> ForecastingEngine:
        eng: ForecastingEngine | None = getattr(request.app.state, "engine", None)
        if eng is None:
            raise HTTPException(status_code=503, detail="engine not initialised")
        return eng

    # ── ops ────────────────────────────────────────────────────────────────

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready(request: Request) -> dict[str, str]:
        if not _engine(request).ready():
            raise HTTPException(status_code=503, detail="components config not loaded")
        return {"status": "ready"}

    @app.get("/metrics", tags=["ops"])
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ── forecasting ────────────────────────────────────────────────────────

    @app.post("/forecast", response_model=ForecastResponse, tags=["forecasting"])
    def forecast(body: ForecastRequest, request: Request) -> dict[str, Any]:
        eng = _engine(request)
        try:
            return eng.run_cycle(body.component_id, body.forecast_window_minutes)
        except UnknownComponentError as exc:
            raise HTTPException(404, f"unknown component: {body.component_id}") from exc
        except ForecastCycleError as exc:
            raise HTTPException(503, str(exc)) from exc

    # ── alerts ─────────────────────────────────────────────────────────────

    @app.get("/alerts", tags=["alerts"])
    def list_alerts(
        request: Request,
        status_: AlertStatus | None = Query(default=None, alias="status"),
        component_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        alerts = _engine(request).alerts.list(status_, component_id, limit)
        return [a.to_dict() for a in alerts]

    @app.get("/alerts/summary", tags=["alerts"])
    def alerts_summary(request: Request) -> dict[str, Any]:
        """Live view of active (open + acknowledged) alerts for the REQ-K dashboard.

        ``min_minutes_to_breach`` is computed now from ``predicted_breach_time``
        (negative = predicted breach time has passed), unlike the alert's
        ``lead_time_minutes`` which is frozen at creation.
        """
        eng = _engine(request)
        active = [
            a for a in eng.alerts.list(limit=1_000_000) if a.status is not AlertStatus.RESOLVED
        ]
        now = eng.now()
        soonest = min(active, key=lambda a: a.predicted_breach_time, default=None)
        by_severity = {s: 0 for s in ("low", "medium", "high", "critical")}
        for a in active:
            by_severity[a.severity.value] += 1
        return {
            "active": len(active),
            "open": sum(a.status is AlertStatus.OPEN for a in active),
            "acknowledged": sum(a.status is AlertStatus.ACKNOWLEDGED for a in active),
            "by_severity": by_severity,
            "min_minutes_to_breach": (
                None
                if soonest is None
                else round((soonest.predicted_breach_time - now).total_seconds() / 60.0, 2)
            ),
            "next_breach_alert_id": soonest.alert_id if soonest else None,
            "next_breach_component": soonest.component_id if soonest else None,
        }

    @app.get("/alerts/{alert_id}", tags=["alerts"])
    def get_alert(alert_id: str, request: Request) -> dict[str, Any]:
        alert = _engine(request).alerts.get(alert_id)
        if alert is None:
            raise HTTPException(404, f"alert {alert_id} not found")
        return alert.to_dict()

    def _transition(request: Request, alert_id: str, target: AlertStatus) -> dict[str, Any]:
        try:
            alert = _engine(request).alerts.transition(alert_id, target)
        except InvalidTransitionError as exc:
            raise HTTPException(409, str(exc)) from exc
        if alert is None:
            raise HTTPException(404, f"alert {alert_id} not found")
        return alert.to_dict()

    @app.post("/alerts/{alert_id}/ack", tags=["alerts"])
    def ack_alert(alert_id: str, request: Request) -> dict[str, Any]:
        return _transition(request, alert_id, AlertStatus.ACKNOWLEDGED)

    @app.post("/alerts/{alert_id}/resolve", tags=["alerts"])
    def resolve_alert(alert_id: str, request: Request) -> dict[str, Any]:
        return _transition(request, alert_id, AlertStatus.RESOLVED)

    # ── thresholds (REQ-F) ─────────────────────────────────────────────────

    @app.get("/thresholds", tags=["thresholds"])
    def get_thresholds(request: Request) -> dict[str, dict[str, float]]:
        return _engine(request).config.thresholds()

    @app.put("/thresholds/{component_id}", tags=["thresholds"])
    def put_thresholds(
        component_id: str,
        request: Request,
        body: dict[str, float] = Body(...),
    ) -> dict[str, float]:
        if not body:
            raise HTTPException(422, "body must map at least one metric name to a threshold")
        try:
            return _engine(request).config.set_thresholds(component_id, body)
        except UnknownComponentError as exc:
            raise HTTPException(404, f"unknown component: {component_id}") from exc
        except UnknownMetricError as exc:
            raise HTTPException(
                422, f"unknown metric(s) for {component_id}: {exc.args[0]}"
            ) from exc

    return app


app = create_app()

__all__ = ["app", "create_app"]
