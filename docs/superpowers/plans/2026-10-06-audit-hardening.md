# ATLAS audit hardening implementation plan

**Goal:** Resolve the project audit findings and strengthen the integration of execution, tools, persistence, backend, and frontend.

**Authority:** The user authorized implementation of the audit on 2026-10-06. Proceed through the fixes without repeating approval gates. Preserve the local-first architecture and use the existing safety funnel for consequential actions.

**Workspace:** `codex/audit-hardening` in the existing `atlas` repository. Native worktree creation failed because the app's project directory is the parent of the Git repository. The repository was clean before the branch was created.

## Implementation sequence

- [ ] 1. Shell safety: share token-aware command classification between the classifier and tool; require approval for interpreters and network utilities; recognize destructive find arguments; block shell credential access and unsafe command parsing. Preserve governed IDE verbs.
- [ ] 2. Transport authentication: authenticate every sensitive HTTP endpoint, all WebSockets, and browser origins; retain anonymous localhost operation and read-only key restrictions. Integrate frontend authentication and streaming rather than putting secrets in URL parameters.
- [ ] 3. Execution limits: reserve DAG work before dispatch, preserve successful observations when a sibling fails, reject invalid dependencies, enforce provider deadlines, retain partial agent tool history.
- [ ] 4. Process lifecycle: kill and reap timed-out/cancelled native children, prevent credential environment inheritance, drain background work and recover interrupted agent/research runs with explicit terminal state.
- [ ] 5. Network safety: validate canonical addresses and resolved destinations, enforce browser request/redirect egress, stream bounded research downloads, enforce upload size/quota/retention, report scanner uncertainty accurately.
- [ ] 6. Provider/deployment correctness: reconcile configured prices/capabilities against official model data; remove paid routes from the zero-cost fleet; ensure Docker's profile can infer; validate actual build/start behavior where available.
- [ ] 7. Automation reliability: use the configured runtime or internal dispatch, preserve identity/authorization, surface failures to durable delivery, and derive stable event-based idempotency keys.
- [ ] 8. Approval workflow: persist confirmation requests and decisions, connect backend confirmer and dashboard, validate decision ownership/role, resume the original action exactly once.
- [ ] 9. Composer integration: visible submission errors, pending keyboard guard, stable submission idempotency, actual uploads and task attachment consumption; support attachment-only tasks.
- [ ] 10. Frontend truth and reliability: functional audit filters/export/pagination, runtime-backed capability indicators, clean SSE/WebSocket reconnection/cancellation, status-aware retry.
- [ ] 11. Evaluation/quality: evaluate current runtime behavior in CI with independent deterministic fixtures and meaningful baselines; fix SQLite lifecycle warnings, formatting, and frontend lint; run coverage, backend gates, frontend build/unit/browser integration and deployment checks.

## Verification requirements

For each defect: reproduce it with a behavior-focused failing regression test before modifying production behavior, then run the focused suite. Review cross-component effects before marking it resolved. Run complete backend tests with coverage, strict typing, lint/format/import contracts, frontend tests/typecheck/lint/build, and browser integration before completion. External services are used only when they supply evidence or functionality required by this repository; no unrelated hosted replacement or external publication is implied.

## Progress and evidence

- Baseline audit: 2,256 backend tests collected, five skipped, no failures; an unhandled SQLite thread warning remains. Frontend: 67 unit tests passed; strict typing passed; four lint warnings. Python formatting needs 12 existing files formatted.
- All implementation items remain open until current tests and review support completion. This file is the continuation ledger for the full scope, not a claim that the goal is complete.

## Pause checkpoint — 2026-10-06

The user explicitly requested stopping and saving for later. All changes are saved on disk on `codex/audit-hardening`, **uncommitted and unpushed**. Do not restart completed implementation waves or claim whole-project verification. Agents were interrupted; no further implementation is authorized until the user resumes.

### Implemented, with focused evidence

