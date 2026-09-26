"""Routing IR model tests (Part 3 §5/§38/§84)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from atlas.tooling.routing.models import (
    CandidateType,
    GraphEdgeCondition,
    GraphNodeType,
    RouteCandidate,
    RouteDecision,
    RouteEdge,
    RouteGraph,
    RouteNode,
    RouteStep,
    TaskIR,
)


def test_task_ir_is_frozen_and_minimal() -> None:
    task = TaskIR(task_id="t1", correlation_id="c1", objective="do a thing")
    assert task.complexity.value == "moderate"
    with pytest.raises(ValidationError):
        task.objective = "mutated"  # type: ignore[misc]


def test_route_candidate_supports_all_candidate_types() -> None:
    """§17: the IR carries every future candidate type; Part 3 populates
    TOOL/DOMAIN/STRATEGY but nothing breaks for the others."""
    for candidate_type in CandidateType:
        candidate = RouteCandidate(candidate_type=candidate_type, candidate_id=f"x:{candidate_type.value}")
        assert candidate.candidate_type == candidate_type


def test_route_graph_requires_terminal_for_valid_routes() -> None:
    """§84: a route graph without END is invalid — detected structurally."""
    graph = RouteGraph(
        plan_id="p1",
        nodes=(RouteNode(node_id="a", node_type=GraphNodeType.EXECUTE),),
        edges=(),
    )
    assert not graph.has_terminal()
    complete = RouteGraph(
        plan_id="p2",
        nodes=(
            RouteNode(node_id="a", node_type=GraphNodeType.EXECUTE),
            RouteNode(node_id="end", node_type=GraphNodeType.END),
        ),
        edges=(
            RouteEdge(from_node="a", to_node="end", condition=GraphEdgeCondition.SUCCESS),
            RouteEdge(from_node="a", to_node="end", condition=GraphEdgeCondition.FAILURE),
        ),
    )
    assert complete.has_terminal()
    assert complete.successors("a") == ["end", "end"]
    assert complete.successors("a", GraphEdgeCondition.FAILURE) == ["end"]


def test_route_decision_is_persistable_json() -> None:
    """§55: a decision round-trips through JSON without information loss."""
    decision = RouteDecision(
        route_id="route-1",
        request_id="t1",
        correlation_id="c1",
        objective="obj",
        decision_type="route",
        domain="general",
        strategy="DIRECT",
        selected_candidate="native:atlas:filesystem",
        fallback_candidates=("native:atlas:shell",),
        ranking_scores={"native:atlas:filesystem": 0.8},
        reasons=("rule matched",),
    )
    restored = RouteDecision.model_validate_json(decision.model_dump_json())
    assert restored == decision
    assert RouteStep(step_id="s1").depends_on == ()
