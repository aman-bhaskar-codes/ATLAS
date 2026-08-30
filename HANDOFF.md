# HANDOFF — one-week pause (2026-08-30)

A clean stopping point so work can resume after a week away without re-deriving state.
Everything here was verified against the live tree, not memory.

## Where the project stands

- **Branch/commit:** `main` @ `672e33e`. Working tree is clean.
- **All quality gates green:**
  - `ruff check .` + `ruff format --check .` — clean (749 files)
  - `mypy` — 0 errors across 506 source files
  - `lint-imports --config importlinter.ini` — **3 contracts kept, 0 broken**
  - `pytest -q` — **1,687 collected, 0 failed / 0 error**; the two browser
    integration tests (`test_playwright`, `test_provider_swap`) now **skip** cleanly
    when no launchable browser is present instead of failing.
- **README.md** is up to date as of 2026-08-30 (counts, routers, migrations, the new
  ADE/IDE section, and the corrected "environment-gated tests skip" note).

## What landed this cycle

1. **Git diff (read-only) ADE slice** — `parse_numstat` + `GitEngine.diff` (numstat +
   raw patch, `--staged` threaded through), surfaced via `IDEService.git_diff`,
   `GET /api/v1/ide/workspaces/{id}/git/diff`, and `atlas ide diff [--staged] [--patch]`.
   A non-repo returns `None`→`is_git_repo=false`; a clean tree is an honest empty diff.
2. **Two "must not break" fixes that made the governed-command path actually work** end
   to end against the real `SafetyEngine` + `ShellTool` (previously green only via fakes):
   - `tools/shell.py` — token-prefix allowlist match (`_matches_prefix`), so multi-word
     entries like `git status` match their subcommands while `git push` / unknown
     executables stay deny-by-default.
   - `config/permissions.yaml` — added `{tool: shell, operation: run, tier: 1,
     constraint: cmd_in_read_only}` so the IDE's one-shot `run` path is classified
     instead of hitting deny-by-default.
3. **Environment-gated browser tests** — `tests/capabilities/browser/providers/conftest.py`
   adds a `browser_available` probe + `require_browser` fixture; the two integration
   tests skip (not fail) where no browser can launch. They still run in full on a machine
   with Chromium installed.
4. **New tests locking all of the above** — git_diff service/route/CLI tests, git-engine
   numstat/diff tests, and a `TestMultiWordAllowlist` regression for the shell fix.

## Resume checklist (when you come back)

1. `cd atlas && git status` — confirm still clean on `main`.
2. Re-run the gate sequence to confirm nothing rotted:
   ```bash
   ./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
   ./.venv/bin/mypy
   ./.venv/bin/lint-imports --config importlinter.ini
   ./.venv/bin/python -m pytest -q
   ```
3. Pick up the **next ADE slice** (see Roadmap #1 in README):
   - git **stage/commit** verbs — `side_effect` tier, funnel-gated `require_confirm`.
   - interactive **PTY terminal** — WS streaming (Phase 6).
   - then dev-process/server management, LSP/diagnostics, DAP debug, the coding-agent
     build-loop, and finally the VS Code-class frontend over all backend features.

## Live-validation debt (needs network + real keys — do on a connected machine)

These cannot be exercised in the sandbox and remain unverified against real vendors:

- **The five OpenRouter `:free` slugs are best-guess.** If a model 404s, fix the slug in
  `config/models.yaml` — no code change needed.
- **Voice has never hit a real vendor endpoint.** Providers are unit-tested with mocked
  HTTP/WebSocket transports only. `uv sync --extra voice`, set keys, flip
  `voice.enabled: true`, then smoke-test `atlas voice speak` / `atlas voice chat`.
- **Embeddings migration:** if switching from prior bge-m3 vectors, wipe once:
  `rm -rf ./.atlas/chroma` (nothing auto-deletes it).

## Ground rules that still apply

- One `.env`, at `atlas/.env` (CWD-relative — always run from `atlas/`); one
  `OPENROUTER_API_KEY` covers chat, embeddings, and speech. No Ollama, no second vendor key.
- Never bypass the `SafetyEngine` funnel; fail closed; new behavior behind a flag defaults off.
- No commits unless explicitly asked.
