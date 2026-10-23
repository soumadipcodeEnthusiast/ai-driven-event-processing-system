"""
forecasters.py — pluggable probabilistic forecasters (SRS v1.0 §4.5, REQ-021, REQ-025).

* :class:`TFTForecaster` — a Temporal Fusion Transformer (pytorch-forecasting)
  trained on the platform's own Prometheus history by
  ``training/train_tft.py``; quantile outputs give the point (p50) and the
  p90 upper band directly. Needs the ML extra (``INSTALL_ML=true``) and the
  checkpoint at ``TFT_MODEL_PATH``. Default when both are present.
* :class:`StatisticalForecaster` — damped-trend Holt (ETS(A,Ad,N)) fitted by a
  vectorised grid search, with an analytical h-step prediction interval
  (p90 upper band). numpy only; always available.
* :class:`MLForecaster` — the trained ``ar-ridge`` model from
  ``predictive_analytics.py`` (fitted by scikit-learn in
  ``training/train_forecast_model.py``), loaded from the JSON file at
  ``ML_MODEL_PATH``. Inference is numpy-only, so the serving image needs no
  ML dependencies.
* :class:`FallbackForecaster` — runs a primary forecaster and falls back to a
  secondary one on any error.

``build_forecaster`` implements ``FORECASTER=auto|tft|ml|statistical``; ``auto``
picks TFT, else the ar-ridge model, else Holt. Whatever is primary, Holt is
the fallback for any single failed prediction.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.predictive_analytics import PredictiveAnalyticsModel

logger = logging.getLogger(__name__)

Z_P90 = 1.2815515655446004  # one-sided 90th percentile of N(0, 1)
MIN_POINTS = 10


class ForecasterError(RuntimeError):
    """Raised when a forecaster cannot produce a forecast for the given input."""


@dataclass
class SeriesForecast:
    """Forecast for one metric: ``point`` (median/mean) and ``upper_90`` per step."""

    point: np.ndarray
    upper_90: np.ndarray
    forecaster: str
    params: dict[str, Any]


class Forecaster(ABC):
    name: str = "base"
    model_version: str = "0"

    @abstractmethod
    def predict(
        self,
        values: np.ndarray,
        horizon: int,
        *,
        timestamps: np.ndarray | None = None,
        step_seconds: float = 60.0,
        metric_name: str = "",
    ) -> SeriesForecast:
        """Forecast *horizon* steps ahead of the evenly spaced history *values*."""


# ─── Statistical: damped-trend Holt ──────────────────────────────────────────

_ALPHAS = np.array([0.1, 0.2, 0.3, 0.5, 0.7, 0.9])
_BETAS = np.array([0.01, 0.05, 0.1, 0.2, 0.4])
_PHIS = np.array([0.9, 0.95, 0.98, 1.0])


class StatisticalForecaster(Forecaster):
    """Additive damped-trend exponential smoothing with residual-based p90 band."""

    name = "statistical"
    model_version = "holt-damped-1.0"

    def __init__(self) -> None:
        a, b, p = np.meshgrid(_ALPHAS, _BETAS, _PHIS, indexing="ij")
        self._alpha = a.ravel()
        self._beta = b.ravel()
        self._phi = p.ravel()

    @staticmethod
    def _filter(
        y: np.ndarray, alpha: np.ndarray, beta: np.ndarray, phi: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Run the Holt recursion for all parameter sets at once.

        Returns final level, final trend and the one-step-ahead errors
        (shape ``(n_params, n-1)``).
        """
        k = min(5, len(y) - 1)
        level = np.full(alpha.shape, y[0], dtype=float)
        trend = np.full(alpha.shape, (y[k] - y[0]) / k if k > 0 else 0.0, dtype=float)
        errors = np.empty((alpha.shape[0], len(y) - 1), dtype=float)
        for t in range(1, len(y)):
            fcst = level + phi * trend
            err = y[t] - fcst
            errors[:, t - 1] = err
            level = fcst + alpha * err
            trend = phi * trend + alpha * beta * err
        return level, trend, errors

    def predict(
        self,
        values: np.ndarray,
        horizon: int,
        *,
        timestamps: np.ndarray | None = None,
        step_seconds: float = 60.0,
        metric_name: str = "",
    ) -> SeriesForecast:
        y = np.asarray(values, dtype=float)
        y = y[np.isfinite(y)]
        if len(y) < MIN_POINTS:
            raise ForecasterError(f"need at least {MIN_POINTS} points, got {len(y)}")
        if horizon < 1:
            raise ForecasterError("horizon must be >= 1")

        # 1) grid search on one-step-ahead SSE (skip warm-up errors)
        _, _, errs = self._filter(y, self._alpha, self._beta, self._phi)
        warm = min(5, errs.shape[1] - 1)
        sse = np.sum(errs[:, warm:] ** 2, axis=1)
        best = int(np.argmin(sse))
        alpha, beta, phi = (
            float(self._alpha[best]),
            float(self._beta[best]),
            float(self._phi[best]),
        )

        # 2) final state + residual scale for the chosen parameters
        level_a, trend_a, err_a = self._filter(
            y, np.array([alpha]), np.array([beta]), np.array([phi])
        )
        level, trend = float(level_a[0]), float(trend_a[0])
        resid = err_a[0, warm:]
        sigma = float(np.std(resid, ddof=1)) if len(resid) > 1 else 0.0
        sigma = max(sigma, 1e-9)

        # 3) h-step point forecast and ETS(A,Ad,N) variance
        h = np.arange(1, horizon + 1, dtype=float)
        damp_sum = h if phi == 1.0 else phi * (1.0 - phi**h) / (1.0 - phi)
        point = level + damp_sum * trend

        # c_j = alpha * (1 + beta * sum_{i=1..j} phi^i), j = 1..h-1
        c = alpha * (1.0 + beta * damp_sum[:-1]) if horizon > 1 else np.empty(0)
        var = sigma**2 * (1.0 + np.concatenate(([0.0], np.cumsum(c**2))))
        upper = point + Z_P90 * np.sqrt(var)

        # utilisation / rates / lag are non-negative
        point = np.maximum(point, 0.0)
        upper = np.maximum(upper, point)
        return SeriesForecast(
            point=point,
            upper_90=upper,
            forecaster=self.name,
            params={
                "alpha": alpha,
                "beta": beta,
                "phi": phi,
                "sigma": sigma,
                "level": level,
                "trend": trend,
            },
        )