- Shell command parsing now shares token-prefix classification, requires confirmation for interpreters/network commands and mutating find/git flags, detects credential arguments, resolves cwd-relative symlinks and container mount aliases, and preserves positional filenames containing `=`. The new shell regression suite last passed 30 cases. Shell/IDE parity suites also passed earlier.
- Backend HTTP and WebSocket identity/origin gates implemented; observational streams allow readonly keys, voice/webhook mutations require admin. Transport regression run passed 116 tests; shared CORS is wired to the same origin constant.
- DAG budgets reserve work before dispatch, invalid dependencies fail before execution, completed observations survive sibling limits, and partial native agent tool turns retain their completed records. Orchestration suite passed 347 tests at that point.
- Native processes use bounded output and an environment allowlist; timeout/cancellation cleanup targets POSIX process groups. Inference/streaming deadlines implemented. Worker reported 269 focused native/runtime/IDE tests passing. Cleanup review findings below still need repair.
- Paid GLM 5.2 and MiniMax M3 have honest IDs/prices; five free routes remain, including Dots and OpenRouter's free router. Default/Docker profile selects free-capable configuration. Zero-cost profiles send an OpenRouter price ceiling; completion accounting consumes actual returned cost. Model/cost/free-policy focused run passed 46 tests. No live paid generation was invoked.
- Automations dispatch through the task control plane rather than a fixed HTTP port; the durable bus supplies stable event IDs, retries reuse deterministic task keys, failures propagate. Focused autonomy tests passed.
- Composer performs real text/PDF uploads, preserves drafts/errors and retry identity, guards repeated submissions, resolves document contents before execution, and enforces opaque IDs, quotas and retention. Images/scanned/encrypted PDFs are explicitly unsupported. Worker reported 25 backend and 5 coordinator tests passing. Added body middleware limits actual upload bytes before multipart spooling; its focused tests passed.
- Reviewer-found attachment retry issue fixed: already-created tasks are returned before expiring attachment resolution. Backend creation is now atomic on the idempotency key. Focused attachments/atomic/tasks-contract command passed after both regressions were observed failing.
- Frontend session-only API credentials, authenticated fetch SSE, replay cursors, socket lifecycle guards and bounded handshake retries are connected to Settings and all stream consumers. Worker reported 55 focused unit tests, typecheck/lint, and two desktop/mobile credential browser flows passing.
- Audit UI/backend now have actual date/tier filters, cursor pagination, bounded redacted JSON/text exports, and runtime-derived context indicators. Worker reported focused backend/unit/type/lint checks passing; full-stack audit/composer browser tests are authored but not run centrally. `frontend/DESIGN.md` preserves the existing design system.
- Browser/research egress has resolver preflight, bounded fetches, scanner uncertainty handling and native Chromium request interception preserving redirect URL/origin. Worker reported 109 focused tests and two Chromium integration tests passing; final worker verification was interrupted. This is not complete network isolation.
- Latest completed whole-source mypy check passed 614 files before later edits. Latest `git diff --check` passed at pause. **No final full backend suite/coverage, frontend build/full E2E, deployment or broad review has been completed for the combined diff.**

### Immediate resume work

1. Read current Git diff and this checkpoint; confirm no unrelated user edits. Reconcile interrupted worker outputs with actual files, rather than re-running implementation from scratch.
2. Fix confirmed native cleanup regression: `_kill_quietly` drains stdout/stderr without its own deadline; a detached descendant holding a pipe open can hang timeout/cancellation/aclose cleanup. Add a controlled regression and bounded cleanup.
3. Fix confirmed multi-mount mismatch: credential checking remaps longest target first, while `NativeSandbox._remap_path` still uses insertion order. Share the mapping algorithm and test overlapping mounts; checked and executed paths must match.
4. Finish skeptical code review, including price ceilings across profiles, attachment retry/ownership/path behavior, async shutdown and automation delivery. Review worker was interrupted before its final report.
5. Finish egress verification and explicitly address DNS preflight TOCTOU/connection pinning. Browser WS is currently blocked, service workers disabled, and WebRTC/background traffic is outside Fetch interception. Do not treat these limitations as the desired final capability; proper network sandbox/proxy support remains work.
6. Run combined lint, formatting, strict types, import contracts, backend suite/coverage, frontend unit/lint/typecheck/build, and real-backend E2E. Browser/local server binding needed escalation in the sandbox. Use a zero-external test backend (`OPENROUTER_API_KEY=''`, `ATLAS_API_KEYS=''`, `ATLAS_PROFILE=local_free`) and isolated test data. Do not kill unrelated services or reuse user runtime data.
7. Full-goal outstanding items include durable agent/research crash recovery and incremental checkpoints, persistent approvals and exactly-once decision/resume wiring, actual image/multimodal attachment support, honest current-runtime evaluation/baselines, SQLite worker lifecycle warning, remaining formatting/lint and deployment/live-provider validation. Existing stages are not globally complete until these and integration evidence are resolved.

