"""
forecasting_engine.py — TFT-based metric forecasting engine stub.

Houses the ForecastingEngine class and the PredictiveAlert dataclass.

Requirements:
    REQ-D — Poll Prometheus metrics and run TFT inference.
    REQ-E — Emit a PredictiveAlert at least N minutes before a predicted breach.
    REQ-F — Threshold values shall be configurable without redeployment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, List

from app.metric_client import MetricClient

logger = logging.getLogger(__name__)


# ─── Supporting types ─────────────────────────────────────────────────────────

class AlertSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


@dataclass
class PredictiveAlert:
    """
    Represents a forecast-driven predictive alert.

    Attributes match the class diagram:
        alert_id            — globally unique alert identifier
        component_id        — identifier of the component at risk
        predicted_breach_time — UTC datetime when the metric is forecast to breach
        severity            — alert severity level
        status              — current lifecycle status
    """
    alert_id: str
    component_id: str
    predicted_breach_time: datetime
    severity: AlertSeverity
    status: AlertStatus = AlertStatus.OPEN


# ─── ForecastingEngine ────────────────────────────────────────────────────────

class ForecastingEngine:
    """
    Orchestrates metric polling, TFT inference, and threshold evaluation.

    Class-diagram attributes:
        model_version           — identifier of the loaded TFT model artefact
        forecast_window_minutes — look-ahead window used during inference
    """

    def __init__(self) -> None:
        # Class-diagram typed fields
        self.model_version: str = os.getenv("TFT_MODEL_VERSION", "0.0.0-stub")
        self.forecast_window_minutes: int = int(
            os.getenv("FORECAST_WINDOW_MINUTES", "60")
        )

        self._metric_client: MetricClient = MetricClient()
        logger.info(
            "ForecastingEngine initialised (model_version=%s, window=%dm)",
            self.model_version,
            self.forecast_window_minutes,
        )

    # ── Public API ────────────────────────────────────────────────────────

    def poll_metrics(self, component_id: str) -> List[dict[str, Any]]:
        """
        Poll Prometheus for time-series metrics relevant to *component_id*.

        REQ-D: The system shall retrieve at minimum CPU utilisation, memory
        utilisation, and request-error-rate for the given component over the
        last ``forecast_window_minutes`` of history.

        Args:
            component_id: Identifier of the component to query.

        Returns:
            A list of metric observation dicts, each with keys
            ``metric_name``, ``timestamp``, and ``value``.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-D
        #   1. Build PromQL queries for component_id
        #   2. Delegate to self._metric_client.query_range(...)
        #   3. Return parsed time-series as list of dicts
        raise NotImplementedError("TODO: implement poll_metrics — REQ-D")

    def forecast(self, metrics: List[dict[str, Any]]) -> dict[str, Any]:
        """
        Run TFT inference over the provided metric time-series.

        REQ-D: The system shall use a Temporal Fusion Transformer model to
        produce a probabilistic forecast for each metric over the next
        ``forecast_window_minutes``.

        REQ-E: The forecast shall include point estimates and 90th-percentile
        confidence intervals for each metric.

        Args:
            metrics: Output of :meth:`poll_metrics`.

        Returns:
            A dict mapping metric names to forecast arrays (point estimate
            + confidence bounds).

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-D, REQ-E
        #   1. Convert metrics list → pandas DataFrame with datetime index
        #   2. Load TFT model artefact identified by self.model_version
        #   3. Run model.predict() over forecast_window_minutes horizon
        #   4. Return {metric_name: {"point": [...], "upper_90": [...]}}
        raise NotImplementedError("TODO: implement forecast — REQ-D, REQ-E")

    def evaluate_threshold(
        self,
        component_id: str,
        forecast_result: dict[str, Any],
    ) -> PredictiveAlert | None:
        """
        Compare forecast values against configurable breach thresholds.

        REQ-E: If any metric's 90th-percentile forecast exceeds its threshold
        within the window, a :class:`PredictiveAlert` must be emitted with at
        least 10 minutes of lead time.

        REQ-F: Thresholds shall be read from environment variables / a remote
        config store without requiring a service restart.

        Args:
            component_id:    Component being evaluated.
            forecast_result: Output of :meth:`forecast`.

        Returns:
            A :class:`PredictiveAlert` if a breach is predicted, else ``None``.

        Raises:
            NotImplementedError: until implemented.
        """
        # TODO: implement — REQ-E, REQ-F
        #   1. Load thresholds from config (env var or config service)
        #   2. For each metric, find first forecast step exceeding threshold
        #   3. If breach predicted with ≥ 10-minute lead time → build PredictiveAlert
        #   4. Assign severity based on breach magnitude
        #   5. Return PredictiveAlert or None
        raise NotImplementedError("TODO: implement evaluate_threshold — REQ-E, REQ-F")
