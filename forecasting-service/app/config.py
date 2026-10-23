"""
config.py — runtime configuration for forecasting-service (docs/CONTRACTS.md §5).

All values come from environment variables; defaults match the contract.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parent.parent


def resolve_path(path: str) -> Path:
    """Resolve *path*: absolute as-is; relative against CWD, else the service root."""
    p = Path(path)
    if p.is_absolute():
        return p
    if p.exists():
        return p.resolve()
    return SERVICE_ROOT / p


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    prometheus_url: str = "http://prometheus:9090"
    prometheus_step: str = "60s"
    prometheus_timeout_seconds: float = 5.0

    forecast_window_minutes: int = 60
    forecast_interval_seconds: float = 60.0  # <= 0 disables the background loop
    alert_min_lead_minutes: float = 10.0

    components_config: str = "config/components.yaml"

    # auto = TFT if available, else the ar-ridge model, else statistical (Holt).
    forecaster: Literal["auto", "tft", "ml", "statistical"] = "auto"
    # Temporal Fusion Transformer checkpoint (training/train_tft.py); the
    # version defaults to the one recorded in the checkpoint's .json sidecar.
    tft_model_path: str = "/models/tft.ckpt"
    tft_model_version: str = ""
    # Empty = use the version recorded in the model file (training --version).
    ml_model_version: str = ""
    ml_model_path: str = "/models/forecast_model.json"

    # REQ-025: predictions whose confidence is below this are flagged low_confidence.
    prediction_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # REQ-024: append-only JSONL of every prediction; empty = in memory only.
    predictions_path: str = ""

    # New predictive alerts are handed to diagnostic-service for a GraphRAG
    # root-cause playbook (POST /diagnose); the alert stores its playbook_id.
    diagnostic_handoff_enabled: bool = True
    diagnostic_url: str = "http://diagnostic-service:8082"
    diagnostic_timeout_seconds: float = 360.0
    diagnostic_retries: int = 2
    diagnostic_backoff_seconds: float = 2.0

    # REQ-046: OAuth 2.0 bearer-token validation (see app/auth.py).
    oauth2_enabled: bool = False
    oauth2_jwks_url: str = ""
    oauth2_issuer: str = ""
    oauth2_audience: str = ""

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