# ─── ML (TensorFlow / scikit-learn) ──────────────────────────────────────────


def ml_deps_available() -> bool:
    """Whether the *training* dependencies are installed (requirements-ml.txt)."""
    return all(importlib.util.find_spec(m) is not None for m in ("sklearn", "pandas"))


class MLForecaster(Forecaster):
    """Trained autoregressive model (``predictive_analytics.PredictiveAnalyticsModel``)."""

    name = "ml"

    def __init__(self, model_path: str | Path, model_version: str | None = None) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise ForecasterError(f"model file not found: {path}")
        try:
            self.model = PredictiveAnalyticsModel.load(path, model_version or None)
        except (ValueError, KeyError, TypeError) as exc:
            raise ForecasterError(f"invalid model file {path}: {exc}") from exc
        self.model_version = self.model.model_version
        self._model_path = path

    def predict(
        self,
        values: np.ndarray,
        horizon: int,
        *,
        timestamps: np.ndarray | None = None,
        step_seconds: float = 60.0,
        metric_name: str = "",
    ) -> SeriesForecast:
        try:
            point, upper = self.model.forecast(metric_name, values, horizon)
        except ValueError as exc:
            raise ForecasterError(str(exc)) from exc
        point = np.maximum(point, 0.0)
        return SeriesForecast(
            point=point,
            upper_90=np.maximum(upper, point),
            forecaster=self.name,
            params={"lags": self.model.lags, "model_version": self.model_version},
        )


