#!/usr/bin/env python3
"""Regression gate: score recorded answers against the golden suite.

Usage:
    uv run python scripts/eval_gate.py --answers eval/recorded/answers.json

The answers file maps golden task ids to answer strings (recorded from a live
agent run or hand-maintained). Exit code 0 = gate passed (no failures, no
regressions vs. previously recorded results); nonzero otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from atlas.evaluation.golden import load_golden_suite  # noqa: E402
from atlas.evaluation.service import EvaluationService, EvaluationStore  # noqa: E402
from atlas.infra.clock import SystemClock  # noqa: E402
from atlas.infra.db import Database  # noqa: E402
from atlas.infra.ids import UuidGenerator  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", required=True, type=Path, help="JSON file of {golden_id: answer}")
    parser.add_argument("--suite", type=Path, default=REPO_ROOT / "eval" / "golden_tasks" / "core.yaml")
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "atlas.db")
    args = parser.parse_args()

    tasks = load_golden_suite(args.suite)
    answers = json.loads(args.answers.read_text())

    unknown = set(answers) - {t.id for t in tasks}
    if unknown:
        print(f"ERROR: answers reference unknown golden ids: {sorted(unknown)}", file=sys.stderr)
        return 2

    args.db.parent.mkdir(parents=True, exist_ok=True)
    db = Database(args.db)
    await db.start()
    try:
        store = EvaluationStore(db, UuidGenerator(), SystemClock())
        svc = EvaluationService(store=store, ids=UuidGenerator())
        report = await svc.run_suite(tasks, answers)
    finally:
        await db.stop()

    print(f"run {report.run_id}: {report.passed}/{report.total} passed (success rate {report.success_rate:.0%})")
    for gid, result in report.results.items():
        status = "PASS" if result.passed else "FAIL"
        print(
            f"  [{status}] {gid} (score {result.score:.2f})"
            + (f" — {result.failure_reason}" if result.failure_reason else "")
        )
    if report.regressions:
        print(f"REGRESSIONS: {report.regressions}", file=sys.stderr)
        return 1
    if report.failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
