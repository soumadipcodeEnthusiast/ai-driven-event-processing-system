"""
config.py — runtime configuration for forecasting-service (docs/CONTRACTS.md §5).

All values come from environment variables; defaults match the contract.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    forecaster: Literal["auto", "tft", "statistical"] = "auto"
    tft_model_version: str = "0.0.0-stub"
    tft_model_path: str = "/models/tft.ckpt"

    diagnostic_url: str = "http://diagnostic-service:8082"
    diagnostic_timeout_seconds: float = 60.0
    diagnostic_retries: int = 3
    diagnostic_backoff_seconds: float = 1.0

    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
