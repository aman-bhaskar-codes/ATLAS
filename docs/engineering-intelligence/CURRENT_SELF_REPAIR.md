# CURRENT_SELF_REPAIR — what ATLAS can already change about itself

**Method.** Read from the tree this pass, including `pyproject.toml` in full (109 lines),
`adaptation/` module listing, and the `_MIGRATIONS`/DDL region of `infra/db.py`.

Short version: **ATLAS can already change its own behaviour. It cannot change its own code,
and nothing in the tree is set up to try.**

---

## 1. What self-modification exists today — ✅ behavioural, gated, reversible

The `adaptation/` package is a complete learn-and-change loop, and it is genuinely
self-modifying in the sense that matters:

```
failure records → clustering → hypothesis → experiment → shadow → canary
   → generalization → promotion decision → new strategy version
```

Backed by `hypotheses`, `experiments`, `shadow_comparisons`, `canary_deployments`,
`canary_observations`, `generalization_results`, `promotion_decisions`, `strategy_versions`,
`strategy_performance`, `adaptation_events`, `negative_experiences`.

What it changes: **strategy**. `AllowedChangeType` enumerates the permitted mutations and they
are all behavioural — routing, workflow ordering, context compilation, and similar. Nothing in
this loop writes a file.

Its safeguards, which §27–§34 should mirror rather than reinvent:

| Safeguard | Implementation |
|---|---|
| Change types are an allow-list | `AllowedChangeType` |
| Riskier changes need more evidence | `MIN_EVIDENCE_DEFAULT = 3`, `MIN_EVIDENCE_HIGH_RISK = 5` |
| Which changes are "risky" is declared | `HIGH_RISK_CHANGE_TYPES = {MODEL_ROUTING, WORKFLOW_ORDERING, CONTEXT_COMPILATION}` |
| Some failures are categorically off-limits | `SAFETY_BLOCK`, `AUTH_FAILURE`, `USER_CONSTRAINT_FAILURE`, `ENVIRONMENT_FAILURE` → change type `None` |
| No duplicate open changes per component | `HypothesisStore.exists_for_component` |
| Every change is versioned and revertible | `strategy_versions` |
| Change is proven before exposure | `experiments` → `shadow` → `canary` → `promotion` |

`Atlas` also carries `checkpoints` and `skill_promoter`, and `RuntimeSupervisor` has a
`RECOVERING` state — so "revert to a known-good configuration" has precedent.

---

## 2. Runtime self-healing — ✅ narrow but real

- `infra/circuit_breaker.py` + `HealthMonitor` per-provider breaker: a failing provider is
  taken out of rotation and probed back in.
- `events` table retry columns (`attempt_count`, `next_retry_at`) — undelivered events are
  retried, then dead-lettered.
- `orchestration/monitor.py::ExecutionMonitor.is_recoverable(exc)` — a retryable/fatal split.
- `AtlasError.retryable` — declared per error class, not guessed per call site.
- `Database._apply_migrations` writes `schema_version` **per step** inside the loop, so a
  mid-sequence migration failure is resumable rather than bricking the database.
- The startup backup (`infra/backup.py`) keeps N archives with a cooldown.

This is failure *tolerance*. None of it diagnoses a cause or proposes a fix.

---

## 3. What does **not** exist

| §  | Requirement | State |
|---|---|---|
| 29 | Self-repair pipeline for the system itself | ❌ |
| 30 | Isolated Git branch/worktree keyed to `incident_id` | ❌ — see §4 below |
| 31 | Patch generation via ModelGateway, verified by the runtime | ❌ |
| 32 | Patch boundary deny-list (`src/atlas/safety/**`, `credentials/**`, deployment secrets) | ❌ |
| 33 | Mandatory security gate returning `HUMAN_REVIEW_REQUIRED` | ❌ |
| 34 | Auto-repair eligibility restricted to low-risk classes | ⚠️ precedent in `AllowedChangeType`, nothing for code |
| 35 | Regression test that fails before and passes after | ❌ |
| 36/37 | Full validation pipeline; "no 'tests pass' as sole proof" | ❌ |
| 64 | Automatic rollback preserving evidence | ⚠️ strategy rollback exists; no patch rollback |
| 84 | Repair limits — stop and escalate after two failures | ❌ |
| 85 | Repair loop guard (`repair_chain_id`, `depth`, `parent_incident`) | ❌ |
| 86 | Full repair audit | ⚠️ `audit_events` hash chain exists and is unused by any repair path |
| 70/118 | Diff view / repair preview | ❌ |

There is **no `incidents` table** among the 88 that exist, and therefore nothing for a repair
to attach to.

---

## 4. Concrete implementation constraints for §30–§31

These come from `pyproject.toml` and `importlinter.ini`, and they shape the code before it is
written:

