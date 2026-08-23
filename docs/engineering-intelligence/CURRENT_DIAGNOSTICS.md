# CURRENT_DIAGNOSTICS — the existing self-inspection surface

**Method.** `src/atlas/diagnostics/doctor.py` (172 lines) read in full this pass;
`src/atlas_cli/main.py` command surface enumerated; `Justfile` (72 lines) read in full.

---

## 1. `atlas doctor` — ✅ working, and the right pattern to extend

`src/atlas/diagnostics/doctor.py`. The module docstring states the two design rules, and both
are worth inheriting verbatim for `atlas self-check` (§112):

> "WHY each check is independent and returns pass/warn/fail: a diagnostic that stops at the
> first failure hides the other three problems you also need to fix.
> WHY fail-closed: a check that raises is reported as a FAIL, never skipped."

Surface:

```python
Status = Literal["pass", "warn", "fail"]

@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str

async def run_doctor(atlas: Atlas, *, verify_manifest_only: bool = False) -> list[CheckResult]
def exit_code(results: list[CheckResult]) -> int      # 1 if any status == "fail"
```

Checks currently run, in order:

| Check | Fails when |
|---|---|
| `manifest.matchers` | a safety matcher named in the manifest is unimplemented |
| `manifest.constraints` | the manifest names an unknown constraint |
| `manifest` (pass path) | reports version + rule count |
| `manifest.orphans` | **warn** — rules exist for tools not yet built |
| `sandbox.runtime` | Docker unreachable — **warn** in `dev`, **fail** otherwise |
| `config` | reports `env=` |
| `directories` | **warn** if `data_dir` is absent |
| `secrets.ntfy` | **warn** if no push topic — presence only, never the value |
| `permissions` | hard blocks **or** confirmation rules missing |
| `environment.python` | `< 3.13` |
| `models.ollama` | gateway health says unreachable |
| `database` | `db.health()` false |
| `audit.chain` | `audit.verify_chain()` false — reports the record it broke at |
| `identity.encryption` | vault verification failed — reports only the exception **type** |
| `identity.credentials` | count of stored credentials (never values) |
| `future.providers` | informational |

Three properties to preserve when extending:

1. **`warn` is a real state, distinct from `fail`.** `exit_code` only trips on `fail`. Docker
   missing in dev is a warning, not a broken system. Any new engineering check must pick
   deliberately: an incident that *should* block a release is `fail`; a degraded-but-working
   subsystem is `warn`.
2. **Never print secret values.** `secrets.ntfy` reports "configured"/"absent";
   `identity.credentials` reports a count; `_verify_encrypted_store` returns
   `type(exc).__name__`. This is the pattern for §75 and for any incident detail line.
3. **`verify_manifest_only=True`** short-circuits after the manifest checks. That is what
   `atlas doctor --verify-manifest` uses inside `just check`, so it stays fast and needs no
   Docker or Ollama.

**Note on layering:** `doctor.py` imports `atlas.app`, which is an explicit
`ignore_imports` entry in `importlinter.ini` (`atlas.diagnostics.doctor -> atlas.app`). A new
diagnostics-adjacent module should **not** assume the same exemption exists for it.

---

## 2. CLI surface today

`[project.scripts] atlas = "atlas_cli.main:app"` — `src/atlas_cli/main.py` is the shipped CLI
(typer + rich). Commands present:

`run`, `task`, `shell`, `events`, `doctor`, `profile`, `smoke-test`, and the sub-apps
`providers {list, free, health, verify, sync-openrouter}`,
`automations {list, create, toggle}`, `cost {show, enforce}`,
`memory {consolidate, promote}`, `models {list, doctor}`.

**Absent, and named by the spec:** `incidents`, `incident show|diagnose|repair|verify|approve|rollback`,
`self-check`, `system-health`, `explain-issue`, `major-issues` (§112), and `verify` /
`learn doctor` (§125). All must be created.