### Worker/state notes

- Completed implementation workers: transport, DAG/agent accounting, native deadlines, composer, frontend authentication.
- Interrupted at pause: `audit_ui_fix`, `resume_auth_ui` (backend review follow-up), `resume_egress` (final checks/edge tests). All three were confirmed interrupted.
- Known root command sessions had completed; no shared build or full-stack E2E servers were started by root. Composer worker reported stopping its failed sandbox backend/build attempt. Authentication browser tests used their dedicated local fixture server; inspect any surviving command handles before restarting later.
- Preserve all added/untracked source, tests, E2E files and design documentation. Do not stash/reset/delete them. Save does not mean verified completion or release.

## Resumed and expanded scope — 2026-10-08

The user resumed work and authorized completing the fixes plus platform separation, Gemini/local inference, research OCR/RAG, IDE fine-tuning, and robust OS/tool/MCP routing. The earlier pause is revoked. Preserve the audit requirements above; these additions do not replace them.

- Command centre launches separate Research and IDE platforms. Research has distinct question/history/corpus/status pages; IDE has a workspace launcher and full workbench. Models, tools/MCP, memory, approvals and operations stay on focused component pages, not a single crowded page. Preserve shared authentication, design tokens and backend execution funnel.
- Configure and verify Gemini multi-turn tool calling with thought-signature and call-ID preservation, plus installed local Qwen3 4B. Use installed BGE-M3 local embeddings; preserve privacy/cost restrictions and report provider availability honestly. Hardware inspected: M1 MacBookAir10,1, 8 GiB RAM. No model downloads are needed.
- Research uses shared hybrid RAG, reranking, bounded document extraction and real local OCR with page/source provenance. Installed runtimes: Tesseract and pdftoppm. Do not fabricate extracted text or create a second disconnected retrieval pipeline.
- IDE fine-tuning means a real dataset/training/evaluation workflow, not merely relabeling prompt configuration. A question about actual training scope/dataset/compute is pending; independent integration work continues. No paid training job or unapproved dataset export is authorized by a generic provider-readiness requirement.
- Robust main-agent scheduling, process cleanup, durable recovery, tool lifecycle/selection and MCP must be implemented through existing OS/runtime/safety boundaries. No ungoverned alternative execution path.
- Run final complete unit/integration/type/lint/coverage/build gates and real full-stack E2E after implementation, including separate platform navigation, local/Gemini provider contracts, tools/MCP, OCR-to-retrieval-to-citations, approvals and recovery. Missing external credentials/services are explicit unverified requirements, not a successful end-to-end test.

Current parallel ownership: execution_fix (provider/model readiness), frontend_audit (platform separation), egress_fix (research ingestion/RAG/OCR). Root handles known cleanup/remapping defects, orchestration metadata/lifecycle/approvals, component API wiring and final verification.

## Latest pause checkpoint — 2026-10-08

User explicitly requested stopping again and resuming **only when they say “continue.”** Do not resume on an unrelated message, a delayed worker result, or an answer to the optional fine-tuning question. Changes are saved on disk on `codex/audit-hardening`, uncommitted and unpushed. All three running workers were interrupted: `execution_fix`, `egress_fix`, `resume_auth_ui`.

### Additional work since the October 6 checkpoint

