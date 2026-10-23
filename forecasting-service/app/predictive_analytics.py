"""
predictive_analytics.py — time-series forecasting with confidence flagging
(SRS v1.0 §4.5, REQ-021 to REQ-025).

REQ-021: time-series forecasting using historical event data.
REQ-022: refresh predictions at configurable intervals (the forecast loop in
         app/main.py, every FORECAST_INTERVAL_SECONDS).
REQ-023: expose prediction results through a query API (GET /predictions).
REQ-024: version and store prediction outputs for later evaluation
         (``PredictionStore``: in memory + append-only JSONL).
REQ-025: flag predictions with low confidence scores (``evaluate_confidence``).

Model (``ar-ridge``): a global autoregressive model per metric name. Each
training window of ``lags`` points is standardised by its own mean/std, so
one model serves components with very different scales; the target is the
standardised next value. Weights are fitted by ridge regression —
scikit-learn's ``Ridge`` when installed (SRS v1.0 §2.5 / §3.3), else the
identical closed form in numpy. Multi-step forecasts are recursive, and the
p90 upper band grows with ``sqrt(h)`` from the training residual scale.

The fitted model is plain JSON (coefficients, residual sigma, version), so
loading it is safe (no pickle) and inference needs numpy only — the serving
image doesn't need tensorflow/scikit-learn, only the training job does.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeAlias

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

Z_P90 = 1.2815515655446004
MODEL_FORMAT = "ar-ridge/1"
EPS = 1e-9
DEFAULT_MODEL_KEY = "*"  # fallback model for metric names without their own

FloatSeq: TypeAlias = Sequence[float] | npt.NDArray[np.float64]


@dataclass(frozen=True)
class Prediction:
    component_id: str
    metric_name: str
    values: list[float]
    confidence: float
    model_version: str
    low_confidence: bool
    upper_90: list[float] = field(default_factory=list)


def band_confidence(point: FloatSeq, upper: FloatSeq, scale: float) -> float:
    """Confidence in ``(0, 1]`` from the p90 band's width at the horizon end (REQ-025).

    ``1 / (1 + w)`` with ``w`` the band half-width relative to the level being
    predicted: a band as wide as the value itself gives 0.5.
    """
    if len(point) == 0:
        return 0.0
    width = max(0.0, float(upper[-1]) - float(point[-1]))
    level = max(abs(float(point[-1])), abs(scale), EPS)
    return round(1.0 / (1.0 + width / level), 6)


def _windows(series: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray]:
    """Standardised (X, y) training pairs from one series."""
    n = len(series) - lags
    if n <= 0:
        return np.empty((0, lags)), np.empty(0)
    idx = np.arange(lags)[None, :] + np.arange(n)[:, None]
    w = series[idx]
    mu = w.mean(axis=1, keepdims=True)
    sd = w.std(axis=1, keepdims=True) + EPS
    return (w - mu) / sd, (series[lags:] - mu[:, 0]) / sd[:, 0]


def _fit_ridge(x: np.ndarray, y: np.ndarray, l2: float) -> tuple[np.ndarray, float]:
    try:
        from sklearn.linear_model import Ridge
    except ImportError:
        xm, ym = x.mean(axis=0), y.mean()
        xc, yc = x - xm, y - ym
        coef = np.linalg.solve(xc.T @ xc + l2 * np.eye(x.shape[1]), xc.T @ yc)
        return coef, float(ym - xm @ coef)
    model = Ridge(alpha=l2).fit(x, y)
    return np.asarray(model.coef_, dtype=float), float(model.intercept_)


class PredictiveAnalyticsModel:
    """Trains, serves and versions a time-series forecasting model.

    ``refresh_interval_seconds`` controls how often ``predict`` results
    should be considered stale and recomputed (REQ-022); enforcing that
    cadence is the caller's responsibility (the forecast loop), not this
    class's.
    """

    def __init__(
        self,
        model_version: str,
        refresh_interval_seconds: float = 60.0,
        *,
        lags: int = 30,
        l2: float = 1.0,
        min_confidence: float = 0.5,
    ) -> None:
        if lags < 2:
            raise ValueError("lags must be >= 2")
        self.model_version = model_version
        self.refresh_interval_seconds = refresh_interval_seconds
        self.lags = lags
        self.l2 = l2
        self.min_confidence = min_confidence
        # metric name → {"coef": [...], "intercept": float, "sigma": float, "samples": int}
        self.weights: dict[str, dict[str, Any]] = {}

    @property
    def trained(self) -> bool:
        return bool(self.weights)

    # ── REQ-021: training ──────────────────────────────────────────────────

    def train(self, historical_data: Mapping[str, Sequence[Sequence[float]]]) -> dict[str, float]:
        """Fit one model per metric name, plus a pooled ``"*"`` fallback (REQ-021).

        ``historical_data`` maps metric name → list of evenly spaced series
        (one per component/instance). Returns the in-sample RMSE per model in
        standardised units.
        """
        pooled_x: list[np.ndarray] = []
        pooled_y: list[np.ndarray] = []
        report: dict[str, float] = {}
        for metric, series_list in historical_data.items():
            xs, ys = [], []
            for series in series_list:
                arr = np.asarray(series, dtype=float)
                arr = arr[np.isfinite(arr)]
                x, y = _windows(arr, self.lags)
                if len(y):
                    xs.append(x)
                    ys.append(y)
            if not ys:
                logger.warning("no training windows for %s (need > %d points)", metric, self.lags)
                continue
            x, y = np.vstack(xs), np.concatenate(ys)
            pooled_x.append(x)
            pooled_y.append(y)
            report[metric] = self._fit(metric, x, y)
        if not pooled_y:
            raise ValueError(f"no series longer than lags={self.lags}; nothing to train on")
        report[DEFAULT_MODEL_KEY] = self._fit(
            DEFAULT_MODEL_KEY, np.vstack(pooled_x), np.concatenate(pooled_y)
        )
        return report

    def _fit(self, key: str, x: np.ndarray, y: np.ndarray) -> float:
        coef, intercept = _fit_ridge(x, y, self.l2)
        resid = y - (x @ coef + intercept)
        sigma = float(np.sqrt(np.mean(resid**2))) if len(resid) else 1.0
        self.weights[key] = {
            "coef": coef.tolist(),
            "intercept": intercept,
            "sigma": max(sigma, 1e-6),
            "samples": len(y),
        }
        return round(sigma, 6)

    # ── REQ-021 / REQ-023: inference ───────────────────────────────────────

    def forecast(
        self, metric_name: str, history: FloatSeq, horizon: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Point forecast and p90 upper band, ``horizon`` steps past ``history``."""
        w = self.weights.get(metric_name) or self.weights.get(DEFAULT_MODEL_KEY)
        if w is None:
            raise ValueError(f"no trained model for metric {metric_name!r}")
        hist = np.asarray(history, dtype=float)
        hist = hist[np.isfinite(hist)]
        if len(hist) < self.lags:
            raise ValueError(f"need at least {self.lags} points, got {len(hist)}")
        if horizon < 1:
            raise ValueError("horizon must be >= 1")
        coef = np.asarray(w["coef"], dtype=float)
        window = deque(hist[-self.lags :].tolist(), maxlen=self.lags)
        point = np.empty(horizon)
        scale = np.empty(horizon)
        for h in range(horizon):
            arr = np.fromiter(window, dtype=float, count=self.lags)
            mu, sd = arr.mean(), arr.std() + EPS
            nxt = mu + sd * (float(((arr - mu) / sd) @ coef) + float(w["intercept"]))
            point[h] = nxt
            scale[h] = sd
            window.append(nxt)
        # Per-step uncertainty in original units: the larger of the training
        # residual scaled to the current window and the model's one-step error
        # on this very history (so a series noisier than the training data gets
        # a wider band), accumulated as a random walk over the horizon.
        sigma = max(float(w["sigma"]) * float(scale[0]), self._local_sigma(hist, coef, w))
        steps = np.arange(1, horizon + 1, dtype=float)
        upper = point + Z_P90 * max(sigma, EPS) * np.sqrt(steps)
        return point, upper

    def _local_sigma(self, hist: np.ndarray, coef: np.ndarray, w: dict[str, Any]) -> float:
        """RMS one-step-ahead error (original units) over the recent history."""
        recent = hist[-(self.lags + 60) :]
        x, y = _windows(recent, self.lags)
        if len(y) < 5:
            return 0.0
        idx = np.arange(self.lags)[None, :] + np.arange(len(y))[:, None]
        sd = recent[idx].std(axis=1) + EPS
        resid = (y - (x @ coef + float(w["intercept"]))) * sd
        return float(np.sqrt(np.mean(resid**2)))

    def predict(
        self, component_id: str, metric_name: str, horizon: int, history: FloatSeq
    ) -> Prediction:
        """Forecast ``horizon`` steps ahead for one (component, metric) (REQ-021, REQ-023)."""
        point, upper = self.forecast(metric_name, history, horizon)
        recent = np.asarray(history, dtype=float)[-self.lags :]
        prediction = Prediction(
            component_id=component_id,
            metric_name=metric_name,
            values=point.tolist(),
            confidence=band_confidence(point, upper, float(np.mean(np.abs(recent)))),
            model_version=self.model_version,
            low_confidence=False,
            upper_90=upper.tolist(),
        )
        return replace(prediction, low_confidence=self.evaluate_confidence(prediction))

    def evaluate_confidence(self, prediction: Prediction) -> bool:
        """Return whether ``prediction`` should be flagged low-confidence (REQ-025)."""
        return prediction.confidence < self.min_confidence

    # ── persistence (REQ-018-style no-downtime swaps: new version = new file) ──

    def save(self, path: str | Path) -> None:
        if not self.trained:
            raise ValueError("model is not trained")
        doc = {
            "format": MODEL_FORMAT,
            "model_version": self.model_version,
            "lags": self.lags,
            "l2": self.l2,
            "min_confidence": self.min_confidence,
            "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "weights": self.weights,
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        tmp.replace(target)  # atomic: the service never reads a half-written model

    @classmethod
    def load(cls, path: str | Path, model_version: str | None = None) -> PredictiveAnalyticsModel:
        doc = json.loads(Path(path).read_text())
        if doc.get("format") != MODEL_FORMAT:
            raise ValueError(f"unsupported model format {doc.get('format')!r}")
        model = cls(
            model_version or doc["model_version"],
            lags=int(doc["lags"]),
            l2=float(doc.get("l2", 1.0)),
            min_confidence=float(doc.get("min_confidence", 0.5)),
        )
        model.weights = {k: dict(v) for k, v in doc["weights"].items()}
        for key, w in model.weights.items():
            if len(w["coef"]) != model.lags:
                raise ValueError(f"model {key!r}: {len(w['coef'])} coefficients, lags={model.lags}")
        return model


# ── REQ-024: versioned prediction outputs ─────────────────────────────────────


class PredictionStore:
    """Keeps every prediction (with its model version) for later evaluation (REQ-024).

    In memory: the latest prediction per (component, metric) for the query
    API, plus a bounded history. On disk (``path`` set): one JSON line per
    prediction, append-only — the archiver/backtests read it later.
    """

    def __init__(self, path: str | Path | None = None, history: int = 5000) -> None:
        self._path = Path(path) if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._latest: dict[tuple[str, str], dict[str, Any]] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=history)

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry = {"prediction_id": str(uuid.uuid4()), **entry}
        line = json.dumps(entry, separators=(",", ":"))
        with self._lock:
            self._latest[(entry["component_id"], entry["metric_name"])] = entry
            self._history.append(entry)
            if self._path is not None:
                try:
                    with self._path.open("a") as fh:
                        fh.write(line + "\n")
                except OSError as exc:
                    logger.error("could not persist prediction: %s", exc)
        return entry

    def latest(
        self, component_id: str | None = None, metric_name: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._latest.values())
        return [
            p
            for p in items
            if (component_id is None or p["component_id"] == component_id)
            and (metric_name is None or p["metric_name"] == metric_name)
        ]

    def history(
        self, component_id: str | None = None, model_version: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._history)
        out = [
            p
            for p in reversed(items)
            if (component_id is None or p["component_id"] == component_id)
            and (model_version is None or p["model_version"] == model_version)
        ]
        return out[:limit]
