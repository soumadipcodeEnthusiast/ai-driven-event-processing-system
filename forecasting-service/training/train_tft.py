"""
train_tft.py — train a Temporal Fusion Transformer on platform metrics (REQ-D).

Data sources (one of):
  --prometheus-url URL   pull history for every enabled metric in components.yaml
  --csv PATH             long-format CSV with columns: series, metric, timestamp, value
                         (timestamp = unix seconds or ISO-8601)

The checkpoint is consumed by ``app.forecasters.TFTForecaster`` via
``TFT_MODEL_PATH`` with ``FORECASTER=auto|tft``. Feature engineering is shared
with inference through ``app.forecasters.build_tft_frame``/``time_features``.

Example:
    pip install -r requirements.txt -r requirements-ml.txt
    python -m training.train_tft --prometheus-url http://localhost:9090 \
        --days 7 --output models/tft.ckpt --version tft-2026.10.04
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.components import ComponentConfigStore
from app.forecasters import time_features
from app.metric_client import MetricClient, parse_duration_seconds

logger = logging.getLogger("train_tft")

MAX_POINTS_PER_QUERY = 10_000  # Prometheus rejects > 11k points per series


def fetch_prometheus(url: str, config_path: str, days: float, step: str) -> list[dict[str, Any]]:
    client = MetricClient(url, default_step=step, timeout=60.0)
    store = ComponentConfigStore(config_path)
    step_s = parse_duration_seconds(step)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    chunk = timedelta(seconds=step_s * MAX_POINTS_PER_QUERY)
    rows: list[dict[str, Any]] = []
    for comp_id, comp in store.components().items():
        for name, spec in comp.metrics.items():
            t0 = start
            while t0 < end:
                t1 = min(t0 + chunk, end)
                for series in client.query_range(spec.query, t0, t1, step):
                    rows.extend(
                        {
                            "series": f"{comp_id}:{name}",
                            "metric": name,
                            "timestamp": float(ts),
                            "value": float(v),
                        }
                        for ts, v in series.get("values", [])
                    )
                t0 = t1
            logger.info("fetched %s/%s", comp_id, name)
    client.close()
    return rows


def prepare_frame(df: Any, step_s: float) -> Any:
    """Regularise each series onto the step grid and add model features."""
    import pandas as pd

    if not np.issubdtype(df["timestamp"].dtype, np.number):
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).astype("int64") / 1e9
    frames = []
    for series, g in df.groupby("series"):
        g = g.groupby("timestamp", as_index=False)["value"].max().sort_values("timestamp")
        ts = g["timestamp"].to_numpy(dtype=float)
        grid = np.arange(ts[0], ts[-1] + step_s / 2, step_s)
        vals = np.interp(grid, ts, g["value"].to_numpy(dtype=float))
        feats = time_features(grid)
        metric = df.loc[df["series"] == series, "metric"].iloc[0]
        frames.append(
            pd.DataFrame(
                {
                    "series": series,
                    "metric": metric,
                    "time_idx": np.arange(len(grid), dtype=np.int64),
                    "value": vals.astype(np.float32),
                    "hour_sin": feats["hour_sin"].astype(np.float32),
                    "hour_cos": feats["hour_cos"].astype(np.float32),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def train(args: argparse.Namespace) -> Path:
    import lightning.pytorch as pl
    import pandas as pd
    from lightning.pytorch.callbacks import EarlyStopping
    from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet
    from pytorch_forecasting.data import EncoderNormalizer, NaNLabelEncoder
    from pytorch_forecasting.metrics import QuantileLoss

    step_s = parse_duration_seconds(args.step)
    if args.csv:
        raw = pd.read_csv(args.csv)
    else:
        raw = pd.DataFrame(fetch_prometheus(args.prometheus_url, args.config, args.days, args.step))
    if raw.empty:
        raise SystemExit("no training data")
    data = prepare_frame(raw, step_s)
    min_len = args.encoder_length + args.prediction_length
    data = data.groupby("series").filter(lambda g: len(g) >= min_len)
    if data.empty:
        raise SystemExit(f"every series is shorter than {min_len} steps")

    cutoff = data.groupby("series")["time_idx"].transform("max") - args.prediction_length
    params: dict[str, Any] = {
        "time_idx": "time_idx",
        "target": "value",
        "group_ids": ["series"],
        "max_encoder_length": args.encoder_length,
        "min_encoder_length": args.encoder_length // 2,
        "max_prediction_length": args.prediction_length,
        "static_categoricals": ["metric"],
        "time_varying_known_reals": ["hour_sin", "hour_cos"],
        "time_varying_unknown_reals": ["value"],
        "target_normalizer": EncoderNormalizer(),
        "categorical_encoders": {
            "series": NaNLabelEncoder(add_nan=True),
            "metric": NaNLabelEncoder(add_nan=True),
        },
        "add_relative_time_idx": True,
        "add_target_scales": True,
        "add_encoder_length": True,
        "allow_missing_timesteps": True,
    }
    training = TimeSeriesDataSet(data[data["time_idx"] <= cutoff], **params)
    validation = TimeSeriesDataSet.from_dataset(
        training, data, predict=True, stop_randomization=True
    )
    train_dl = training.to_dataloader(train=True, batch_size=args.batch_size, num_workers=0)
    val_dl = validation.to_dataloader(train=False, batch_size=args.batch_size, num_workers=0)

    pl.seed_everything(42)
    model = TemporalFusionTransformer.from_dataset(
        training,
        learning_rate=args.learning_rate,
        hidden_size=args.hidden_size,
        attention_head_size=2,
        dropout=0.1,
        hidden_continuous_size=max(8, args.hidden_size // 2),
        loss=QuantileLoss(quantiles=[0.1, 0.5, 0.9]),
        log_interval=-1,
        reduce_on_plateau_patience=3,
    )
    trainer = pl.Trainer(
        max_epochs=args.epochs,
        accelerator="cpu",
        gradient_clip_val=0.1,
        callbacks=[EarlyStopping(monitor="val_loss", patience=4, mode="min")],
        enable_checkpointing=False,
        logger=False,
    )
    started = time.time()
    trainer.fit(model, train_dataloaders=train_dl, val_dataloaders=val_dl)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    trainer.save_checkpoint(str(out))
    meta = {
        "model_version": args.version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_seconds": round(time.time() - started, 1),
        "series": sorted(data["series"].unique().tolist()),
        "step": args.step,
        "encoder_length": args.encoder_length,
        "prediction_length": args.prediction_length,
        "quantiles": [0.1, 0.5, 0.9],
        "val_loss": float(trainer.callback_metrics.get("val_loss", float("nan"))),
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    logger.info("saved %s (set TFT_MODEL_PATH=%s TFT_MODEL_VERSION=%s)", out, out, args.version)
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--prometheus-url")
    src.add_argument("--csv")
    p.add_argument("--config", default="config/components.yaml")
    p.add_argument("--days", type=float, default=7.0)
    p.add_argument("--step", default="60s")
    p.add_argument("--encoder-length", type=int, default=120)
    p.add_argument("--prediction-length", type=int, default=60)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--hidden-size", type=int, default=32)
    p.add_argument("--learning-rate", type=float, default=0.01)
    p.add_argument("--output", default="models/tft.ckpt")
    p.add_argument("--version", default=f"tft-{datetime.now(timezone.utc):%Y%m%d}")
    logging.basicConfig(level=logging.INFO)
    train(p.parse_args(argv))


if __name__ == "__main__":
    main()
