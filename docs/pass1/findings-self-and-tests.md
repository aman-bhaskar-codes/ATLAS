# Phase 0 Findings — Self-Knowledge, Tests, Quality Gates

## Part A — Self-knowledge: almost nothing exists

Global greps returning **ZERO hits** across `src/ tests/ scripts/ benchmarks/ config/ docs/`:
`SelfModel`, `SelfIndex`, `self_index`, `self_model`, `LimitRegistry`, `limitations`,
`what_can_i_do`, `ast.parse`, `import ast`, `symbol_index`, `repo_index`, `code_search`,
sha256-over-source-files, git introspection (`rev-parse`/GitPython/dulwich), `/version` route.

All 14 `hashlib` sites hash **user data**, never ATLAS's own source.

| Dimension | Verdict |
|---|---|
| Own version | **PARTIAL + CONFLICTING** — `atlas/__init__.py:3` `"0.1.0"` is **never read**; `facade.py:81` hardcodes `version="1.0.0"`, contradicting `pyproject.toml:3`. This is the only version reported over HTTP. |
| Git commit / build SHA | **ABSENT** — no git introspection; Dockerfile injects no build arg |
| Enabled capabilities | **EXISTS but scattered** — `routes_capabilities.py:12`, `routes_ops.py:67,92`, `routes_providers.py:78`. No unified self-catalog, no LLM-consumable description |
| Provider health | **EXISTS** — `routes_providers.py:16`, `routes_ops.py:114`, `health.py:120`, `health_monitor.py:30` |
| Config profile | **EXISTS** — `routes_providers.py:49`, `infra/profiles.py:23`, CLI `main.py:948` |
| DB schema version | **EXISTS internally, NOT EXPOSED** — `infra/db.py:650-667`; no route/CLI/doctor surfaces the number |
| Limitations | **ABSENT** |
| **Own source code** | **ABSENT, completely** |

Nearest adjacent things that are *not* self-knowledge: `orchestration/limits.py` (resource
budgets); `RuntimeSupervisor.get_unavailable_capabilities()` (transient degradation, not
declared limits); `TECHNICAL_DEBT.md` (hand-maintained markdown, not machine-readable).

Duplicate/conflicting route: `routes_capabilities.py:20-26` also registers
`/providers/health` returning `RuntimeHealthResponse`, with a
`# Phase 1: reusing runtime_health for now` comment.

**`diagnostics/doctor.py`** (172 lines, 14 checks). `_REGISTERED_TOOLS` is a **hardcoded
dict** at :31-34 (`filesystem`, `shell`) — it does not read the live tool registry, so a
newly registered tool is invisible to the manifest verifier. `future.providers` :165-167 is a
hardcoded literal that checks nothing. The doctor reports `env=` but **not the profile**, and
reports DB as a boolean but **not the schema version**.

**`evaluation/`** (4 files, 431 lines) is *not* self-knowledge — it scores answers against
caller-supplied golden fixtures.

**Must be built for Phases 16-19:** `SelfModel`, `SelfIndex` (repo+symbol index with source
hashing), `LimitRegistry`, unified capability catalog, git-commit reporting, DB-version
exposure.

## Part B — Tests

**117 `.py` files, 89 `test_*.py`, 9,341 lines, 408 `def test_`.**

Largest: `tests/orchestration` (17 files, 1,804 lines, 100 tests), `tests/memory` (9/1,498/55),
`tests/intelligence` (8/778/59), `tests/free_first` (1/409/23), `tests/interfaces` (4/463/24).

**There is exactly ONE conftest.py** — `tests/conftest.py`, 46 lines, 3 fixtures:
`setup_logging` (session autouse), `memory_db` (**real** `Database` + real migrations),
`manifest_dir` (writes a **fully permissive** `permissions.yaml`: `rules: [{tool:"*",
operation:"*", tier:0}]`, `hard_block: []`, `allowed_paths: ["*"]`).

**No conftest fixture builds an `Atlas`.** There is no shared `atlas` fixture in the suite.
15 separate test files each re-implement their own real-`Database` fixture.

**The ONLY place a real `Atlas` is constructed in the entire suite is
`tests/e2e/test_first_light.py`** (4× `await build()` at :68, :149, :207, :257).

`tests/contract/` is an **empty stub dir** (only `__init__.py`, 0 tests). 12 test dirs lack
`__init__.py`, including `tests/e2e`. Two never-collected scripts sit at repo root outside
`testpaths`: `test_app_routes.py`, `_verify_build.py`.

### tests/e2e/test_first_light.py — 4 tests, 282 lines

All four patch exactly 3 things: `OllamaEmbedder.embed` → `[0.0]*1024`;
`OllamaProvider.complete` → a fixed payload; `DockerSandbox.health` → `False` (forces
NativeSandbox). **Everything else is real** — SQLite, migrations, Chroma, safety engine, tool
registry, RuntimeSupervisor, orchestrator, planner, router, reasoning loop.

| Test | Calls orchestrator? | Status |
|---|---|---|
| `test_first_light_simple_task` | yes :88 | **FAILS** |
| `test_runtime_health_endpoints` | no (and touches **no HTTP endpoint** — misnamed) | passes |
| `test_graceful_shutdown` | no | passes |
| `test_task_state_transitions` | yes :269 | **FAILS** |

The two that pass are precisely the two that never call `orchestrator.run()` — localizing the
fault to the model-consuming path, not to `build()`/`start()`.

