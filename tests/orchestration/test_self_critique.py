from atlas.infra.ids import CorrelationId
from atlas.infra.types import ModelResponse, ModelTarget, SafetyDecision, Tier, TokenCost
from atlas.orchestration.parser import ResponseParser
from atlas.orchestration.self_critique import SelfCritique
from atlas.orchestration.tiering import TierEstimator
from atlas.orchestration.types import Action
from atlas.orchestration.validator import OutputValidator


class ScriptedGateway:
    def __init__(self, texts: list[str]) -> None:
        self._t = texts
        self._i = 0

    async def complete(self, req: object) -> ModelResponse:
        text = self._t[min(self._i, len(self._t) - 1)]
        self._i += 1
        return ModelResponse(text=text, target=ModelTarget.LOCAL_FAST, model="fake",
                             cost=TokenCost(input_tokens=1, output_tokens=1))


class FakeClassifier:
    """Returns a fixed tier so we control 'consequential'."""
    def __init__(self, tier: Tier) -> None:
        self._tier = tier

    def classify(self, req: object) -> SafetyDecision:
        return SafetyDecision(decision="require_confirm", tier=self._tier, reason="x")


def _hook(gateway: ScriptedGateway, tier: Tier) -> SelfCritique:
    est = TierEstimator(FakeClassifier(tier))  # type: ignore
    return SelfCritique(gateway=gateway, estimator=est, parser=ResponseParser(),  # type: ignore
                        validator=OutputValidator(),
                        correlation_id_provider=lambda: CorrelationId("c"))


_TOOL_ACTION = Action(step=1, kind="tool_call", tool="filesystem",
                      operation="delete", args={"path": "/x"})


async def test_benign_action_skips_critique() -> None:
    # tier AUTO => not consequential => gateway never called
    gw = ScriptedGateway(["SHOULD NOT BE USED"])
    hook = _hook(gw, Tier.AUTO)
    out = await hook.critique(Action(step=1, kind="tool_call", tool="filesystem",
                                     operation="read", args={}), "ctx")
    assert out.operation == "read" and gw._i == 0  # no critique call spent


async def test_ok_verdict_passes_through_unchanged() -> None:
    gw = ScriptedGateway(['{"verdict":"ok","reason":"fine"}'])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.kind == "tool_call" and out.operation == "delete"  # UNCHANGED (still gated by L1)


async def test_abort_becomes_ask_user() -> None:
    gw = ScriptedGateway(['{"verdict":"abort","reason":"wrong directory"}'])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.kind == "ask_user" and "wrong directory" in (out.final_text or "")


async def test_revise_regenerates_once() -> None:
    gw = ScriptedGateway([
        '{"verdict":"revise","reason":"scope too broad","suggestion":"target one file"}',
        '{"thought":"narrower","confidence":0.9,"action":{"kind":"tool_call",'
        '"tool":"filesystem","operation":"delete","args":{"path":"/x/one.txt"}}}',
    ])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.args["path"] == "/x/one.txt"  # revised, narrower


async def test_critic_failure_falls_open_to_safety() -> None:
    # unparseable critique => OK => action proceeds to the (authoritative) Safety Engine
    gw = ScriptedGateway(["garbage not json"])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.kind == "tool_call" and out.operation == "delete"


async def test_revise_failure_falls_to_ask_user() -> None:
    gw = ScriptedGateway([
        '{"verdict":"revise","reason":"scope","suggestion":"narrow"}',
        "garbage — revision unparseable",
    ])
    out = await _hook(gw, Tier.CONFIRM).critique(_TOOL_ACTION, "ctx")
    assert out.kind == "ask_user"  # known concern + failed revise => ask human