- Fixed both native review findings with failing-then-passing regressions: cleanup has an independent deadline and closes subprocess pipes even when detached descendants retain them; native execution and credential checking share longest-target mount remapping. Native/shell/atomic focused command passed 81 tests; scoped Ruff passed. Fixed the async busy-wait lint finding in the atomic task test.
- Gemini thought signatures now survive engine execution, JSON persistence and conversation rehydration through `ToolCallRecord`; call IDs stay intact. Engine/persistence focused run passed 19 tests.
- Provider health endpoint now probes adapters with a bounded timeout, uses actual latency snapshots, and does not return vendor exception details. Free-provider filtering derives model cost classes, not remaining quota. Two new endpoint regressions failed before changes and passed afterward.
- Separate platform UI implementation completed by `frontend_audit`: Research has Overview/History/Corpus/Status/session routes, IDE launcher is `/workspaces` with `/ide` and `/dev` redirects and a separate workbench header, command centre launches platform tabs with popup fallback. `/mcp` has validated status/discovery and existing lifecycle controls. Worker reported 121 frontend unit tests, TypeScript and diff checks passing; one existing EventFeed lint warning remains. Four new platform/MCP E2E tests are queued for the final shared run. No build or full-stack browser server was started by root.
- Provider worker implemented restored Ollama registration, `local-qwen3` and `gemini-flash` catalog entries, configurable Qwen3 4B/8K context and Gemini model/billing tier, local BGE-M3 embedding defaults, Gemini header authentication/health/thought signatures/function IDs, Ollama tool-history encoding and loopback/proxy restrictions, and typed validated `ImageInput`/Message/ModelRequest images. Last worker report: 20 new focused cases green; broader intelligence/bootstrap/config run 125 passed. Worker was interrupted during final documentation/lint/type verification; inspect actual diff before calling the whole provider stage verified.
- Research worker added bounded document extraction/OCR modules and provenance-aware chunks. Last report: 38 focused tests passing, including real OCR of a synthetic image and scanned PDF and retrieval evidence with page provenance. Worker was still wiring authorization/read allowlists, API ingestion into the shared fabric, and FeatureReranker into ResearchRunner when interrupted. Those final edits may be partial/unverified.
- `resume_auth_ui` was implementing native gateway strict policy merging, cache privacy/eligibility separation and image forwarding. It was interrupted without a completion report. Inspect its gateway/cache changes and tests before proceeding.

### Critical continuation details

1. Preserve every tracked and untracked source/test/design/E2E artifact. Latest read-only `git diff --check` passed. No combined final build, coverage, complete backend suite or full-stack E2E has been run for this expanded diff.
2. Existing Qwen cloud embeddings and BGE-M3 both have 1024 dimensions but **different vector spaces**. Default-switching embeddings must use model-scoped collections or a safe explicit reindex migration; do not reuse old vectors as if compatible, reset user data, or claim RAG correctness from dimensional equality alone.
3. Installed local resources: Qwen3 4B and BGE-M3 via Ollama; Tesseract and pdftoppm for OCR; 8 GiB M1 MacBook Air. Avoid concurrent live model/OCR/build loads that overrun RAM. No model download or paid generation/training was performed.
4. The optional fine-tuning question remains unanswered: dataset/training/evaluation pipeline first versus actual local weight training versus paid Google Cloud tuning. Fine-tuning implementation and its IDE UI remain outstanding. Do not silently reinterpret weight training as prompt tuning, or launch paid jobs without the necessary dataset/budget scope.
5. Root still needs to harden MCP inspection: resources/prompts GET handlers currently call `ensure_ready`, which can start a configured server. Read-only inspection must not implicitly perform an owner-only connection action. UI only queries them after connection, but the backend must enforce this boundary itself.
6. Still outstanding: model readiness/config workflow polish and actual local/Gemini integration verification; safe embedding migration; complete OCR/API/RAG wiring and authorization; native gateway/cache privacy verification; durable agent/research recovery/checkpoints; persistent approvals/decision/resume; image attachment integration; IDE fine-tuning workflow; runtime evaluation baselines; SQLite shutdown warning; complete browser egress isolation; remaining lint/format/design findings; final independent review and full end-to-end verification.
7. Coordinate worker ownership on resume rather than redoing finished work. Provider contracts are `ImageInput(mime_type, data)`, `Message.images`, `ModelRequest.images`, and `ProviderToolCall.thought_signature`; the root orchestration signature propagation is already implemented. The gateway worker may have added further typed per-request privacy/cost/network fields—inspect them as authoritative.

