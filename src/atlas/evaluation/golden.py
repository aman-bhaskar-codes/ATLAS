"""Golden tasks — deterministic evaluation fixtures for agent behavior.

WHY a declarative YAML suite: golden tasks must be reviewable, versionable,
and runnable without a live model. Each task pairs a prompt-shaped input with
the criteria a correct answer must satisfy. Deterministic criteria run in CI;
LLM-judge criteria run behind the gated suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class MatchSpec(BaseModel):
    """Deterministic criteria applied to the agent's answer text."""

    contains_all: tuple[str, ...] = ()  # every substring must appear (case-insensitive)
    contains_any: tuple[str, ...] = ()  # at least one must appear
    contains_none: tuple[str, ...] = ()  # forbidden substrings (safety regressions)
    regex_all: tuple[str, ...] = ()  # every regex must match (search)
    min_length: int = 0  # answer must be at least this many chars
    max_length: int = 0  # 0 = unbounded


class GoldenTask(BaseModel):
    """A single evaluation fixture."""

    model_config = {"frozen": True}

    id: str
    category: str  # research | coding | filesystem | analysis | communication | safety
    prompt: str
    expected: MatchSpec = Field(default_factory=MatchSpec)
    use_llm_judge: bool = False  # additionally score with the judge model
    timeout_s: float = 120.0
    max_cost_usd: float = 0.05
    tags: tuple[str, ...] = ()


def load_golden_suite(path: Path) -> tuple[GoldenTask, ...]:
    """Load golden tasks from a YAML file (list of task mappings)."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"golden task file {path} must contain a YAML list")
    tasks = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"golden task #{i} in {path} must be a mapping")
        tasks.append(GoldenTask(**_norm(item)))
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate golden task ids in {path}")
    return tuple(tasks)


def _norm(item: dict[str, Any]) -> dict[str, Any]:
    # YAML lists → tuples for frozen pydantic models.
    out = dict(item)
    exp = out.get("expected") or {}
    for key in ("contains_all", "contains_any", "contains_none", "regex_all"):
        if isinstance(exp.get(key), list):
            exp[key] = tuple(exp[key])
    if isinstance(out.get("tags"), list):
        out["tags"] = tuple(out["tags"])
    if exp:
        out["expected"] = exp
    return out
