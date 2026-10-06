"""
forecasting_engine.py — metric forecasting and predictive alerting.

Requirements:
    REQ-D — Poll Prometheus metrics and run probabilistic forecasting
            (TFT when available, damped-trend Holt otherwise).
    REQ-E — Emit a PredictiveAlert at least ALERT_MIN_LEAD_MINUTES before a
            predicted (p90) breach.
    REQ-F — Thresholds configurable without redeployment (components.yaml
            hot reload + PUT /thresholds).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from app.alerts import (
    AlertStore,
    PredictiveAlert,
    new_alert_id,
    overshoot_ratio,
    severity_for,
    severity_rank,
    utc_iso,
)
from app.components import ComponentConfigStore
from app.config import Settings, resolve_path
from app.diagnostic_client import DiagnosticClient
from app.forecasters import Forecaster, ForecasterError, build_forecaster
from app.metric_client import MetricClient, MetricClientError, parse_duration_seconds
from app.metrics import FORECAST_LATENCY, FORECAST_RUNS, set_forecaster_info

logger = logging.getLogger(__name__)


class ForecastCycleError(RuntimeError):
    """The forecast cycle could not run (e.g. Prometheus unreachable)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ForecastingEngine:
    """
    Orchestrates metric polling, forecasting and threshold evaluation.

    Class-diagram attributes:
        model_version           — identifier of the active forecaster/model artefact
        forecast_window_minutes — default look-ahead window
    """

    def __init__(
        self,
        settings: Settings,
        *,
        metric_client: MetricClient | None = None,
        config_store: ComponentConfigStore | None = None,
        forecaster: Forecaster | None = None,
        alert_store: AlertStore | None = None,
        diagnostic_client: DiagnosticClient | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.settings = settings
        self.forecast_window_minutes: int = settings.forecast_window_minutes
        self.step_seconds: float = parse_duration_seconds(settings.prometheus_step)
        self._clock = clock

        self.metric_client = metric_client or MetricClient(
            settings.prometheus_url,
            default_step=settings.prometheus_step,
            timeout=settings.prometheus_timeout_seconds,
        )
        self.config = config_store or ComponentConfigStore(resolve_path(settings.components_config))
        self.forecaster = forecaster or build_forecaster(
            settings.forecaster, settings.tft_model_path, settings.tft_model_version
        )
        self.model_version: str = (
            self.forecaster.model_version
            if self.forecaster.name == "tft"
            else f"statistical-{self.forecaster.model_version}"
        )
        set_forecaster_info(self.forecaster.name, self.model_version)

        self.alerts = alert_store or AlertStore()
        self.diagnostic = diagnostic_client or DiagnosticClient(
            settings.diagnostic_url,
            timeout=settings.diagnostic_timeout_seconds,
            attempts=settings.diagnostic_retries,
            backoff_seconds=settings.diagnostic_backoff_seconds,
        )
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="diag-handoff")
        self._pending: list[Future[None]] = []
        self._pending_lock = threading.Lock()
        logger.info(
            "ForecastingEngine initialised (forecaster=%s, model_version=%s, window=%dm)",
            self.forecaster.name,
            self.model_version,
            self.forecast_window_minutes,
        )

    # ── REQ-D: polling ─────────────────────────────────────────────────────

    def _poll(
        self, component_id: str, history_minutes: int | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        comp = self.config.get(component_id)
        end = self._clock()
        start = end - timedelta(minutes=history_minutes or comp.history_minutes)
        observations: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        for name, spec in comp.metrics.items():
            try:
                result = self.metric_client.query_range(
                    spec.query, start, end, self.settings.prometheus_step
                )
            except MetricClientError as exc:
                errors[name] = str(exc)
                continue
            # several series → per-timestamp max (worst instance)
            merged: dict[float, float] = {}
            for series in result:
                for ts, raw in series.get("values", []):
                    try:
                        v = float(raw)
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(v):
                        continue
                    t = float(ts)
                    merged[t] = max(v, merged.get(t, -math.inf))
            observations.extend(
                {"metric_name": name, "timestamp": t, "value": v} for t, v in sorted(merged.items())
            )
        if comp.metrics and len(errors) == len(comp.metrics):
            raise ForecastCycleError(
                f"all metric queries failed for {component_id}: {next(iter(errors.values()))}"
            )
        return observations, errors

    def now(self) -> datetime:
        """Current UTC time from the engine's (injectable) clock."""
        return self._clock()

    def poll_metrics(
        self, component_id: str, history_minutes: int | None = None
    ) -> list[dict[str, Any]]:
        """
        Poll Prometheus for the configured metrics of *component_id*.

        REQ-D: retrieves CPU utilisation, memory utilisation and request
        error rate (as configured in components.yaml) over the component's
        history window.

        Returns:
            List of ``{"metric_name", "timestamp" (unix s), "value"}`` dicts.

        Raises:
            UnknownComponentError: component not configured.
            ForecastCycleError: every query failed (Prometheus unreachable).
        """
        return self._poll(component_id, history_minutes)[0]

    # ── REQ-D/E: forecasting ───────────────────────────────────────────────

    def _regularise(self, ts: np.ndarray, vals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Interpolate onto an evenly spaced grid (fills scrape gaps)."""
        step = self.step_seconds
        grid = np.arange(ts[0], ts[-1] + step / 2, step)
        return grid, np.interp(grid, ts, vals)

    def forecast(
        self, metrics: list[dict[str, Any]], horizon_minutes: int | None = None
    ) -> dict[str, Any]:
        """
        Run the forecaster over each metric series.

        REQ-E: each forecast contains point estimates and the 90th-percentile
        upper band for every step of the horizon.

        Returns:
            ``{metric_name: {"status": "ok", "timestamps": [...], "point": [...],
            "upper_90": [...], "last_value", "last_timestamp"}}`` or
            ``{"status": "insufficient_data" | "error", ...}`` per metric.
        """
        horizon_minutes = horizon_minutes or self.forecast_window_minutes
        horizon = max(1, math.ceil(horizon_minutes * 60 / self.step_seconds))
        by_metric: dict[str, list[tuple[float, float]]] = {}
        for obs in metrics:
            by_metric.setdefault(obs["metric_name"], []).append(
                (float(obs["timestamp"]), float(obs["value"]))
            )
        out: dict[str, Any] = {}
        for name, points in by_metric.items():
            points.sort()
            ts = np.array([p[0] for p in points])
            vals = np.array([p[1] for p in points])
            ts, vals = self._regularise(ts, vals)
            try:
                fc = self.forecaster.predict(
                    vals,
                    horizon,
                    timestamps=ts,
                    step_seconds=self.step_seconds,
                    metric_name=name,
                )
            except ForecasterError as exc:
                out[name] = {"status": "insufficient_data", "points": len(vals), "reason": str(exc)}
                continue
            future_ts = ts[-1] + self.step_seconds * np.arange(1, horizon + 1)
            out[name] = {
                "status": "ok",
                "timestamps": future_ts.tolist(),
                "point": fc.point.tolist(),
                "upper_90": fc.upper_90.tolist(),
                "last_value": float(vals[-1]),
                "last_timestamp": float(ts[-1]),
                "points": len(vals),
                "forecaster": fc.forecaster,
            }
        return out

    # ── REQ-E/F: threshold evaluation ──────────────────────────────────────

    def evaluate_thresholds(
        self, component_id: str, forecast_result: dict[str, Any]
    ) -> tuple[list[PredictiveAlert], dict[str, dict[str, Any]]]:
        """Evaluate every metric; returns (newly created alerts, per-metric summary)."""
        comp = self.config.get(component_id)  # thresholds read fresh (hot reload)
        now = self._clock()
        min_lead = self.settings.alert_min_lead_minutes
        created: list[PredictiveAlert] = []
        summary: dict[str, dict[str, Any]] = {}

        for name, fc in forecast_result.items():
            spec = comp.metrics.get(name)
            if spec is None:
                continue
            info: dict[str, Any] = {"threshold": spec.threshold, "status": fc.get("status")}
            summary[name] = info
            if fc.get("status") != "ok":
                info["outcome"] = fc.get("status")
                continue
            upper = np.asarray(fc["upper_90"], dtype=float)
            point = np.asarray(fc["point"], dtype=float)
            info.update(
                last_value=round(fc["last_value"], 6),
                max_point=round(float(point.max()), 6),
                max_upper_90=round(float(upper.max()), 6),
            )
            crossing = np.nonzero(upper > spec.threshold)[0]
            if crossing.size == 0:
                info["outcome"] = "no_breach"
                continue
            # Breach time = first p90 crossing; a metric already over the
            # threshold has lead 0. Severity uses the *peak* p90 in the horizon,
            # so it reflects how bad the breach gets, not how it starts.
            idx = int(crossing[0])
            breach_time = datetime.fromtimestamp(fc["timestamps"][idx], tz=timezone.utc)
            if fc["last_value"] > spec.threshold:
                breach_time = now  # already breaching
            value = float(upper.max())
            lead = max(0.0, (breach_time - now).total_seconds() / 60.0)
            ratio = overshoot_ratio(value, spec.threshold)
            info.update(
                predicted_breach_time=utc_iso(breach_time),
                predicted_value=round(value, 6),
                lead_time_minutes=round(lead, 2),
                overshoot=None if math.isinf(ratio) else round(ratio, 4),
                late=lead < min_lead,
            )
            alert = PredictiveAlert(
                alert_id=new_alert_id(),
                component_id=component_id,
                metric_name=name,
                predicted_breach_time=breach_time,
                predicted_value=value,
                threshold=spec.threshold,
                lead_time_minutes=lead,
                severity=severity_for(value, spec.threshold),
                created_at=now,
                model_version=self.model_version,
            )
            if self.alerts.add_if_absent(alert):
                info["outcome"] = "alert_created"
                info["alert_id"] = alert.alert_id
                created.append(alert)
                logger.info(
                    "predictive alert %s: %s/%s %s breach at %s (lead %.1f min%s)",
                    alert.alert_id,
                    component_id,
                    name,
                    alert.severity.value,
                    info["predicted_breach_time"],
                    lead,
                    ", below target lead time" if lead < min_lead else "",
                )
            else:
                existing = self.alerts.active_for(component_id, name)
                info["outcome"] = "deduplicated"
                info["alert_id"] = existing.alert_id if existing else None
        return created, summary

    def evaluate_threshold(
        self, component_id: str, forecast_result: dict[str, Any]
    ) -> PredictiveAlert | None:
        """
        Compare forecast p90 bands against the (hot-reloadable) thresholds.

        REQ-E: an alert is created only when the p90 forecast first crosses
        the threshold at least ALERT_MIN_LEAD_MINUTES in the future, and only
        if no open/acknowledged alert exists for (component, metric).

        Returns:
            The most severe newly created alert (earliest breach on ties), else None.
        """
        created, _ = self.evaluate_thresholds(component_id, forecast_result)
        return self._pick(created)

    @staticmethod
    def _pick(alerts: list[PredictiveAlert]) -> PredictiveAlert | None:
        if not alerts:
            return None
        return sorted(
            alerts,
            key=lambda a: (-severity_rank(a.severity), a.predicted_breach_time),
        )[0]

    # ── orchestration ──────────────────────────────────────────────────────

    def run_cycle(
        self, component_id: str, forecast_window_minutes: int | None = None
    ) -> dict[str, Any]:
        """Poll → forecast → evaluate → hand off new alerts. Returns the /forecast body."""
        window = forecast_window_minutes or self.forecast_window_minutes
        start = time.perf_counter()
        try:
            observations, poll_errors = self._poll(component_id)
            forecast_result = self.forecast(observations, window)
            created, summary = self.evaluate_thresholds(component_id, forecast_result)
        except Exception:
            FORECAST_RUNS.labels(component=component_id, outcome="error").inc()
            raise
        finally:
            elapsed = time.perf_counter() - start
            FORECAST_LATENCY.observe(elapsed)
        FORECAST_RUNS.labels(component=component_id, outcome="ok").inc()

        for name, err in poll_errors.items():
            summary[name] = {"status": "error", "outcome": "query_failed", "error": err}
        comp = self.config.get(component_id)
        for name in comp.metrics:
            summary.setdefault(name, {"status": "no_data", "outcome": "no_data"})

        for alert in created:
            self.notify_diagnostic(alert)
        best = self._pick(created)
        return {
            "component_id": component_id,
            "alert_emitted": best is not None,
            "alert": best.to_dict() if best else None,
            "details": {
                "forecaster": self.forecaster.name,
                "model_version": self.model_version,
                "forecast_window_minutes": window,
                "step_seconds": self.step_seconds,
                "alert_min_lead_minutes": self.settings.alert_min_lead_minutes,
                "new_alert_ids": [a.alert_id for a in created],
                "latency_seconds": round(elapsed, 4),
                "metrics": summary,
            },
        }

    def run_all(self) -> dict[str, str]:
        """One background iteration over all enabled components; never raises."""
        outcomes: dict[str, str] = {}
        for component_id in self.config.components():
            try:
                res = self.run_cycle(component_id)
                outcomes[component_id] = "alert" if res["alert_emitted"] else "ok"
            except Exception as exc:  # - keep the loop alive
                logger.warning("forecast cycle for %s failed: %s", component_id, exc)
                outcomes[component_id] = "error"
        return outcomes

    # ── diagnostic hand-off (ADR 0001) ─────────────────────────────────────

    def _handoff(self, alert: PredictiveAlert) -> None:
        try:
            pb = self.diagnostic.diagnose(alert.component_id, alert.alert_id, [alert.anomaly()])
        except Exception as exc:
            logger.error("diagnostic hand-off for %s crashed: %s", alert.alert_id, exc)
            return
        if pb:
            self.alerts.set_playbook(alert.alert_id, pb)
            logger.info("alert %s linked to playbook %s", alert.alert_id, pb)

    def notify_diagnostic(self, alert: PredictiveAlert) -> None:
        """Asynchronously request a playbook so /forecast stays fast (< 2 s)."""
        fut: Future[None] = self._executor.submit(self._handoff, alert)
        with self._pending_lock:
            self._pending = [f for f in self._pending if not f.done()] + [fut]

    def wait_for_handoffs(self, timeout: float | None = 30.0) -> None:
        with self._pending_lock:
            pending = list(self._pending)
        wait_futures(pending, timeout=timeout)

    def ready(self) -> bool:
        return bool(self.config.components(include_disabled=True))

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.metric_client.close()
        self.diagnostic.close()