## Newest pause checkpoint — 2026-10-08, after second resume

The user again explicitly requested stopping and resuming **only on “continue.”** All edits remain saved on disk on `codex/audit-hardening`, uncommitted and unpushed. `execution_fix` (IDE training workflow) was confirmed interrupted. Other resumed implementation workers completed. The known root approval/auth test process was checked at stop: it had finished successfully. Latest `git diff --check` passed. Do not continue implementation or launch further verification until the user resumes.

### Work completed during this resume

- Provider stage finished with 160 focused tests passing, 26 new regression cases observed RED→GREEN, and owned Ruff/format/mypy checks clean. Safe embedding identity namespaces now cover cache, memory, episode and knowledge vectors; legacy collections/data remain intact. Documentation is in `docs/providers/gemini-local.md`.
- OCR/RAG stage finished: bounded real local image/scanned-PDF OCR, source/path/upload authorization and limits, shared fabric ingestion/retrieval/reranking, page provenance through tool payloads and persisted research citations, and non-destructive bounded embedding reindexing. Worker reported 269 focused tests plus 41 touched regressions after its final preservation fix, and owned lint/types clean. Documentation is in `docs/knowledge/local-ocr.md`.
- Gateway/cache privacy integration finished: strict native/legacy profile policy meet, selection before cache, eligibility-checked cached model/provider identity, policy/context/history cache namespaces, tools/images bypass, VISION forwarding, correct cloud/local provenance, and conservative cloud embedding avoidance for private/local/free-only policies. Worker reported 144 intelligence tests passing and owned formatting/lint/types clean.
- MCP inspection no longer connects disconnected servers on GET; resources/prompts require an already-ready connection. Lifecycle POST operations explicitly require admin. Raw connection/discovery exception details are no longer returned. New regression tests failed first and then passed with the runtime suite.
- Agent/research services now have durable running-record queries without the previous 50-item limit, interrupted-run settlement, cancellation/shutdown cleanup and startup recovery wiring. Interrupted agent continuations receive an explicit inspection warning; no unknown side effects are automatically replayed. These changes do **not** yet supply full incremental/write-ahead checkpoints or a multi-process ownership lease.
- Root orchestration/provider/MCP focused command passed **355 tests**, and all three import contracts passed. Subsequent research/agent service focused tests passed after new RED→GREEN recovery cases.
- Persistent approval queue and UI were implemented: atomic/idempotent admin decisions, sanitized concrete previews, actual pending counts, expiry/cancellation/startup recovery, tier-3 challenge validation, BLOCK refusal, unified legacy/trust payloads, and shared home/Approvals review UI. Worker reported 22 focused backend tests, 127 frontend unit tests, TypeScript and scoped lint/types passing. Fabricated resolved approval rows were removed; dangerous CLI confirmation now requires the actual code.
- Root installed the approval queue in the HTTP composition before runtime startup; queue startup occurs after database provisioning and before schedulers/tasks are accepted, and shutdown denies/drains approvals before services/providers/DB close. Known settings/API-key values are supplied only to runtime redaction, never printed. The last composed approval/auth test command passed at stop.
- Narrow legacy model-setting aliases migrate `glm-5.2-free`→`glm-5.2` and `minimax-m3-free`→`minimax-m3`; the catalog still identifies these as paid and free-policy filters remain authoritative. Unknown custom model IDs still fail clearly. Existing real `.env` files were not edited. API test fixtures explicitly select their mocked provider/embedder to avoid accidental local model execution. Legacy/env-auth/approval focused command passed; `.env` API-key enforcement was already correct and gained regression coverage.
- `uv lock` completed with approved metadata/cache access and added the OCR extra dependencies (Pillow 12.3.0 and pypdf 6.19.0). It warned about existing yanked charset-normalizer 3.4.8 and grpcio 1.82.0 pins; those warnings still need assessment. No model downloads, paid inference, cloud training, user-data resets or external publication occurred.

### Interrupted IDE training work

