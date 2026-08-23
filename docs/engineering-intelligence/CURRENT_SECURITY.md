# CURRENT_SECURITY — the existing trust boundary

**Method.** Read from the tree this pass. This document exists so the engineering layer does
not build a second security system (spec §1) and does not weaken the one that is there
(§121).

---

## 1. The central invariant

`src/atlas/safety/engine.py` states it in its module docstring: **nothing executes a tool
except through `guard()`.** Every tool call, without exception, passes the safety pipeline.

Pipeline order, as implemented:

```
kill-switch check
  → classify
    → policy chain
      → AUDIT (write)
        → branch: allow / confirm / deny
```

Two properties worth naming because they are easy to break:

1. **AUDIT happens before the branch.** The decision is recorded whether or not the action
   proceeds. A denial is as auditable as an allow.
2. **The kill switch is re-checked after any confirmation wait.** A human pressing stop while
   a confirmation prompt is open is honoured. Without the re-check, consent obtained before
   the kill would authorise an action after it.

**Consequence for §33 and §121.** The engineering layer's security gate is not a new
authority. It is a *caller* of this engine. A repair that touches the filesystem, runs a
command, or reaches the network must go through `guard()` like anything else. §121's "final
security boundary" is already this boundary; the engineering layer's job is to route through
it, never around it.

---

## 2. Redaction — ✅ already implemented, three places

**`safety/engine.py`** (lines 1–60):

- `_SECRET_FIELDS` — **15 field names** matched by key.
- `_SECRET_PATTERN` — matches `Bearer \S+`, JWTs (`eyJ…`), OpenAI-style keys (`sk-…`),
  GitHub tokens (`ghp_…`).
- `_MAX_PAYLOAD_VALUE_LEN = 2000` — values are truncated, so a large blob cannot be smuggled
  into an audit row.
- `_redact_payload(...)` applies both, by key and by pattern.

**`capabilities/computer_use/redaction.py`** — redaction for screen/computer-use capture.

**`frontend/lib/api/client.ts`** — `AtlasApiError.detail` is truncated to 500 characters, so a
server error cannot spill a long payload into the browser or into a screenshot.

**Against the standing constraint** ("Never log: API keys, passwords, secrets, full private
content, raw credential-bearing prompts") and §75 ("never expose sensitive payloads"): the
redaction primitive exists and is tested. **New engineering-layer records must call
`_redact_payload` (or an exported equivalent) before persisting any captured payload,
evidence blob, log excerpt, or patch diff.** This is the single most important reuse
requirement in the whole security section — an incident's "evidence" field is precisely the
kind of place a credential leaks.

Note `_redact_payload` is currently module-private. Exposing it (or a thin public wrapper)
is preferable to copying the pattern list, because a copied pattern list drifts.

---

## 3. Prompt injection — ✅ detection exists

`src/atlas/knowledge/injection.py`:

```
scan_for_injection(text, *, sample_cap=3) -> InjectionReport
```

`sample_cap` bounds how much matched text is retained — i.e. the report itself is designed
not to become a copy of the payload.

**Against §110 (the prompt-injection E2E test) and §52–§53.** Detection is present.
What is absent is everything after detection: there is no `SecurityIncident` record, no
containment step, no evidence preservation, no notification, and no link from an injection
report to the evaluation plane. §53's chain (detect → contain → block → preserve evidence →
notify → investigate → evaluate response) has exactly **one** link implemented.

---

## 4. Audit trail — ✅ tamper-evident

Tables `audit_events` and `payloads`. `audit_events` carries **`prev_hash` and `row_hash`** —
a hash chain, so a retroactively edited or deleted row is detectable. `diagnostics/doctor.py`
includes an `audit.chain` check that verifies it.

**Against §86 ("Nothing is silently changed") and the constraint that the model must never
"modify incident history / modify audit records".** The mechanism to *detect* that violation
already exists. The engineering layer should write repair decisions into this same chain
rather than a private log, so that a repair record inherits tamper-evidence for free. It must
not gain a delete or update path.

---

## 5. Authentication and authorisation — ✅ opt-in, fail-closed

`src/atlas/interfaces/api/auth.py` + `create_app()`:

- `require_principal` returns `ANONYMOUS_LOCAL` when `app.state.api_keys` is empty, so with
  no `ATLAS_API_KEYS` configured the local workflow is unchanged.
- Setting `ATLAS_API_KEYS` starts enforcement: 401 without a header, 200 with a valid key.
- A `ro:`-prefixed key is rejected on mutating methods —
  `_SAFE_METHODS = frozenset({"GET","HEAD","OPTIONS"})` via `_reject_readonly_mutation`.
