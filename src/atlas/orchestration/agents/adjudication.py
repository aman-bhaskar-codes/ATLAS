"""Deterministic conflict detection across specialist outputs.

WHY this is not a model call: a delegated run's whole risk is that two branches
report incompatible things and synthesis quietly picks one. Asking a model
"do these contradict?" replaces one unverified claim with another. This module
answers the narrow version of that question that a machine can answer with
certainty, and answers nothing else.

WHAT it detects (both require an *identical sentence skeleton*, which is what
keeps false positives rare):
  1. polarity conflict — the same sentence asserted and negated;
  2. numeric conflict — the same sentence with different numbers.

WHY conservative rather than clever: the only effect of a detected conflict is
to downgrade the run to ``uncertain``. Under-detection leaves an existing gap;
over-detection destroys good answers. When in doubt this module stays silent —
verification, not adjudication, is the primary gate.

Nothing here votes. A conflict is never resolved by counting agents (§5.11);
it is reported so the run stops claiming a verified result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel

from atlas.orchestration.agents.types import SubTaskResult

__all__ = ["ClaimConflict", "detect_conflicts"]

# Negations that flip a sentence's polarity without removing a word we need for
# the skeleton comparison.
_BARE_NEGATIONS = frozenset({"not", "never", "no", "none", "nor", "neither"})

# Contracted negations carry their verb. Dropping them wholesale would change
# the skeleton, so they are rewritten to the positive verb and counted.
_CONTRACTED_NEGATIONS = {
    "isnt": "is",
    "arent": "are",
    "wasnt": "was",
    "werent": "were",
    "doesnt": "does",
    "dont": "do",
    "didnt": "did",
    "wont": "will",
    "cant": "can",
    "cannot": "can",
    "couldnt": "could",
    "shouldnt": "should",
    "wouldnt": "would",
    "hasnt": "has",
    "havent": "have",
    "hadnt": "had",
}

_SENTENCE_SPLIT = re.compile(r"[.!?\n;]+")
_APOSTROPHES = str.maketrans({"'": "", "\u2019": ""})  # straight and curly apostrophes
_WORD = re.compile(r"[a-z0-9][a-z0-9.,%$-]*")
_NUMERIC = re.compile(r"^[$]?-?\d[\d,]*(?:\.\d+)?%?$")

_MIN_SKELETON_TOKENS = 5  # below this, "identical skeleton" means nothing
_MAX_SENTENCES_PER_RESULT = 80  # bound the scan; specialist output is capped anyway
_MAX_CONFLICTS = 5  # a report, not an exhaustive diff


class ClaimConflict(BaseModel):
    """Two succeeded subtasks made incompatible statements."""

    model_config = {"frozen": True}

    kind: str  # "polarity" | "numeric"
    subtask_a: str
    subtask_b: str
    statement_a: str
    statement_b: str

    def describe(self) -> str:
        return (
            f"{self.kind} conflict between {self.subtask_a} and {self.subtask_b}: "
            f"'{self.statement_a}' vs '{self.statement_b}'"
        )


def detect_conflicts(results: Iterable[SubTaskResult]) -> tuple[ClaimConflict, ...]:
    """Find incompatible statements across *succeeded* subtasks.

    Failed and skipped subtasks are ignored: their output is absent or is an
    error string, and treating an error message as a claim would manufacture
    conflicts out of failures.
    """
    # skeleton -> (subtask_id, sentence, polarity, numbers)
    seen: dict[tuple[str, ...], tuple[str, str, bool, tuple[str, ...]]] = {}
    conflicts: list[ClaimConflict] = []

    for r in results:
        if not r.ok or not r.output:
            continue
        for sentence in _sentences(r.output):
            parsed = _parse(sentence)
            if parsed is None:
                continue
            skeleton, polarity, numbers = parsed
            prior = seen.get(skeleton)
            if prior is None:
                seen[skeleton] = (r.subtask_id, sentence, polarity, numbers)
                continue
            prior_id, prior_sentence, prior_polarity, prior_numbers = prior
            if prior_id == r.subtask_id:
                # One agent contradicting itself is a quality problem for that
                # agent, not a cross-branch conflict. Out of scope here.
                continue
            kind = ""
            if polarity != prior_polarity:
                kind = "polarity"
            elif numbers != prior_numbers:
                kind = "numeric"
            if kind:
                conflicts.append(
                    ClaimConflict(
                        kind=kind,
                        subtask_a=prior_id,
                        subtask_b=r.subtask_id,
                        statement_a=prior_sentence,
                        statement_b=sentence,
                    )
                )

    conflicts.sort(key=lambda c: (c.subtask_a, c.subtask_b, c.kind, c.statement_a))
    return tuple(conflicts[:_MAX_CONFLICTS])


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for raw in _SENTENCE_SPLIT.split(text):
        s = raw.strip()
        if s:
            out.append(s)
        if len(out) >= _MAX_SENTENCES_PER_RESULT:
            break
    return out


def _parse(sentence: str) -> tuple[tuple[str, ...], bool, tuple[str, ...]] | None:
    """Return (skeleton, negated, numbers), or None when not comparable.

    The skeleton excludes numbers so that "the suite has 12 tests" and "the
    suite has 40 tests" collide; polarity is tracked separately so that
    "X is reachable" and "X is not reachable" collide too.
    """
    tokens = _WORD.findall(sentence.lower().translate(_APOSTROPHES))
    if not tokens:
        return None

    negations = 0
    skeleton: list[str] = []
    numbers: list[str] = []
    for token in tokens:
        word = token.strip(".,")
        if not word:
            continue
        if word in _BARE_NEGATIONS:
            negations += 1
            continue
        if word in _CONTRACTED_NEGATIONS:
            negations += 1
            skeleton.append(_CONTRACTED_NEGATIONS[word])
            continue
        if _NUMERIC.match(word):
            numbers.append(_normalize_number(word))
            continue
        skeleton.append(word)

    if len(skeleton) < _MIN_SKELETON_TOKENS:
        return None
    return tuple(skeleton), negations % 2 == 1, tuple(numbers)


def _normalize_number(word: str) -> str:
    """So that "1,000", "1000" and "1000.0" compare equal."""
    suffix = "%" if word.endswith("%") else ""
    cleaned = word.lstrip("$").rstrip("%").replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return word
    return (f"{value:g}") + suffix
