"""Configuration layer.

WHY no module-level cache/singleton: the composition root loads config once
and injects it. A global lru_cache would be hidden shared state and would fight
tests. Precedence: code defaults < settings.yaml < environment/.env.
The permission manifest is loaded separately and is NEVER overridable by env
(it is a safety artifact).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from atlas.infra.errors import ConfigError, ManifestError


class Settings(BaseSettings):
    """Secrets + environment. Sourced ONLY from env / .env."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore", frozen=True)

    env: str = "dev"
    data_dir: Path = Path("./.atlas")
    ollama_host: str = "http://localhost:11434"
    default_model: str = "qwen3:4b"
    heavy_model: str = "qwen3:8b"
    embed_model: str = "bge-m3"
    ntfy_topic: str = ""
    ntfy_callback_base: str = "http://localhost:8730"
    # ── API keys ──────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    master_key: str = ""
    api_keys: str = ""  # comma-separated; 'ro:' prefix = readonly key (Batch 7)
    safe_browsing_api_key: str = ""
    virustotal_api_key: str = ""
    # ── Zero-cost-first policy ────────────────────────────────────────
    profile: str = "local_free"  # local_free | free_hybrid | free_demo | production
    cost_policy: str = "zero_cost"  # zero_cost | free_only | free_preferred | balanced | unrestricted
    network_policy: str = "local_only"  # offline | local_only | free_cloud | unrestricted

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
    # Phase 4 inference tiers. Logical model ids from models.yaml, in
    # preference order — index 0 is the primary for that tier. These are
    # ranking preferences applied AFTER the selector's hard policy filters, so
    # naming a paid model here cannot bypass a zero-cost profile; it simply has
    # no eligible candidates and the next preference wins.
    # WHY lists rather than a single id: Phase 4 requires a declared fallback,
    # and a single "best model" string is exactly the hard-coding it forbids.
    fast_models: tuple[str, ...] = ()
    deep_models: tuple[str, ...] = ()
    fallback_models: tuple[str, ...] = ()


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


class VerificationCfg(BaseModel):
    """Post-hoc checking of delivered work (Phase 12).

    WHY separate from ``CritiqueCfg``: self-critique reviews a *proposed*
    action before dispatch; verification checks *delivered* work against the
    intent's success criteria afterwards. They were previously sharing
    ``critique.enabled``, so disabling action review silently disabled all
    verification and every task reported itself verified.
    """

    model_config = {"frozen": True}
    enabled: bool = True
    max_replans: int = 3
    # Below this score a "passed" verdict is not trusted. WHY a floor exists:
    # a model that returns passed=true with score 0.2 is contradicting itself.
    min_pass_score: float = 0.5
    # Read-only check command for CODING tasks (e.g. "uv run pytest -q").
    # Empty by default: the command executes through the SafetyEngine like any
    # other action, so it is opt-in rather than assumed. WHY not model-supplied:
    # a verifier that runs a string the model produced would be an arbitrary
    # code execution path, which Phase 31 forbids.
    command: str = ""
    command_timeout_s: int = 120


class BrowserCfg(BaseModel):
    """Optional browser automation platform config."""

    model_config = {"frozen": True}
    enabled: bool = False
    headless: bool = True
    default_provider: str = "playwright"


class AgentsCfg(BaseModel):
    """Multi-agent specialist layer.

    Disabled by default: delegation costs one decomposition call plus one
    synthesis call on top of per-subtask reasoning, which is a bad trade for
    simple requests. Turn it on when the workload is genuinely multi-branch.
    """

    model_config = {"frozen": True}
    enabled: bool = False
    max_subtasks: int = 4  # graph ceiling; the decomposer clamps to this
    min_subtasks: int = 2  # below this, serial execution is cheaper
    max_steps_per_subtask: int = 6
    max_concurrency: int = 2  # concurrent specialists (one local GPU lane)
    max_tokens_per_subtask: int = 20_000
    subtask_runtime_s: float = 180.0  # per specialist
    deadline_s: float = 600.0  # whole delegated run
    synthesis_max_tokens: int = 2048


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
    verification: VerificationCfg = Field(default_factory=VerificationCfg)
    browser: BrowserCfg = Field(default_factory=BrowserCfg)
    agents: AgentsCfg = Field(default_factory=AgentsCfg)


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
        raise ManifestError("permissions.yaml missing or empty — refusing to run deny-by-default with no manifest")
    return raw


def resolve_master_key(settings: Settings) -> str:
    """macOS Keychain first, env fallback. WHY: never store the key on disk."""
    if sys.platform == "darwin":
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "atlas-master", "-w"], capture_output=True, text=True
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()

    if settings.master_key:  # ATLAS_MASTER_KEY env fallback (dev/CI)
        return settings.master_key

    if settings.env == "dev":
        import hashlib
        import platform

        print("WARNING: No master key found; generating stable dev key. DO NOT USE IN PRODUCTION.", file=sys.stderr)
        return hashlib.sha256(f"dev-atlas-{platform.node()}".encode()).hexdigest()

    raise ConfigError("no master key: set it in Keychain (atlas-master) or ATLAS_MASTER_KEY")