- **Fail-closed:** if `ATLAS_API_KEYS` is present in the environment but unparseable,
  `create_app()` raises `RuntimeError`. An operator who configured auth can never
  accidentally get an unauthenticated server. No key material appears in any log line — only
  the exception *type*.
- `require_principal` sets `request.state.principal`, which makes the rate limiter key per
  principal instead of per IP.
- 16 routers; `auth_required` on all but `health_router` (probes must stay open for the
  container `HEALTHCHECK`), `memory_router`, `events_ws_router`.

**Known gap — `docs/final/TECHNICAL_DEBT_FINAL.md` #20.** WebSocket routes carry **no**
authentication: `require_principal` takes a `Request`, which does not resolve in a WebSocket
scope, so `memory_router` and `events_ws_router` are included without the dependency. With
`ATLAS_API_KEYS` set, HTTP is enforced and WebSockets are open. This was left honest rather
than papered over with a `Depends` that silently no-ops.

**Direct consequence for this build:** if incidents get a live stream, **it must not be a
WebSocket carrying incident detail**, or it inherits an unauthenticated read of security
data. Use SSE behind `auth_required`, or poll. This is a design constraint, not a preference.

---

## 6. Architectural containment — ✅ enforced by import-linter

`importlinter.ini` has three contracts:

1. **Layers** (top may import lower, never the reverse):
   `atlas.interfaces` → `atlas.diagnostics` → `atlas.adaptation` → `atlas.evaluation` →
   `atlas.orchestration` → `atlas.knowledge` → `atlas.capabilities` → `atlas.memory` →
   `atlas.intelligence` → `atlas.safety` → `atlas.tools` → `atlas.control` →
   `atlas.perception` → `atlas.infra`.
   `ignore_imports`: `atlas.tools.filesystem -> atlas.safety.sandbox`,
   `atlas.tools.shell -> atlas.safety.sandbox`, `atlas.diagnostics.doctor -> atlas.app`.
   (`atlas.agents`, `atlas.autonomy`, `atlas.training`, `atlas.bootstrap` are **not** in the
   contract.)
2. **`infra-knows-no-policy`** — `atlas.infra` must not import safety, tools, interfaces,
   diagnostics, evaluation, adaptation, capabilities, intelligence or memory.
3. **`provider-sdk-containment`** — `atlas.safety` and `atlas.tools` must not import
   `atlas.infra.providers`.

**Why this constrains the engineering layer.** Safety sits *low* in the stack (above tools,
below intelligence). Anything that wants to consult safety may import it; safety may not
import upward. So an engineering layer **cannot** be imported by `atlas.safety` — which is
exactly right: the safety engine must not depend on the thing it is authorising. The layer
belongs between `atlas.diagnostics` and `atlas.adaptation`, matching §123's diagram, giving
it read access to everything below and making it importable by `interfaces` and
`diagnostics`.

Contract 2 also means the incident *store* cannot live in `atlas.infra` if it needs to
consult safety — which it will, for redaction. Put the store in `atlas.engineering`.

---

## 7. Operational security checks — ✅ in `doctor`

`src/atlas/diagnostics/doctor.py` (172 lines) already runs, among others:
`secrets.ntfy`, `permissions`, `identity.encryption`, `identity.credentials`, `audit.chain`,
`sandbox.runtime`. See `CURRENT_DIAGNOSTICS.md`.

---

## 8. Patch-boundary enforcement — ❌ absent

§32 and §83 require that autonomous repair can never touch `src/atlas/safety/**`,
`credentials/**`, or deployment secrets. **There is no such mechanism today**, because there
is no autonomous patching today. The import-linter layers are a *build-time* structural
guard, not a write-time path guard; nothing prevents a process with filesystem access from
writing into `src/atlas/safety/`.

This must be an explicit deny-list check inside the repair pipeline, evaluated on the
concrete file paths of a candidate diff **before** the diff is applied anywhere — not a
convention, and not a prompt instruction to the model. Per the spec's own framing: "The
model is untrusted computation inside a trusted control plane."

---

## 9. Summary

| §  | Requirement | State |
|---|---|---|
| 33 | Safety/security gate for repairs | ⚠️ engine exists; no repair caller |
| 51 | Safety events surfaced even when the task succeeded | ⚠️ audited, never surfaced in UI |
| 52 | `SecurityIncident` model | ❌ absent |
| 53 | detect → contain → block → preserve → notify → investigate → evaluate | ⚠️ detect only |
| 75 | `/system/security` that never exposes sensitive payloads | ❌ route absent; redaction ready |
| 86 | Full repair audit, nothing silently changed | ⚠️ hash-chained audit exists, unused by repair |
| 32/83 | Patch boundary deny-list | ❌ absent — must be built |
| 121 | Final security boundary | ✅ `guard()`, and it must not be bypassed |