# ─── Temporal Fusion Transformer (pytorch-forecasting) ───────────────────────


def tft_deps_available() -> bool:
    """Whether torch + pytorch-forecasting are installed (requirements-ml.txt)."""
    return all(
        importlib.util.find_spec(m) is not None for m in ("torch", "pytorch_forecasting", "pandas")
    )


def time_features(epoch_seconds: np.ndarray) -> dict[str, np.ndarray]:
    """Known-in-advance covariates shared by training and inference."""
    seconds_of_day = np.mod(epoch_seconds, 86400.0)
    angle = 2.0 * math.pi * seconds_of_day / 86400.0
    return {"hour_sin": np.sin(angle), "hour_cos": np.cos(angle)}


def build_tft_frame(
    values: np.ndarray,
    timestamps: np.ndarray,
    metric_name: str,
    horizon: int,
    step_seconds: float,
) -> Any:
    """Long-format DataFrame (history + ``horizon`` future rows) in the TFT's training layout.

    Columns: ``series``, ``metric``, ``time_idx``, ``value``, ``hour_sin``, ``hour_cos``.
    Future rows carry the last observed value as a placeholder target (the
    decoder ignores it; pytorch-forecasting needs the rows to exist).
    """
    import pandas as pd  # lazy: ML extra

    n = len(values)
    future_ts = timestamps[-1] + step_seconds * np.arange(1, horizon + 1)
    all_ts = np.concatenate([timestamps, future_ts])
    feats = time_features(all_ts)
    return pd.DataFrame(
        {
            "series": metric_name or "series",
            "metric": metric_name or "unknown",
            "time_idx": np.arange(n + horizon, dtype=np.int64),
            "value": np.concatenate([values, np.full(horizon, values[-1])]).astype(np.float32),
            "hour_sin": feats["hour_sin"].astype(np.float32),
            "hour_cos": feats["hour_cos"].astype(np.float32),
        }
    )