All four mutate `os.environ` **without monkeypatch** (:37-39, :120-121, :178-180, :228-230),
leaking `ATLAS_DATA_DIR` and `ATLAS_ENV` into every subsequently-collected test.

## Part C — Root cause of the 2 failures (confirmed)

**The stub returns prose; the planner demands a JSON object.**

Stub `test_first_light.py:56-59` → `text="I found 42 Python files in the repository."`
and :246-249 → `text="Task completed successfully"`. Neither contains `{` or `}`.

Parser `orchestration/plan_parsing.py:63-69`:
```python
s, e = text.find("{"), text.rfind("}")
if s == -1 or e == -1:
    raise ValueError("no JSON object in response")
```

Chain:
1. `test:88` → `orchestrator.run(event)`
2. Mock **is** reached; `resp.text` is the prose verbatim
3. `orchestrator.py:127` → `router.route()` → `ValueError("no JSON")` at `router.py:87` is
   **caught** at :57 and degrades to `Capabilities(needs_tools=True, ...)` at :65 — survivable
4. `orchestrator.py:141` → `planner.plan()`
5. `planner.py:70` → `extract_json_object` raises at `plan_parsing.py:68`
6. `planner.py:72-73` catches and **re-raises** `PlanningError`
7. **`Orchestrator.run` has `try:` at :118 and `finally:` at :209 with NO `except`** —
   verified: the only `except` clauses in the file are at :93, :244, :271, :321, :359, none
   inside `run`'s try. `PlanningError` propagates out.
8. Test errors on uncaught `PlanningError`, never reaching `assert result.ok`

### Fix belongs in the TEST STUB, not production

Production behaviour is correct and deliberate: `plan_parsing.py`'s docstring states a single
hardened implementation exists so "a divergence would mean replans silently accept plans the
planner would reject." Making the planner tolerate prose would degrade every real plan to
`Plan(goal="", steps=(), confidence=0.5)` (defaults at `plan_parsing.py:51-59`) — the
orchestrator would run a **zero-step plan and report success**. That converts a loud failure
into a silent one product-wide.

**Critical: the fix must be SCHEMA-AWARE.** One `OllamaProvider.complete` mock feeds *three*
distinct JSON contracts with three different schemas — `Router._CLASSIFY_SYSTEM`
(`router.py:22-26`), `Planner._PLAN_SYSTEM` (`planner.py:23-31`), and the ReAct loop in
`reasoning.py`. Returning valid plan JSON alone will fail one step later at the first
`reasoning.run` model call (`orchestrator.py:182-191`). The stub must dispatch on the incoming
`system` message and return the matching shape.

## Part D — Quality gates

**CI (`.github/workflows/ci.yml`, single job, in order):**

| # | Step | Threshold |
|---|---|---|
| 1 | `ruff check .` | 0 findings — **CI omits `ruff format --check`** that the Justfile runs |
| 2 | `mypy` | `strict`, `packages = ["atlas"]` — `atlas_cli` and `tests` **not** type-checked |
| 3 | `lint-imports` | 3 contracts (one of which is vacuous — see intelligence findings) |
| 4 | `pytest --cov=atlas` | `fail_under = 63`, `branch = true` |
| 5 | safety coverage | `--fail-under=70` |
| 6 | orchestration coverage | `--fail-under=83` |
| 7 | `scripts/eval_gate.py` | 0 failures AND 0 regressions |

Step 4 has no marker exclusion and `testpaths = ["tests"]`, so `tests/e2e/test_first_light.py`
**is collected in CI** — the two failures block the pipeline at step 4, before coverage or the
eval gate is ever reached.

**Not in CI:** `atlas doctor --verify-manifest` (Justfile-only), `benchmarks/run.py`,
`ruff format --check`. `.pre-commit-config.yaml` is a **third, different** gate set.

### Eval gate

`scripts/eval_gate.py` — `judge=None`, so deterministic-only and fully offline. Exit 2 on
unknown answer id; exit 1 on any regression or any failure; exit 0 otherwise. No success-rate
threshold (`report.success_rate` is printed but never compared).

**The regression arm is structurally dead in CI:** `ci.yml:30` passes
`--db /tmp/eval-gate.db`, a fresh ephemeral file every run, so `latest_run_passing` always
returns `None`. Only the absolute-failure arm can fire.

`eval/golden_tasks/core.yaml` — 10 tasks, **zero** set `use_llm_judge`.
`eval/recorded/answers.json` — 10 keys, 2,238 bytes, hand-written strings. **The gate scores a
fixture file against a fixture file.** A task with an empty `MatchSpec` passes vacuously
(`evaluators.py:92`, `all(()) is True`).

## Part E — Performance: p99 measured nowhere

`tests/performance/test_benchmarks.py` (100 lines, 5 tests, in CI) reports **no percentiles** —
only a mean, `_elapsed()` :20-24, all with the same `< 0.005` s threshold.

`benchmarks/run.py` (103 lines, **not in CI**) computes **p50 and p95 only**, `N=200`,
`_percentiles()` :26-39. Three stages: `plan_parsing_20_steps`,
`context_compaction_60_turns`, `tool_routing_10_tools` — all **pure-CPU**. I/O stages
(retrieval, providers, DB) are deferred to a "gated live suite" that does not exist.

`grep -rn "p99|percentile|quantile"` returns **only** the 4 lines in `benchmarks/run.py`.
**Phase 39 requires p50/p95/p99 on 8 stages — this is nearly all new work.**
