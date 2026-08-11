"""Tests for agent system — DAG, registry, specialists."""

from __future__ import annotations

import pytest

from atlas.agents.base import AgentConfig
from atlas.agents.dag import SubTask, TaskDAG
from atlas.agents.registry import AgentRegistry

# ── TaskDAG tests ──────────────────────────────────────────────────── #

def test_dag_topological_batches_linear() -> None:
    dag = TaskDAG(goal="test", subtasks=[
        SubTask(id="a", description="first", agent_type="general"),
        SubTask(id="b", description="second", agent_type="general", dependencies=["a"]),
        SubTask(id="c", description="third", agent_type="general", dependencies=["b"]),
    ])
    batches = dag.topological_batches()
    assert len(batches) == 3
    assert [st.id for st in batches[0]] == ["a"]
    assert [st.id for st in batches[1]] == ["b"]
    assert [st.id for st in batches[2]] == ["c"]


def test_dag_topological_batches_parallel() -> None:
    dag = TaskDAG(goal="test", subtasks=[
        SubTask(id="a", description="independent 1", agent_type="researcher"),
        SubTask(id="b", description="independent 2", agent_type="writer"),
        SubTask(id="c", description="merge", agent_type="general", dependencies=["a", "b"]),
    ])
    batches = dag.topological_batches()
    assert len(batches) == 2
    batch_0_ids = sorted([st.id for st in batches[0]])
    assert batch_0_ids == ["a", "b"]  # parallel
    assert [st.id for st in batches[1]] == ["c"]


def test_dag_cycle_detection() -> None:
    dag = TaskDAG(goal="test", subtasks=[
        SubTask(id="a", description="one", agent_type="general", dependencies=["b"]),
        SubTask(id="b", description="two", agent_type="general", dependencies=["a"]),
    ])
    errors = dag.validate()
    assert len(errors) > 0
    assert any("Cycle" in e for e in errors)


def test_dag_empty() -> None:
    dag = TaskDAG(goal="test", subtasks=[])
    assert dag.topological_batches() == []
    assert dag.validate() == []


def test_dag_unknown_dependency() -> None:
    dag = TaskDAG(goal="test", subtasks=[
        SubTask(id="a", description="x", agent_type="general", dependencies=["nonexistent"]),
    ])
    errors = dag.validate()
    assert any("unknown" in e for e in errors)


# ── AgentRegistry tests ───────────────────────────────────────────── #

def test_registry_register_and_get() -> None:
    registry = AgentRegistry()
    config = AgentConfig(agent_type="test", system_prompt="test prompt")

    class DummyAgent:
        def __init__(self, cfg: AgentConfig) -> None:
            self.config = cfg

    registry.register(config, DummyAgent)  # type: ignore
    assert registry.has("test")
    assert not registry.has("unknown")
    assert "test" in registry.agent_types

    agent = registry.get("test")
    assert agent.config.agent_type == "test"


def test_registry_get_unknown_raises() -> None:
    registry = AgentRegistry()
    with pytest.raises(KeyError, match="not registered"):
        registry.get("nonexistent")


def test_registry_list_agents() -> None:
    registry = AgentRegistry()
    c1 = AgentConfig(agent_type="a", system_prompt="p1")
    c2 = AgentConfig(agent_type="b", system_prompt="p2")
    registry.register(c1, lambda c: None)  # type: ignore
    registry.register(c2, lambda c: None)  # type: ignore
    listed = registry.list_agents()
    assert len(listed) == 2
    assert {c.agent_type for c in listed} == {"a", "b"}