class TFTForecaster(Forecaster):
    """Temporal Fusion Transformer inference from a Lightning checkpoint.

    Runs the network's forward pass directly on one batch rather than through
    ``model.predict`` (which builds a Lightning Trainer per call), keeping a
    60-step forecast well under the REQ-021 latency budget on CPU.
    """

    name = "tft"

    def __init__(self, model_path: str | Path, model_version: str = "") -> None:
        path = Path(model_path)
        if not path.is_file():
            raise ForecasterError(f"TFT checkpoint not found: {path}")
        try:
            import torch
            from pytorch_forecasting import TemporalFusionTransformer
        except ImportError as exc:  # pragma: no cover - depends on ML extra
            raise ForecasterError(f"ML dependencies not installed: {exc}") from exc
        torch.set_num_threads(2)
        self._model = TemporalFusionTransformer.load_from_checkpoint(str(path), map_location="cpu")
        self._model.eval()
        self._params = dict(self._model.dataset_parameters or {})
        meta_path = path.with_suffix(".json")
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        self.model_version = model_version or str(meta.get("model_version") or "tft")
        self.encoder_length = int(self._params.get("max_encoder_length", 120))
        self.prediction_length = int(self._params.get("max_prediction_length", 60))
        quantiles = list(getattr(self._model.loss, "quantiles", [0.1, 0.5, 0.9]))
        self._q50 = int(np.argmin([abs(q - 0.5) for q in quantiles]))
        self._q90 = int(np.argmin([abs(q - 0.9) for q in quantiles]))
        logger.info(
            "TFT loaded from %s (version=%s, encoder=%d, horizon=%d)",
            path,
            self.model_version,
            self.encoder_length,
            self.prediction_length,
        )

    def predict(  # pragma: no cover - requires torch + checkpoint
        self,
        values: np.ndarray,
        horizon: int,
        *,
        timestamps: np.ndarray | None = None,
        step_seconds: float = 60.0,
        metric_name: str = "",
    ) -> SeriesForecast:
        if horizon > self.prediction_length:
            raise ForecasterError(
                f"horizon {horizon} exceeds TFT max_prediction_length {self.prediction_length}"
            )
        y = np.asarray(values, dtype=float)
        min_encoder = int(self._params.get("min_encoder_length", self.encoder_length // 2))
        if len(y) < max(MIN_POINTS, min_encoder):
            raise ForecasterError("not enough history for the TFT encoder")
        if timestamps is None:
            timestamps = np.arange(len(y), dtype=float) * step_seconds
        y = y[-self.encoder_length :]
        ts = np.asarray(timestamps, dtype=float)[-self.encoder_length :]
        frame = build_tft_frame(y, ts, metric_name, self.prediction_length, step_seconds)

        import torch
        from pytorch_forecasting import TimeSeriesDataSet

        dataset = TimeSeriesDataSet.from_parameters(
            self._params, frame, predict=True, stop_randomization=True
        )
        x, _ = next(iter(dataset.to_dataloader(train=False, batch_size=1, num_workers=0)))
        with torch.no_grad():
            out = self._model(x)
        arr = self._model.to_quantiles(out)[0].detach().cpu().numpy()  # (horizon, n_quantiles)
        point = np.maximum(arr[:horizon, self._q50], 0.0)
        upper = np.maximum(arr[:horizon, self._q90], point)
        return SeriesForecast(
            point, upper, self.name, {"encoder_length": len(y), "model_version": self.model_version}
        )


class FallbackForecaster(Forecaster):
    """Use *primary*; on any error fall back to *secondary* (statistical)."""

    def __init__(self, primary: Forecaster, secondary: Forecaster) -> None:
        self.primary = primary
        self.secondary = secondary
        self.name = primary.name
        self.model_version = primary.model_version

    def predict(
        self,
        values: np.ndarray,
        horizon: int,
        *,
        timestamps: np.ndarray | None = None,
        step_seconds: float = 60.0,
        metric_name: str = "",
    ) -> SeriesForecast:
        try:
            return self.primary.predict(
                values,
                horizon,
                timestamps=timestamps,
                step_seconds=step_seconds,
                metric_name=metric_name,
            )
        except Exception as exc:  # - any model failure → fallback
            logger.warning(
                "%s forecaster failed (%s); using %s", self.primary.name, exc, self.secondary.name
            )
            return self.secondary.predict(
                values,
                horizon,
                timestamps=timestamps,
                step_seconds=step_seconds,
                metric_name=metric_name,
            )


def build_forecaster(
    mode: str,
    model_path: str,
    model_version: str,
    tft_model_path: str = "",
    tft_model_version: str = "",
) -> Forecaster:
    """Select the forecaster per ``FORECASTER`` (auto|tft|ml|statistical).

    ``auto``: TFT if its checkpoint and the ML extra are present, else the
    ar-ridge model if its file exists, else Holt. Holt always backs up the
    chosen model for individual predictions that fail.
    """
    statistical = StatisticalForecaster()
    if mode == "statistical":
        return statistical
    tft_ready = bool(tft_model_path) and Path(tft_model_path).is_file() and tft_deps_available()
    if mode == "tft" or (mode == "auto" and tft_ready):
        try:
            return FallbackForecaster(TFTForecaster(tft_model_path, tft_model_version), statistical)
        except Exception as exc:
            logger.error("could not load TFT (%s); trying the next forecaster", exc)
    if mode == "auto" and not Path(model_path).is_file():
        logger.info("FORECASTER=auto: no TFT or ML model → statistical")
        return statistical
    if mode == "tft":
        return statistical
    try:
        return FallbackForecaster(MLForecaster(model_path, model_version), statistical)
    except Exception as exc:
        logger.error("could not load ML model (%s); using statistical forecaster", exc)
        return statistical