There is a **second, older CLI surface** at `src/atlas/interfaces/cli.py` (1205 lines) which is
not the `[project.scripts]` entry point. New commands belong in `src/atlas_cli/main.py`,
following the existing typer sub-app pattern — adding them to `interfaces/cli.py` would ship
nothing.

**Gate consequence (`docs/final/TECHNICAL_DEBT_FINAL.md` #29):** `[tool.mypy] packages =
["atlas"]` excludes `src/atlas_cli`, and `[tool.coverage.run] source = ["atlas"]` excludes it
too. So the package that *is* the `atlas` entry point is currently neither type-checked nor
measured. New CLI code lands in an unchecked tree — which makes it more important, not less,
that the command bodies stay thin and delegate to `atlas.engineering` (which **is** checked).

---

## 3. Gate targets

`Justfile`:

| Target | Runs |
|---|---|
| `just lint` | `ruff check .` **and** `ruff format --check .` |
| `just check` | lint + typecheck + `lint-imports` + `pytest` + `atlas doctor --verify-manifest` |
| `just cov` | coverage run + report |
| `just web-lint` / `web-test` / `web-build` / `e2e` | frontend gates |
| `just check-all` | `check` + `web-lint` + `web-test` + `web-build` + `e2e` |

**`just check` is stricter than CI**, which runs only `ruff check .`. **94 files fail
`ruff format --check`** on a pre-existing basis, so `just check` cannot go green until the two
agree. Plan accordingly: judge the new work by `ruff check .`, `mypy`, `lint-imports` and
`pytest` individually, not by `just check`'s exit code.

Pytest config: `asyncio_mode = "auto"`, `testpaths = ["tests"]`, `addopts = "-q"`.
Coverage: `source = ["atlas"]`, `branch = true`, `fail_under = 63`, plus two CI-enforced
slices — `*/atlas/safety/*` at 70 and `*/atlas/orchestration/*` at 83.

**Any new package under `src/atlas/` is immediately inside the coverage `source`**, so
shipping a large, thinly-tested `atlas.engineering` will pull the measured total *down* and
can fail `fail_under = 63` even though nothing regressed. Tests must land with the code, not
after it.

---

## 4. Health endpoints

`/api/v1/live`, `/api/v1/ready`, `/api/v1/health` are served by `health_router` and are the
only routers **without** `auth_required`, because the container `HEALTHCHECK` needs them open.
They are backed by `RuntimeSupervisor.get_health_report()` — see `CURRENT_OBSERVABILITY.md` §8.

`atlas system-health` (§112) should render that same `HealthReport` rather than re-deriving
health, so the CLI and the probes can never disagree.

---

## 5. What is missing

| §  | Requirement | State |
|---|---|---|
| 112 | `atlas self-check` | ❌ `run_doctor` is the pattern; the incident-aware checks do not exist |
| 112 | `atlas system-health` | ❌ `HealthReport` exists and is unrendered by any CLI command |
| 112 | `atlas incidents` / `incident <verb> <id>` | ❌ no store to read |
| 112 | `atlas explain-issue`, `atlas major-issues` | ❌ |
| 125 | `atlas verify`, `atlas learn doctor` | ❌ named by the required-gates list, do not exist |
| 65/119 | Major Issue Summary | ❌ |
| 66 | Daily engineering report from actual telemetry | ❌ — but `CronScheduler.register_job(name=..., cron=..., fn=...)` is the established pattern (`memory_consolidation` at `0 2 * * *`) |
| 91 | Self-diagnostic chat | ❌ |
| 113/114 | Final dashboard, `/engineering` centre | ❌ no route |

**One caution for §66.** A daily report generated from an empty incident store will render
"0 incidents, all healthy" — which reads as a measurement but is really an absence of
instrumentation. The report must distinguish *no incidents detected* from *no detectors
running*, or it becomes exactly the kind of reassuring-but-false surface this project's truth
mandate exists to prevent.