Partial files now exist under `src/atlas/capabilities/ide/training/`: service, dataset validation/grouped splitting, evaluation gates, artifact manifests, CLI and trusted local runner. Worker reported 13 synthetic dataset/evaluation cases RED→GREEN. Training service constructor is `TrainingService(ide, agent_runs=None, native_runtime=False)`, with `recover_interrupted()` and `close()` lifecycle contracts planned. Readiness must require actual MLX availability, Mac ARM native execution and a compatible local 4-bit training checkpoint; installed Ollama GGUF is not that checkpoint.

Runner plan uses the real allowlisted `python -I <trusted runner>` through governed IDE execution, offline HF behavior, bounded memory/batch/sequence/layers and no model download. No weights were trained. API/router, dedicated IDE training UI, app composition, runtime/resource verification and broader tests were still in progress when interrupted. Inspect actual files; do not assume all planned methods or interfaces are finished.

### Next resume priorities

1. Reconcile actual diffs and interrupted training worker outputs; preserve all tracked/untracked work.
2. Finish training API/UI/composition and honest dataset/trainer/base-artifact readiness. Earlier fine-tuning dataset/compute question remains unanswered; do not launch paid jobs, export unapproved user data or claim trained weights.
3. Review and strengthen recovery: incremental/write-ahead tool outcomes, safe live-run ownership, admission during shutdown and bounded cleanup. Current settlement fixes indefinite “running” but cannot infer effects lost in a process crash.
4. Finish component integration gaps: image attachment consumption; native/other-provider image encoding; provider/config readiness views; bounded reindex lifecycle/status; training artifact privacy/ignore rules; current-runtime evaluation baselines and SQLite worker warning. The identified PDF test starts a database without a `finally: stop()` and remains to be repaired.
5. Review approval composition and cancellation end to end, including preserving intended notification behavior. Full-stack browser approval tests have not run centrally.
6. Address remaining browser egress isolation (DNS pinning/TOCTOU, WS/WebRTC/background traffic) without claiming resolver preflight is a network sandbox.
7. Run complete combined backend tests/coverage/lint/format/types/import boundaries, frontend unit/lint/types/build, local-model smoke, and final real-platform E2E (Research/IDE/MCP/composer/audit/credentials/approvals/OCR-to-citations/recovery/training readiness). No final combined build, full backend coverage run, broad independent review or full-stack E2E has yet been completed.

Do not mark the overall objective achieved based on the focused suites above. The scope remains the full original audit plus the user’s platform/provider/RAG/OCR/tooling/IDE requirements.

## Newest pause checkpoint — 2026-10-11

User explicitly requested stopping until “continue”. All three active workers (`deadline_fix`, `egress_fix`, `execution_fix`) were interrupted. All changes remain local, uncommitted and unpushed. No root test/build process remains running; do not restart checks while paused.

### Saved progress and evidence