**No git library is available.** Dependencies are pydantic, pydantic-settings, pyyaml,
aiosqlite, httpx, typer, rich, structlog, chromadb, cryptography, feedparser,
email-validator, playwright, fastapi, uvicorn, python-multipart, websockets, prompt-toolkit.
**No GitPython, no dulwich.** §30's branch/worktree isolation must therefore drive the `git`
binary as a subprocess — and it must be `asyncio.create_subprocess_exec`, not
`subprocess.run`, because ruff's `ASYNC220` (blocking subprocess in an async function) is
enabled tree-wide and is per-file-ignored **only** for `src/atlas/capabilities/browser/*`.
Adding a new per-file ignore to run `git` synchronously would be the wrong trade.

**Blocking path operations in async will trip `ASYNC240`.** Repair work is inherently
path-heavy. The tree's precedent is a per-file ignore *with a stated reason* when the
operation is sub-millisecond and local (e.g. `"src/atlas/memory/episodic.py" =
["ASYNC240"] # event-store path check, sub-ms`). Reading a candidate diff or walking a
worktree is **not** sub-millisecond, so the correct answer is `asyncio.to_thread`, not a new
ignore line.

**`RUF006` is deliberately not ignored** — `ignore = []`. The comment in `pyproject.toml`
explains: a discarded `create_task` reference is garbage-collectable mid-flight and silently
dropped background work. Any long-running repair or detector task must go through
`atlas.infra.tasks.spawn(coro, name=...)`.

**mypy is `strict = true` over `packages = ["atlas"]`** — so `atlas.engineering` is fully
strict-checked from its first line. `src/atlas_cli` is not checked at all, which is another
reason CLI command bodies must stay thin.

**Safety is *below* the new layer.** `importlinter.ini` places `atlas.safety` far down the
stack; `atlas.engineering` (planned between `atlas.diagnostics` and `atlas.adaptation`) may
import it, and it may never import upward. So the security gate is a call *from* engineering
*into* safety. Good: the authoriser cannot depend on the thing it authorises.

**Git root is unconfirmed.** This environment reports "Is a git repository: false" for the
working directory. `git rev-parse --show-toplevel` must be run before any code assumes a
repository exists, and §30's worktree isolation must degrade honestly — an incident whose
repair requires a worktree, in a tree that is not a git repository, is
`HUMAN_REVIEW_REQUIRED`, not a silent in-place edit.

---

## 5. Migration mechanics for the `incidents` tables

DB migrations are **inline** in `src/atlas/infra/db.py` as
`_MIGRATIONS: tuple[str, ...] = (` at line 20, closing at line 1315. Each element is a
triple-quoted `executescript` body preceded by a `#`-comment. The last comment is
`# 021 — Prompt 4: domain feedback loops, verification, capability stats, autonomy modes
(§54-§74)`, ending at line 1314.

`src/atlas/infra/migrations/` holds only a vestigial `007_idempotency_keys.sql` — it is not
the mechanism.

To add a migration: **append a new string to the tuple**, with the next sequential comment
(`# 022 — …`). No index bookkeeping is needed — `_apply_migrations` iterates
`_MIGRATIONS[current:]` with `enumerate(..., start=current + 1)` and writes `schema_version`
after each successful step (line ~1370). `_log.info("db.ready", …, version=len(_MIGRATIONS))`
reports the count. Note the comment *numbers* are inconsistent earlier in the tuple (015 and
016 each appear twice), so **tuple position is authoritative, comments are labels.**

Migrations must be additive and re-runnable in spirit: the tree's DDL uses
`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` throughout. Bare
`ALTER TABLE … ADD COLUMN` is not idempotent, which is exactly why the per-step
`schema_version` write matters.

---

## 6. The correct shape of the new pipeline

Per §29 ("Never: incident → direct production source modification") and §31 ("the model does
not decide whether patch is accepted. The runtime verifies"), reusing what exists:

```
Incident (new)
  → RootCauseAnalyzer (new; calls FailureAnalyzer when a trajectory exists)
    → RepairHypothesis (new; shaped like adaptation's Hypothesis)
      → eligibility check  (new; mirrors AllowedChangeType + evidence floors)
      → SECURITY GATE      → SafetyEngine.guard()      [existing]
      → patch boundary deny-list (new — path check, before any write)
      → isolated worktree keyed to incident_id (new; git via subprocess)
      → fail-before regression test capture (new)
      → EvaluationService.run_suite → RunReport.gate_passed  [existing]
      → adaptation canary / promotion                        [existing]
      → audit_events hash chain                              [existing]
      → promote or roll back, evidence preserved (new rollback for patches)
```

Everything marked `[existing]` must be **called**, not reimplemented — that is §1's
instruction. Everything marked `(new)` is a genuine gap.
