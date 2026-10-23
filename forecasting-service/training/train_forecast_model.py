"""
train_forecast_model.py — train the forecasting model (SRS v1.0 §4.5, REQ-021).

Loads history (Prometheus, using the PromQL in config/components.yaml, or a
long-format CSV), fits ``PredictiveAnalyticsModel`` (scikit-learn ridge when
installed), backtests it against the statistical damped-Holt baseline on a
held-out tail of every series, and writes the JSON model the service loads
from ``ML_MODEL_PATH``. Exit status 2 when ``--require-better`` is set and
the model doesn't beat the baseline, so a CI/CD retrain job can gate on it.

Usage:
    pip install -r requirements.txt -r requirements-ml.txt
    python -m training.train_forecast_model --prometheus-url http://localhost:9090 \\
        --days 7 --output models/forecast_model.json --version forecast-2026.10.20
    python -m training.train_forecast_model --csv history.csv \\
        --output models/forecast_model.json --version forecast-2026.10.20

CSV columns: ``series,metric,timestamp,value`` (timestamp = unix seconds or
ISO-8601). Rows of one series must share a step; gaps are interpolated.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from app.components import ComponentConfigStore
from app.config import resolve_path
from app.forecasters import ForecasterError, StatisticalForecaster
from app.metric_client import MetricClient, parse_duration_seconds
from app.predictive_analytics import PredictiveAnalyticsModel

logger = logging.getLogger("train_forecast_model")

Series = dict[str, list[np.ndarray]]  # metric name → evenly spaced series


def _regularise(ts: np.ndarray, vals: np.ndarray, step: float) -> np.ndarray:
    order = np.argsort(ts)
    ts, vals = ts[order], vals[order]
    grid = np.arange(ts[0], ts[-1] + step / 2, step)
    return np.interp(grid, ts, vals)


def _parse_ts(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


def load_csv(path: Path, step: float) -> Series:
    points: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            points[(row["series"], row["metric"])].append(
                (_parse_ts(row["timestamp"]), float(row["value"]))
            )
    out: Series = defaultdict(list)
    for (_series, metric), pts in points.items():
        arr = np.array(pts, dtype=float)
        out[metric].append(_regularise(arr[:, 0], arr[:, 1], step))
    return out


def load_prometheus(url: str, components: Path, days: float, step: str) -> Series:
    store = ComponentConfigStore(components)
    client = MetricClient(url, default_step=step, timeout=60.0)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    out: Series = defaultdict(list)
    try:
        for component_id in store.components():
            for metric, spec in store.get(component_id).metrics.items():
                for series in client.query_range(spec.query, start, end, step):
                    vals = [(float(t), float(v)) for t, v in series.get("values", [])]
                    arr = np.array([p for p in vals if np.isfinite(p[1])], dtype=float)
                    if len(arr) > 1:
                        out[metric].append(
                            _regularise(arr[:, 0], arr[:, 1], parse_duration_seconds(step))
                        )
                logger.info("loaded %s/%s", component_id, metric)
    finally:
        client.close()
    return out


def backtest(model: PredictiveAnalyticsModel, test: Series, horizon: int) -> dict[str, float]:
    """Mean absolute error of the model vs. the statistical baseline on held-out tails."""
    baseline = StatisticalForecaster()
    errs: dict[str, list[float]] = {"model": [], "baseline": []}
    for metric, series_list in test.items():
        for series in series_list:
            hist, actual = series[:-horizon], series[-horizon:]
            if len(hist) < max(model.lags, 10):
                continue
            try:
                point, _ = model.forecast(metric, hist, horizon)
                base = baseline.predict(hist, horizon).point
            except (ValueError, ForecasterError):
                continue
            errs["model"].append(float(np.mean(np.abs(point - actual))))
            errs["baseline"].append(float(np.mean(np.abs(base - actual))))
    return {k: round(float(np.mean(v)), 6) if v else float("nan") for k, v in errs.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--prometheus-url", help="pull history for every enabled metric")
    src.add_argument("--csv", type=Path, help="long-format CSV: series, metric, timestamp, value")
    parser.add_argument("--components", default="config/components.yaml")
    parser.add_argument("--days", type=float, default=7.0)
    parser.add_argument("--step", default="60s")
    parser.add_argument("--lags", type=int, default=30)
    parser.add_argument("--l2", type=float, default=1.0)
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--holdout", type=int, default=60, help="steps held out per series")
    parser.add_argument("--require-better", action="store_true", help="exit 2 if baseline wins")
    parser.add_argument("--output", required=True, type=Path, help="model JSON to write")
    parser.add_argument("--version", required=True, help="model_version tag for the saved model")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    step = parse_duration_seconds(args.step)
    data = (
        load_csv(args.csv, step)
        if args.csv
        else load_prometheus(
            args.prometheus_url, resolve_path(args.components), args.days, args.step
        )
    )
    train = {m: [s[: -args.holdout] for s in ss if len(s) > args.holdout] for m, ss in data.items()}
    test = {m: [s for s in ss if len(s) > args.holdout] for m, ss in data.items()}

    model = PredictiveAnalyticsModel(
        args.version, lags=args.lags, l2=args.l2, min_confidence=args.min_confidence
    )
    fit = model.train(train)
    scores = backtest(model, test, args.holdout)
    # Final model is refit on everything, including the held-out tails.
    model.train(data)
    model.save(args.output)
    print(json.dumps({"version": args.version, "train_rmse": fit, "backtest_mae": scores}))

    if args.require_better and not scores["model"] <= scores["baseline"]:
        logger.error("model MAE %s does not beat baseline %s", scores["model"], scores["baseline"])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