- Training app composition now constructs TrainingService, exposes sandbox for readiness, recovers and closes training with other runtime services, and registers its authenticated API router. Training worker implemented dedicated `/ide/training` UI, contracts, API/CLI and bounded offline runner. No actual weights trained or model downloaded. Worker was last asked to finish verification and strengthen current-runtime evaluation beyond fixture-only checks; inspect partial files next resume.
- Workspace direct file reads and indexing now reject credential files/directories, including case-insensitive names/suffixes, and directory symlinks are skipped. Focused workspace/shell checks passed 43 tests. Training signing key remains protected by the shared path policy; native development execution is still not an OS jail.
- Recovery worker reported 99 focused recovery/approval/API tests plus clean owned Ruff/types. Added write-ahead checkpoints and execution/approval ownership leases. Root asked worker to expose checkpoint counters in agent API and add PostgreSQL lease-row locking variant; worker was interrupted during these additions. `tests/orchestration/test_postgres_run_ownership_sql.py` exists; inspect current diff and rerun before trusting final state.
- Browser worker is mid-implementation of DNS-pinned httpcore transport, preserving origin Host/TLS SNI; new `security/pinned_transport.py` and transport tests exist. Managed-browser proxy/WS/WebRTC isolation remains unfinished. Existing external CDP/tab attachment must fail honestly if isolation cannot be verified. Do not claim browser network isolation complete.
- Root confirmed typed images were dropped by OpenAI-compatible serialization (regression RED), then implemented text-plus-image data URL encoding. Provider tool-loop and architecture tests passed 6 tests. Main composer image upload/consumption remains unfinished.
- Root implemented EventFeed `showTimestamps` behavior and fixed API router import order. Latest frontend check: 131 unit tests passed, TypeScript passed, ESLint passed.
- Root added bounded admin-only `POST /api/v1/knowledge/reindex` calling shared pipeline with limit 1–50 and 30-second deadline. Route regression failed first, then route + non-destructive reindex tests passed 5. UI/status integration remains outstanding.
- Full backend baseline earlier this resume: 2564 passed, 7 skipped, 1 failed (training forbidden upward import), 1 SQLite closed-loop warning; diagnostic coverage 75.35% at `/tmp/atlas-resume-coverage.xml`. Worker removed the forbidden import; a later architecture run passed. This was not a clean final backend run. Root repaired missing PDF test database cleanup, but warning needs fresh verification.
- Latest strict mypy run passed 612 source files. Concurrent broad Ruff found unfinished worker-file formatting/protocol override issues; those were not a final gate. Recheck once workers finish.
- Production Next build failed because sandbox prohibits Turbopack binding its internal port. Error explicitly says `Operation not permitted`; no application build success established. Next resume retry with `require_escalated` (required for sandbox-induced failure). Also set explicit Next workspace root after consulting bundled docs; detected unrelated home lockfile currently widens inferred root.
- `uv run` initially failed cache permissions then dependency DNS; approved escalated retry finished installing local editable project and fixed router imports. Direct `.venv/bin/{pytest,ruff,mypy}` avoids unnecessary dependency resolution for checks.

### Resume order

1. Reconcile interrupted workers and actual diff; preserve all work. Resume network, training, and lease/API verification without duplicate ownership.
2. Complete composer image consumption, corpus reindex UI/readiness/status, current-runtime eval, remaining notification/lifecycle integration and accurate docs.
3. Retry production build with needed sandbox escalation; configure isolated E2E environment to avoid `.env` cloud credentials and unrelated running services. No final full-stack E2E has run.
4. Run fresh combined backend coverage/lint/format/types/import boundaries, frontend checks/build, real local-model smoke, broad independent review and final full-stack E2E. Overall user objective remains incomplete.

## Newest pause checkpoint — 2026-10-17

User explicitly stopped work until “continue”. Interrupted all three workers and the active E2E run. Confirmed test servers and temporary Ollama server are stopped; local Qwen was unloaded first. Changes remain saved, uncommitted/unpushed. No further implementation or checks should run while paused.

### New implementation and verification

