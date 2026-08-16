"""Shared LLM plan deserialization.

WHY one function: Planner and Replanner previously carried identical copies of
this parsing logic. LLM JSON is defensive-parse territory (every field may be
missing or mistyped), so a single hardened implementation is the only way to
keep the two paths provably consistent — a divergence would mean replans
silently accept plans the planner would reject.
"""

from __future__ import annotations

from atlas.orchestration.types import Plan, PlanStep, RiskLevel


def plan_from_llm_json(data: dict[str, object]) -> Plan:
    """Build a Plan from defensively-parsed LLM JSON output.

    Never raises for missing/mistyped fields; falls back to safe defaults
    (medium risk, 0.5 confidence) so callers can decide whether the result is
    usable.
    """
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        raw_steps = []
    steps = tuple(
        PlanStep(
            index=int(s.get("index", 0)),
            intent=str(s.get("intent", "")),
            tool=s.get("tool") if isinstance(s.get("tool"), str) else None,
            operation=s.get("operation") if isinstance(s.get("operation"), str) else None,
            args=dict(s.get("args", {})) if isinstance(s.get("args"), dict) else {},
            depends_on=tuple(int(d) for d in s.get("depends_on", []) if isinstance(d, (int, str, float))),
            expected_output=s.get("expected_output") if isinstance(s.get("expected_output"), str) else None,
        )
        for s in raw_steps
        if isinstance(s, dict)
    )
    raw_constraints = data.get("constraints")
    constraints = tuple(str(c) for c in raw_constraints) if isinstance(raw_constraints, list) else ()
    raw_tc = data.get("termination_conditions")
    tc = tuple(str(t) for t in raw_tc) if isinstance(raw_tc, list) else ()
    raw_unk = data.get("unknowns")
    unknowns = tuple(str(u) for u in raw_unk) if isinstance(raw_unk, list) else ()
    raw_cost = data.get("estimated_cost_usd")
    raw_conf = data.get("confidence")
    try:
        risk = RiskLevel(str(data.get("risk", "medium")))
    except ValueError:
        risk = RiskLevel.MEDIUM

    return Plan(
        goal=str(data.get("goal", "")),
        constraints=constraints,
        steps=steps,
        termination_conditions=tc,
        risk=risk,
        estimated_cost_usd=float(str(raw_cost)) if raw_cost is not None else 0.0,
        confidence=float(str(raw_conf)) if raw_conf is not None else 0.5,
        unknowns=unknowns,
    )


def extract_json_object(text: str) -> str:
    """Slice the outermost JSON object out of free model text (handles prose,
    markdown fences, thinking prefixes). Raises ValueError when absent."""
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        raise ValueError("no JSON object in response")
    return text[s : e + 1]
