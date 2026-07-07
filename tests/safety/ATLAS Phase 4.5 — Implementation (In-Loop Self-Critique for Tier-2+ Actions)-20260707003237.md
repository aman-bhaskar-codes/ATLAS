# ATLAS Phase 4.5 — Implementation (In-Loop Self-Critique for Tier-2+ Actions)

# ATLAS Phase 4.5 — Implementation
## In-loop self-critique: generate → critique → revise/abort → (then human confirm)
> Builds on the Phase 4 runtime (Private ([https://app.clickup.com/90161683469/docs/2kz0w70d-816](https://app.clickup.com/90161683469/docs/2kz0w70d-816)) · Private ([https://app.clickup.com/90161683469/docs/2kz0w70d-836](https://app.clickup.com/90161683469/docs/2kz0w70d-836))). Fills the `ReflectionHook` seam that was shipped as `NoOpReflection`. **Zero loop rewrite** — this is a drop-in hook, exactly as promised.  
> Bar: Python 3.13, mypy --strict, ruff clean, uv, DI, everything auditable.  
> **This is the mechanism behind your self-evaluation/self-correction specialization** — the _online_ complement to the _offline_ eval harness (LX, later). For the research direction, this is the "peer-review before claiming a result" loop.
* * *
## 0\. What Phase 4.5 is (and is not)
**Is:** a cheap, local, pre-execution critique pass that runs _only for consequential actions_ (Tier-2+ / risky). It generates the action (Phase 4 already does), then a second local-model call critiques it against the task + user-model + constraints, and the loop either proceeds, revises once, or aborts.

**Is not:** a replacement for the Safety Engine. Critique runs **before** L1 and can only make an action _safer_ (revise or abort). It can never elevate privilege, clear a hard block, or skip the human confirmation that Tier-2 still requires. Belt _and_ suspenders.

**The invariant (ADR-011, now implemented):** self-critique sits before the Safety Engine, is non-privileged, and its verdict is advisory-toward-caution only. A critique that says "looks fine" does **not** downgrade the tier; a Tier-2 action still hits human confirm. A critique that says "abort" stops the action regardless.

**The 3am test applied to critique itself:** would I trust an unattended action _more_ because a cheap local model sanity-checked it against my stated intent and preferences before asking me? Yes, if and only if the critique can only tighten. That's the whole design.

* * *
## 1\. Where it slots
The Phase 4 loop already calls `self._reflection.critique(action, context)` before dispatching a tool action. Phase 4.5 replaces the injected `NoOpReflection` with a real `SelfCritique`. One wiring line changes. The loop body is untouched.

```plain
reasoning step produces Action (tool_call)
        │
        ▼
[Phase 4.5] is this action consequential? (Tier-2+ / risky)
   no  → pass through unchanged  (cheap: no extra model call for benign reads)
   yes → critique (local, thinking-off, tight budget):
           verdict ∈ {ok, revise, abort}
             ok     → proceed unchanged
             revise → regenerate the action ONCE with the critique appended
             abort  → replace with ask_user(reason); do NOT attempt
        │
        ▼
[Phase 4] dispatch → SafetyEngine.guard()  (Tier-2 still confirms with the human)
```

**Why only for consequential actions:** running a critique on every benign read doubles cost and latency for no safety gain. We gate it on the action's projected tier / risk so routine work stays fast and free. This preserves the Cheap invariant.

* * *
## 2\. Tier estimation without executing — `src/atlas/orchestration/tiering.py`
**Purpose.** Cheaply estimate whether an action is consequential _before_ the Safety Engine formally classifies it, so critique knows whether to fire. **Responsibilities.** map an `Action` to a provisional tier using the same classifier (pure, no I/O). **Dependencies.** classifier, types. **Why reuse the classifier.** one source of truth for "what's risky"; we don't want a second, drifting notion of tier inside orchestration.

```python
"""Provisional tier estimation for the critique gate.

WHY reuse the real classifier: we must not invent a second definition of
'consequential'. We build the same ToolRequest the dispatcher would and ask the
classifier (pure, no side effects) for its tier. This is an ESTIMATE used only
to decide whether to spend a critique call; the authoritative decision still
happens in SafetyEngine.guard() at dispatch.
"""

from __future__ import annotations

from atlas.infra.ids import CorrelationId
from atlas.infra.types import Tier, ToolRequest
from atlas.orchestration.types import Action
from atlas.safety.classifier import TierClassifier


class TierEstimator:
    def __init__(self, classifier: TierClassifier) -> None:
        self._clf = classifier

    def estimate(self, action: Action, correlation_id: CorrelationId) -> Tier:
        if action.kind != "tool_call" or action.tool is None or action.operation is None:
            return Tier.AUTO
        req = ToolRequest(
            correlation_id=correlation_id, tool=action.tool,
            operation=action.operation, args=action.args,
        )
        return self._clf.classify(req).tier

    def is_consequential(self, action: Action, correlation_id: CorrelationId) -> bool:
        return self.estimate(action, correlation_id) >= Tier.CONFIRM
```

* * *
## 3\. Critique contracts — extend `src/atlas/orchestration/types.py`

```python
# append to orchestration/types.py

from enum import Enum


class CritiqueVerdict(str, Enum):
    OK = "ok"           # action is sound — proceed (tier UNCHANGED)
    REVISE = "revise"   # regenerate once with the critique in mind
    ABORT = "abort"     # do not attempt; surface reason to the user


class Critique(BaseModel):
    model_config = {"frozen": True}
    verdict: CritiqueVerdict
    reason: str
    suggestion: str | None = None   # guidance for the revise pass
```

* * *
## 4\. The self-critique hook — `src/atlas/orchestration/self_critique.py`
**Purpose.** Implement `ReflectionHook` with a real, cheap critique that can revise or abort a consequential action. **Responsibilities.** gate on consequentiality; run one local critique call; on `revise`, regenerate the action once with the critique appended; on `abort`, convert to `ask_user`; audit the critique. **Dependencies.** gateway, tier estimator, parser/validator (for the revise regen), types, an audit hook. **Why regenerate only once.** a bounded correction — infinite critique/revise ping-pong would violate the loop's determinism and cost bounds.

```python
"""Self-critique hook — online self-correction for consequential actions.

CONTRACT (ADR-011): runs BEFORE the Safety Engine; can only make an action
SAFER. 'ok' does not lower the tier (Tier-2 still confirms). 'revise'
regenerates the action ONCE (bounded — no ping-pong). 'abort' turns the action
into ask_user. Benign actions skip critique entirely (cost discipline). Every
critique is audited so the learning loop (Phase 8) can see where the agent
caught itself.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from atlas.infra.ids import CorrelationId
from atlas.infra.logging import get_logger
from atlas.infra.model_gateway import ModelGateway
from atlas.infra.types import ModelRequest
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.tiering import TierEstimator
from atlas.orchestration.types import Action, Critique, CritiqueVerdict
from atlas.orchestration.validator import OutputValidator

_log = get_logger("atlas.orch.critique")

CritiqueAudit = Callable[[str, Action, Critique], Awaitable[None]]

_CRITIQUE_SYSTEM = (
    "You are a careful reviewer of an agent's PROPOSED next action, before it "
    "runs. Given the TASK, USER CONTEXT, and the PROPOSED ACTION, decide if it "
    "overreaches, misreads intent, risks an irreversible mistake, or targets the "
    "wrong thing. Output ONLY JSON: "
    '{"verdict":"ok|revise|abort","reason":str,"suggestion":str|null}. '
    "Prefer 'ok' unless there is a concrete concern. Use 'abort' only for clear, "
    "serious risk (wrong recipient, destructive scope, misread goal)."
)

_REVISE_SYSTEM = (
    "Regenerate the agent's next action, addressing the reviewer's concern. "
    "Output ONLY JSON for ONE step: "
    '{"thought":str,"confidence":0.0-1.0,"action":{"kind":"tool_call|final_answer|'
    'ask_user|noop","tool":str|null,"operation":str|null,"args":object,'
    '"final_text":str|null}}'
)


class SelfCritique:
    """Implements the ReflectionHook protocol from Phase 4."""

    def __init__(
        self, *, gateway: ModelGateway, estimator: TierEstimator,
        parser: ResponseParser, validator: OutputValidator,
        correlation_id_provider: Callable[[], CorrelationId],
        audit: CritiqueAudit | None = None,
    ) -> None:
        self._gw = gateway
        self._estimator = estimator
        self._parser = parser
        self._validator = validator
        self._corr = correlation_id_provider
        self._audit = audit

    async def critique(self, action: Action, context: str) -> Action:
        corr = self._corr()
        # 1) cost discipline: only critique consequential actions
        if not self._estimator.is_consequential(action, corr):
            return action

        # 2) one cheap local critique (thinking off, tight budget)
        verdict = await self._run_critique(action, context, corr)
        if self._audit is not None:
            await self._audit(str(corr), action, verdict)

        if verdict.verdict is CritiqueVerdict.OK:
            return action  # tier UNCHANGED — Safety Engine still gates it
        if verdict.verdict is CritiqueVerdict.ABORT:
            _log.info("critique.abort", event_type="critique",
                      correlation_id=corr, reason=verdict.reason)
            return Action(
                step=action.step, kind="ask_user",
                final_text=f"I held off: {verdict.reason} Want me to proceed anyway?",
            )
        # REVISE: regenerate once, bounded
        revised = await self._revise(action, context, verdict, corr)
        return revised

    async def _run_critique(
        self, action: Action, context: str, corr: CorrelationId
    ) -> Critique:
        prompt = (
            f"USER CONTEXT:
{context[:2000]}

"
            f"PROPOSED ACTION:
{action.model_dump_json()}"
        )
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=corr, system=_CRITIQUE_SYSTEM, prompt=prompt,
                max_tokens=250, temperature=0.0,  # thinking off: fast + cheap
            ))
            data = json.loads(self._json(resp.text))
            return Critique(
                verdict=self._verdict(data.get("verdict")),
                reason=str(data.get("reason", "")),
                suggestion=data.get("suggestion"),
            )
        except Exception as exc:
            # fail-safe: if the critic itself fails, do NOT abort or revise blindly.
            # Return OK so the action proceeds to the Safety Engine, which is the
            # authoritative gate. Critique is an ADDITIONAL check, never the only one.
            _log.warning("critique.failed_open_to_safety", event_type="critique",
                         correlation_id=corr, error=repr(exc))
            return Critique(verdict=CritiqueVerdict.OK, reason="critic unavailable")

    async def _revise(
        self, action: Action, context: str, critique: Critique, corr: CorrelationId
    ) -> Action:
        prompt = (
            f"CONTEXT:
{context[:2000]}

"
            f"ORIGINAL ACTION:
{action.model_dump_json()}

"
            f"REVIEWER CONCERN: {critique.reason}
"
            f"SUGGESTION: {critique.suggestion or '(none)'}"
        )
        try:
            resp = await self._gw.complete(ModelRequest(
                correlation_id=corr, system=_REVISE_SYSTEM, prompt=prompt,
                max_tokens=512, temperature=0.1,
            ))
            _thought, revised = self._parser.parse(resp.text, action.step)
            self._validator.validate(revised)
            return revised
        except Exception as exc:
            # revision failed -> safest fallback is to ask the human, not to run
            # the un-revised risky action.
            _log.info("critique.revise_failed_to_ask", event_type="critique",
                      correlation_id=corr, error=repr(exc))
            return Action(
                step=action.step, kind="ask_user",
                final_text=f"I wanted to adjust this action ({critique.reason}) "
                           f"but couldn't. How should I proceed?",
            )

    @staticmethod
    def _verdict(raw: object) -> CritiqueVerdict:
        try:
            return CritiqueVerdict(str(raw))
        except ValueError:
            return CritiqueVerdict.OK  # unknown -> defer to Safety Engine

    @staticmethod
    def _json(text: str) -> str:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            raise ValueError("no JSON")
        return text[s : e + 1]
```

### 4.1 The critical fail-direction decision
Note the two failure paths and why they differ:
*   **Critic call fails → return OK.** The action then proceeds _to the Safety Engine_, which is the authoritative gate. Critique is an _additional_ safety layer, never the only one; if the extra layer is unavailable, we don't block legitimate work, we fall back to the real gate (L1). This is correct because L1 is stricter and always runs.
*   **Revision fails → ask the human.** Here we already _know_ there was a concern (the critic said revise). Running the un-revised risky action would be wrong, so the safe fallback is to ask you. Different failure, different safe direction.

This asymmetry is deliberate and is the subtle part a reviewer would probe. The rule: _never let a critique failure make an action riskier than it would have been without critique at all._

* * *
## 5\. Wiring — `src/atlas/app.py`
Replace the `NoOpReflection` from Phase 4 with the real hook:

```python
from atlas.orchestration.tiering import TierEstimator
from atlas.orchestration.self_critique import SelfCritique
from atlas.infra.types import AuditRecord

# inside build(), after classifier + gateway + parser/validator exist:

    estimator = TierEstimator(classifier)

    async def critique_audit(corr: str, action, critique) -> None:
        # critiques are episodic-worthy: they're where the agent caught itself,
        # prime training signal for Phase 8 reflection + the eval set.
        await audit.record(AuditRecord(
            correlation_id=corr, ts=clock.now(), actor="critique",
            action="self_critique", tool=action.tool,
            outcome=critique.verdict.value,
            payload={"reason": critique.reason, "action": action.model_dump()},
        ))

    reflection = SelfCritique(
        gateway=gateway, estimator=estimator,
        parser=ResponseParser(), validator=OutputValidator(),
        correlation_id_provider=ids.correlation_id, audit=critique_audit,
    )

    # pass `reflection` into ReasoningLoop instead of NoOpReflection — that's the
    # ONLY change to Phase 4 wiring. The loop body is unchanged.
    reasoning = ReasoningLoop(
        gateway=gateway, dispatcher=dispatcher, parser=ResponseParser(),
        validator=OutputValidator(), prompts=PromptBuilder(), recorder=recorder,
        monitor=ExecutionMonitor(killswitch), retry=RetryManager(),
        reflection=reflection,          # <-- was NoOpReflection()
        events=events, limits=ExecutionLimits(),
    )
```

That is the entire integration. The promise from Phase 4 ("replace `NoOpReflection`, no loop rewrite") holds exactly.

* * *
## 6\. Config toggle — `config/settings.yaml`

```yaml
critique:
  enabled: true          # master switch
  min_tier: 2            # only critique actions at/above this tier (Tier.CONFIRM)
  revise_max: 1          # bounded regeneration (no ping-pong)
```

Add `CritiqueCfg` to `config.py` and let `SelfCritique` read `enabled`/`min_tier` (estimator threshold) so you can turn it off or widen it without code changes. Off = behaves exactly like Phase 4.

* * *
## 7\. Tests — `tests/orchestration/test_self_critique.py`

```python
from atlas.infra.ids import CorrelationId
from atlas.infra.types import ModelResponse, ModelTarget, SafetyDecision, Tier, TokenCost
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.self_critique import SelfCritique
from atlas.orchestration.tiering import TierEstimator
from atlas.orchestration.types import Action
from atlas.orchestration.validator import OutputValidator


class ScriptedGateway:
    def __init__(self, texts): self._t = texts; self._i = 0
    async def complete(self, req):
        text = self._t[min(self._i, len(self._t) - 1)]; self._i += 1
        return ModelResponse(text=text, target=ModelTarget.LOCAL_FAST, model="fake",
                             cost=TokenCost(input_tokens=1, output_tokens=1))


class FakeClassifier:
    """Returns a fixed tier so we control 'consequential'."""
    def __init__(self, tier): self._tier = tier
    def classify(self, req):
        return SafetyDecision(decision="require_confirm", tier=self._tier, reason="x")


def _hook(gateway, tier):
    est = TierEstimator(FakeClassifier(tier))
    return SelfCritique(gateway=gateway, estimator=est, parser=ResponseParser(),
                        validator=OutputValidator(),
                        correlation_id_provider=lambda: CorrelationId("c"))


_TOOL_ACTION = Action(step=1, kind="tool_call", tool="filesystem",
                      operation="delete", args={"path": "/x"})


async def test_benign_action_skips_critique():
    # tier AUTO => not consequential => gateway never called
    gw = ScriptedGateway(["SHOULD NOT BE USED"])
    hook = _hook(gw, Tier.AUTO)
    out = await hook.critique(Action(step=1, kind="tool_call", tool="filesystem",
                                     operation="read", args={}), "ctx")
    assert out.operation == "read" and gw._i == 0  # no critique call spent


async def test_ok_verdict_passes_through_unchanged():
    gw = ScriptedGateway(['{"verdict":"ok","reason":"fine"}'])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.kind == "tool_call" and out.operation == "delete"  # UNCHANGED (still gated by L1)


async def test_abort_becomes_ask_user():
    gw = ScriptedGateway(['{"verdict":"abort","reason":"wrong directory"}'])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.kind == "ask_user" and "wrong directory" in (out.final_text or "")


async def test_revise_regenerates_once():
    gw = ScriptedGateway([
        '{"verdict":"revise","reason":"scope too broad","suggestion":"target one file"}',
        '{"thought":"narrower","confidence":0.9,"action":{"kind":"tool_call",'
        '"tool":"filesystem","operation":"delete","args":{"path":"/x/one.txt"}}}',
    ])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.args["path"] == "/x/one.txt"  # revised, narrower


async def test_critic_failure_falls_open_to_safety():
    # unparseable critique => OK => action proceeds to the (authoritative) Safety Engine
    gw = ScriptedGateway(["garbage not json"])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.kind == "tool_call" and out.operation == "delete"


async def test_revise_failure_falls_to_ask_user():
    gw = ScriptedGateway([
        '{"verdict":"revise","reason":"scope","suggestion":"narrow"}',
        "garbage — revision unparseable",
    ])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.kind == "ask_user"  # known concern + failed revise => ask human
```

* * *
## 8\. Acceptance checklist
- [ ] `ruff` / `mypy --strict` / `lint-imports` / `pytest` green.
- [ ] Only ONE Phase 4 wiring line changed (`NoOpReflection` → `SelfCritique`); loop body untouched.
- [ ] Benign actions skip critique entirely (no model call, cost preserved).
- [ ] `ok` passes the action through **unchanged** and does NOT lower its tier (L1 still gates).
- [ ] `abort` converts to `ask_user` with the reason; action never attempted.
- [ ] `revise` regenerates the action exactly once (bounded, no ping-pong); revised action is validated.
- [ ] Critic-call failure → OK (fall open to the authoritative Safety Engine).
- [ ] Revision failure → ask\_user (never run the un-revised risky action).
- [ ] Every critique is audited (`actor="critique"`), feeding Phase 8 reflection + the eval set.
- [ ] Config toggle: disabling critique reverts behavior to exact Phase 4.

* * *
## 9\. Why this is the right shape (the defense)
*   **It can only tighten.** Every path either leaves the action unchanged, makes it narrower (revise), or stops it (abort/ask). There is no path where critique _expands_ an action's scope or privilege. That's what makes it safe to run before L1.
*   **It's cheap by gating.** Benign reads never pay for critique. Only consequential actions do, and it's a single tight local call. Cheap invariant intact.
*   **Its failures are safe in both directions,** and the two directions differ correctly (critic-fail → defer to L1; revise-fail → ask human).
*   **It's a learning substrate.** Audited critiques are exactly the "where did the agent almost go wrong, and catch itself" signal that Phase 8 reflection distills and the eval set (LX) freezes. This is the online half of your self-correction specialization; the offline half comes later.
*   **It generalizes to the research agent.** "Generate a result → critique it against the data/goal → revise or withhold" is peer-review. Same code path, different domain. This is arguably the single most important enhancement for your long-term Sakana/Deep-Research direction, and it's now real.

* * *
## TL;DR
Phase 4.5 fills the `ReflectionHook` seam with a real self-critique that runs only for consequential (Tier-2+) actions: a cheap local critic returns ok/revise/abort, and the loop proceeds unchanged, regenerates the action once, or converts it to a question, all _before_ the Safety Engine, which still gates everything. It can only make actions safer, never grant privilege; its failure directions are deliberately asymmetric (critic-fail defers to L1, revise-fail asks you); benign actions skip it entirely to stay cheap; and every critique is audited as learning signal. One wiring line changed, zero loop rewrite. This is the online self-correction engine behind your specialization, and the same loop is peer-review for the research agent. Want Phase 5 (bring DeepSeek/GLM/Kimi online with the cost governor live) next?