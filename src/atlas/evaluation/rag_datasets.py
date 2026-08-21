"""RAG evaluation datasets (§64, §107).

JSONL format, one entry per line:
    {"query": ..., "ground_truth": ..., "expected_uris": [...], "category": ...}

A tiny builtin smoke dataset ships with the code so the eval layer is
testable without external fixtures; real datasets live under eval/rag/.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class EvalEntry:
    query: str
    ground_truth: str = ""
    expected_uris: tuple[str, ...] = ()
    category: str = "general"  # mirrors the §134 golden-test categories


@dataclass
class EvalDataset:
    name: str
    entries: list[EvalEntry] = field(default_factory=list)

    def by_category(self, category: str) -> list[EvalEntry]:
        return [e for e in self.entries if e.category == category]


def load_jsonl(path: Path, *, name: str = "") -> EvalDataset:
    entries: list[EvalEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        entries.append(
            EvalEntry(
                query=str(raw.get("query", "")),
                ground_truth=str(raw.get("ground_truth", "")),
                expected_uris=tuple(raw.get("expected_uris", ())),
                category=str(raw.get("category", "general")),
            )
        )
    return EvalDataset(name=name or path.stem, entries=entries)


def builtin_smoke_dataset() -> EvalDataset:
    """Four entries exercising the main metric paths (no external data)."""
    return EvalDataset(
        name="builtin_smoke",
        entries=[
            EvalEntry(
                query="What is the safety tier for sending email in ATLAS?",
                ground_truth="Sending email is a Tier-2 action requiring preview and approval.",
                category="simple_fact",
            ),
            EvalEntry(
                query="How does hybrid retrieval combine lexical and dense results?",
                ground_truth="Lexical BM25 and dense vector results are fused with reciprocal rank fusion.",
                category="mechanism",
            ),
            EvalEntry(
                query="What happened in the meeting I never had on Mars?",
                ground_truth="",
                category="unanswerable",
            ),
            EvalEntry(
                query="Which component indexes chunks for lexical search?",
                ground_truth="The BM25 index stores tokenized chunks for lexical retrieval.",
                category="codebase",
            ),
        ],
    )
