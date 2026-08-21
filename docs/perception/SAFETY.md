# Safety — one boundary for every body

Computer use does NOT get its own safety system. Every action flows through
the SAME SafetyEngine that guards filesystem, shell, email and calendar.

## The tool is the boundary

`ComputerUseTool` (`computer_use/tool.py`) presents the whole substrate
family as ONE tool (`computer_use`) with three ops:

| op | effect |
| --- | --- |
| `health` | read-only introspection (registered adapters, per-substrate status) |
| `perceive` | read-only observation (redacted snapshot summary) |
| `act` | mutation — gated like any other tool call |

Because it is an ordinary Tool, `SafetyEngine.guard()` sees every `act`
before execution: tier classification, permission manifest, confirmation for
higher tiers, and audit recording all apply unchanged.

## Honest result reporting

The tool output distinguishes what actually happened:

- `executed` — the adapter primitive ran (`ControlResult.ok`)
- `verified` — post-action perception proved the expectations
  (`None` when no verification was attempted — never upgraded to true)
- `evidence` / `resolved` — proof artifacts (what matched, how, confidence)
- `note` / `error` — honest failure reasons ("target not found in perception")

A failed verification can NEVER be reported as success (Phase 47).

## Side effects are declared, not implied

A successful mutating action produces an explicit `SideEffect` record:

```
kind     = computer_use.<substrate>.<operation>
target   = the resolved target (or URL / substrate)
detail   = post-execution evidence from the adapter
reversible = action.reversibility declaration
```

`_MUTATING` defines which operations count (click, type_text, navigate, tap,
launch, …). Read-only ops produce no side-effect records.

## Engine-level gates (before safety even runs)

1. **Resolution gate** — target must resolve against real perception with
   evidence; unresolvable targets are refused, coordinates never invented
2. **Health gate** — missing/unhealthy adapter → honest limitation message
3. **Verification gate** — expectations are declarative and checked against
   fresh re-perception

## Action metadata (Phase 6)

Every `ControlAction` carries `confidence`, `evidence`, `risk_hint` and
`reversible` — the proposer's declarations. The SafetyEngine's classifier
remains authoritative over `risk_hint`.

## Public-API safety

The connector funnel adds its own honesty layers (see `PUBLIC_APIS.md`):
execution refused for anything not `VALIDATED`/`AVAILABLE`, results always
`trust="untrusted"`, HTTPS-only validation probes.

## Redaction

Snapshots are redacted (`redact_snapshot`) before entering tool output,
model context or logs — sensitive field names and secret-shaped values
become `[REDACTED]`. See `VISION.md`.

## What this design refuses to do

- claim a click happened without dispatch evidence
- claim a goal was achieved without verification evidence
- act on a guessed location when structured resolution fails
- promote a connector to executable without recorded validation evidence
- simulate a missing substrate to make tests/demos look better
