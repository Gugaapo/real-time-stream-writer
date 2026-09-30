"""Runtime settings. Init arguments win over the environment."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "livrinho.sqlite"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8005
    cors_origins: str = "https://tossemideia.cloud"
    database_path: str = str(_DEFAULT_DB)
    deepseek_api_key: str = ""
    channel: str = "omeiaum"
    novel_base_url: str = "https://api.deepseek.com"
    novel_model: str = "deepseek-v4-flash-vision-exp"
    interval_seconds: float = 15.0
    change_threshold: float = 1.5
    grace_seconds: float = 45.0
    max_live_sessions: int = 8
    frame_width: int = 640
    frame_height: int = 360
    fps: float = 2.0
    monthly_cap_usd: float = 10.0


def get_settings() -> Settings:
    return Settings()
