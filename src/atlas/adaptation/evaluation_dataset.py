"""Evaluation dataset + synthetic test generation (Prompt 4 §43-§44).

§43: every evaluation sample carries task, domain, difficulty, success
criteria, allowed capabilities, risk and evaluation method. §44: variants
(paraphrase, different files/websites/data/constraints/tool availability)
test generalization — and ONLY human review can promote a variant to a
golden benchmark.
"""

from __future__ import annotations

import json

from atlas.adaptation.domain import EvalSample, SyntheticVariant, VariantKind
from atlas.infra.clock import Clock, SystemClock
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.adaptation.dataset")

#: Deterministic variation clause per variant kind (§44). The generator is
#: honest about what it does: it appends an explicit variation instruction,
#: it never pretends to rewrite task semantics invisibly.
_VARIANT_MODIFIER: dict[VariantKind, str] = {
    VariantKind.PARAPHRASE: "expressed with different wording",
    VariantKind.DIFFERENT_FILES: "using different files",
    VariantKind.DIFFERENT_WEBSITES: "on a different website",
    VariantKind.DIFFERENT_DATA: "with different data",
    VariantKind.DIFFERENT_CONSTRAINTS: "under different constraints",
    VariantKind.DIFFERENT_TOOL_AVAILABILITY: "with reduced tool availability",
}


class EvalDatasetStore:
    """Persists the ATLAS evaluation dataset (§43)."""

    def __init__(self, *, db: Database) -> None:
        self._db = db

    async def save(self, sample: EvalSample) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO eval_samples (
                sample_id, task, domain, difficulty, success_criteria,
                allowed_capabilities_json, risk, evaluation_method, source,
                approved, created_ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sample.sample_id,
                sample.task,
                sample.domain,
                sample.difficulty,
                sample.success_criteria,
                json.dumps(list(sample.allowed_capabilities)),
                sample.risk,
                sample.evaluation_method,
                sample.source,
                int(sample.approved),
                sample.created_ts,
            ),
        )
        await self._db.conn.commit()

    async def get(self, sample_id: str) -> EvalSample | None:
        cur = await self._db.conn.execute("SELECT * FROM eval_samples WHERE sample_id=?", (sample_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return _sample_from_row(dict(row))

    async def samples(self, *, approved_only: bool = True) -> tuple[EvalSample, ...]:
        query = "SELECT * FROM eval_samples"
        if approved_only:
            query += " WHERE approved=1"
        query += " ORDER BY created_ts"
        cur = await self._db.conn.execute(query)
        rows = await cur.fetchall()
        return tuple(_sample_from_row(dict(row)) for row in rows)


class SyntheticGenerator:
    """§44: generates deterministic task variants and enforces the human
    review path before anything becomes golden."""

    def __init__(self, *, db: Database, clock: Clock | None = None) -> None:
        self._db = db
        self._clock = clock or SystemClock()

    async def generate(self, sample: EvalSample, kind: VariantKind) -> SyntheticVariant:
        variant = SyntheticVariant(
            source_sample_id=sample.sample_id,
            kind=kind,
            task=f"{sample.task} ({_VARIANT_MODIFIER[kind]})",
            created_ts=self._clock.now().isoformat(),
        )
        await self._save(variant)
        _log.info(
            "dataset.variant_created",
            event_type="adaptation",
            source=sample.sample_id,
            kind=kind.value,
        )
        return variant

    async def review(self, variant_id: str, status: str) -> SyntheticVariant:
        """Human review (§44). Only this path can promote DRAFT -> GOLDEN;
        status must be APPROVED, REJECTED or GOLDEN."""
        if status not in ("APPROVED", "REJECTED", "GOLDEN"):
            msg = f"review status must be APPROVED/REJECTED/GOLDEN, got {status}"
            raise ValueError(msg)
        variant = await self.get(variant_id)
        if variant is None:
            msg = f"unknown variant: {variant_id}"
            raise KeyError(msg)
        if variant.status not in ("DRAFT", "APPROVED"):
            msg = f"variant {variant_id} already finalized as {variant.status}"
            raise ValueError(msg)
        updated = SyntheticVariant(
            variant_id=variant.variant_id,
            source_sample_id=variant.source_sample_id,
            kind=variant.kind,
            task=variant.task,
            status=status,  # type: ignore[arg-type]
            created_ts=variant.created_ts,
        )
        await self._save(updated)
        return updated

    async def golden_variants(self) -> tuple[SyntheticVariant, ...]:
        cur = await self._db.conn.execute("SELECT * FROM synthetic_variants WHERE status='GOLDEN' ORDER BY created_ts")
        rows = await cur.fetchall()
        return tuple(_variant_from_row(dict(row)) for row in rows)

    async def get(self, variant_id: str) -> SyntheticVariant | None:
        cur = await self._db.conn.execute("SELECT * FROM synthetic_variants WHERE variant_id=?", (variant_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return _variant_from_row(dict(row))

    async def _save(self, variant: SyntheticVariant) -> None:
        await self._db.conn.execute(
            """
            INSERT OR REPLACE INTO synthetic_variants (
                variant_id, source_sample_id, kind, task, status, created_ts
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                variant.variant_id,
                variant.source_sample_id,
                variant.kind.value,
                variant.task,
                variant.status,
                variant.created_ts,
            ),
        )
        await self._db.conn.commit()


def _sample_from_row(d: dict[str, object]) -> EvalSample:
    return EvalSample(
        sample_id=str(d["sample_id"]),
        task=str(d["task"]),
        domain=str(d["domain"]),
        difficulty=d["difficulty"],  # type: ignore[arg-type]
        success_criteria=str(d["success_criteria"]),
        allowed_capabilities=tuple(json.loads(str(d["allowed_capabilities_json"]))),
        risk=d["risk"],  # type: ignore[arg-type]
        evaluation_method=str(d["evaluation_method"]),
        source=d["source"],  # type: ignore[arg-type]
        approved=bool(d["approved"]),
        created_ts=str(d["created_ts"]),
    )


def _variant_from_row(d: dict[str, object]) -> SyntheticVariant:
    return SyntheticVariant(
        variant_id=str(d["variant_id"]),
        source_sample_id=str(d["source_sample_id"]),
        kind=VariantKind(str(d["kind"])),
        task=str(d["task"]),
        status=d["status"],  # type: ignore[arg-type]
        created_ts=str(d["created_ts"]),
    )


__all__ = ["EvalDatasetStore", "SyntheticGenerator"]