- Production frontend build PASSED after sandbox escalation; later E2E builds also passed. Set explicit Turbopack root based on installed Next docs. Frontend 131 unit tests, TypeScript and lint passed before latest small changes; training worker separately passed 44 focused units/full tsc/owned eslint after its latest changes.
- Composer image uploads now perform bounded real local OCR and send extracted text to tasks, clearly excluding visual details. RED→GREEN real image API regression plus composer/body-limit suite passed 16 cases. Invalid images still reject415. Typed OpenAI images remain supported from prior resume.
- Added Research corpus embedding-maintenance UI with pending/error/result feedback, batch10, 35s client timeout vs30s server deadline. Pipeline exposes availability; unavailable embedding configuration returns503. Route/reindex tests passed5. Real browser maintenance flow passed.
- README corrected stale free-model fleet and cloud embedding claims; removed destructive delete-corpus migration advice. Provider doc calls Gemini identifier configured rather than claiming current availability.
- Training worker: actual local-runtime evaluation endpoint `/api/v1/ide/training/evaluations/runtime`, admin + approved, strict pinned registered-local model/secret/offline/zero-cost, bounded holdout replay. Prediction-file scoring cannot promote. Provenance binds signed training artifact to adapter hash and registered ModelSpec.artifact_sha256. Optional gateway/model_registry injected in app composition. Cross-process OS file-lock admission/recovery, bounded cancellation retaining live ownership when cancellation is suppressed. Worker reported59 focused backend tests,12-file mypy, lint; final report interrupted. MLX-LM/safetensors absent and no compatible base checkpoint or tuned candidate; NO weights trained/downloaded/paid calls made.
- Recovery worker finished PG row-lock SQL variants and API checkpoint exposure with32 focused tests/types/lint. Found and fixed live DAG task corruption on second startup: task ownership lease, API admission before row visibility, durable-write/tool-dispatch fencing, deferred recovery. Root replaced unsafe bare startup recovery with orchestrator.recover_interrupted() and added orchestrator.close() before providers. Worker still finishing ownership-loss/post-confirmation guards/tests when interrupted; inspect actual diff, no final gate established.
- Network worker: DNS-pinned httpcore transport preserving Host/SNI; owned authenticated browser proxy supports native redirects/subresources/WebSockets. Unprotected external CDP/injected browsers rejected; shared9222 launch removed. Initial flag-only WebRTC control leaked3STUN packets in controlled fixture; native fresh-profile preferences then passed zero-packet/zero-candidate test. Owned real Chrome HTTP/WS/private-subresource integration passed, browser focused suite earlier145passed/5skipped and owned lint/typesclean. Final rerun interrupted. No OS firewall guarantee; tunnel/WS limits120s/32MiB, scopes intersect context-wide.
- Actual installed Ollama qwen3:4b and bge-m3:latest confirmed. Real ATLAS Ollama adapter returned valid JSON `{ "status": "ready" }` for a bounded local prompt. Model unloaded and temporary service stopped afterward. Gemini not live-tested; no cloud inference performed.
- Broad backend coverage run completed while workers were evolving: only5failures, all new proxy fixture loopback binds denied by sandbox (`PermissionError`). Coverage74.76%, repository63% floor reached; this is NOT final clean verification. `/tmp/atlas-final-tests.log`, `/tmp/atlas-final-coverage.xml`. Retry with required escalation after workers stabilize.
- SQLite warning traced to actual missing cleanup in `tests/memory/test_phase3_integration.py` db fixture. Changed return to yield/finally stop;17 tests passed with unhandled-thread warnings treated as errors. Fresh full suite still required.
- Broad lint/format caught unfinished worker files and some pre-existing formatting; `/tmp/atlas-lint.log`, `/tmp/atlas-format.log`. Do not globally format unrelated files; finish touched scopes and rerun final checks.

### E2E state and precise next steps

- Added `scripts/e2e_server.py`: allowlisted child environment, no operator dotenv/cloud keys, local-only profile with intentionally unavailable model endpoint, unique per-run state under `.e2e-data`, exec replaces parent so shutdown reaches API. Tests seed same unique directory. No destructive test database wipe or existing-server reuse.
- Downloaded test Chromium/ffmpeg/headless shell into `/tmp/atlas-playwright`; also verified installed Chrome with fresh test profile via `PLAYWRIGHT_CHANNEL=chrome`. Video capture disabled; failure screenshots/traces retained.
- Latest real API+production Next+Chrome E2E interrupted by user: **34 passed,2 failed,1 interrupted,5 not run**. Log `/tmp/atlas-e2e.log`. This is NOT a completed E2E gate.
- Failure1: OCR task WAS persisted and browser reached task route, but direct read-only Playwright GET hit transient `socket hang up`. Added maxRetries2 to those read-only task verification GETs after failure; NOT rerun yet.
- Failure2: MCP test expects navigation landmark `Primary navigation`, but Sidebar puts that label on `<aside>` (complementary), leaving actual `<nav>` unnamed. Next resume move label from aside to nav (not yet edited), rerun affected/full suite. Other platforms/credentials/audit/upload/retry/maintenance flows passed.
- Resume workers, inspect partial DAG/network/training changes, finish their owned gates, fix Sidebar landmark, rerun full backend escalated + frontend gates + full real E2E after stable code. Use `CI=true PLAYWRIGHT_CHANNEL=chrome npm run test:e2e -- --retries=0` or set `PLAYWRIGHT_BROWSERS_PATH=/tmp/atlas-playwright`. Full user scope, independent review and final documentation/evidence remain unfinished.
