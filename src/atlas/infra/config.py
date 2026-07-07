"""Configuration layer.

WHY no module-level cache/singleton: the composition root loads config once
and injects it. A global lru_cache would be hidden shared state and would fight
tests. Precedence: code defaults < settings.yaml < environment/.env.
The permission manifest is loaded separately and is NEVER overridable by env
(it is a safety artifact).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from atlas.infra.errors import ConfigError, ManifestError


class Settings(BaseSettings):
    """Secrets + environment. Sourced ONLY from env / .env."""

    model_config = SettingsConfigDict(
        env_prefix="ATLAS_", env_file=".env", extra="ignore", frozen=True
    )

    env: str = "dev"
    data_dir: Path = Path("./.atlas")
    ollama_host: str = "http://localhost:11434"
    default_model: str = "qwen3:4b"
    heavy_model: str = "qwen3:8b"
    embed_model: str = "bge-m3"
    ntfy_topic: str = ""
    ntfy_callback_base: str = "http://localhost:8730"
    deepseek_api_key: str = ""
    glm_api_key: str = ""
    kimi_api_key: str = ""
    mimo_api_key: str = ""

    def db_path(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self.data_dir / "atlas.db"


class LoggingCfg(BaseModel):
    model_config = {"frozen": True}
    level: str = "INFO"
    format: str = "console"


class ModelCfg(BaseModel):
    model_config = {"frozen": True}
    gpu_concurrency: int = 1
    local_timeout_s: float = 120.0
    cloud_timeout_s: float = 90.0
    allow_cloud: bool = False
    daily_usd: float = 1.0
    weekly_usd: float = 5.0
    monthly_usd: float = 15.0
    per_task_usd: float = 0.50


class SafetyCfg(BaseModel):
    model_config = {"frozen": True}
    stop_flag_path: str = "STOP.flag"
    confirm_timeout_s: float = 300.0
    default_tier_on_error: int = 2


class NotifyCfg(BaseModel):
    model_config = {"frozen": True}
    confirm_timeout_s: float = 300.0
    quiet_hours: dict[str, str] | None = None


class MetricsCfg(BaseModel):
    model_config = {"frozen": True}
    snapshot_interval_s: float = 60.0


class TracingCfg(BaseModel):
    model_config = {"frozen": True}
    enabled: bool = True


class SandboxCfg(BaseModel):
    model_config = {"frozen": True}
    image: str = "python:3.13-slim"
    cpus: float = 1.0
    memory: str = "512m"
    pids_limit: int = 128
    workdir: str = "/work"


class MemoryCfg(BaseModel):
    model_config = {"frozen": True}
    token_budget: int = 1500
    auto_apply_confidence: float = 0.8
    hot_days: int = 30
    max_episodes: int = 20_000
    keep_superseded_days: int = 90


class CritiqueCfg(BaseModel):
    model_config = {"frozen": True}
    enabled: bool = True
    min_tier: int = 2
    revise_max: int = 1


class AppConfig(BaseModel):
    model_config = {"frozen": True}
    logging: LoggingCfg = Field(default_factory=LoggingCfg)
    models: ModelCfg = Field(default_factory=ModelCfg)
    safety: SafetyCfg = Field(default_factory=SafetyCfg)
    notify: NotifyCfg = Field(default_factory=NotifyCfg)
    metrics: MetricsCfg = Field(default_factory=MetricsCfg)
    tracing: TracingCfg = Field(default_factory=TracingCfg)
    sandbox: SandboxCfg = Field(default_factory=SandboxCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    critique: CritiqueCfg = Field(default_factory=CritiqueCfg)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def load_settings() -> Settings:
    return Settings()


def load_app_config(config_dir: Path) -> AppConfig:
    raw = _read_yaml(config_dir / "settings.yaml")
    try:
        return AppConfig(**raw)
    except Exception as exc:  # pydantic ValidationError -> fatal config error
        raise ConfigError(f"invalid settings.yaml: {exc}") from exc


def load_permissions(config_dir: Path) -> dict[str, Any]:
    raw = _read_yaml(config_dir / "permissions.yaml")
    if not raw:
        raise ManifestError(
            "permissions.yaml missing or empty — refusing to run deny-by-default with no manifest"
        )
    return raw
