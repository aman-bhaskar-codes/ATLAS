"""Task DAG — directed acyclic graph for multi-agent task decomposition.

WHY: the Vamos Supervisor decomposes complex tasks into a DAG of subtasks.
Each subtask specifies which specialist agent should handle it, what it depends
on, and what data flows from dependencies. The DAG is executed in topological
order — independent subtasks run in parallel, dependent ones wait.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SubTask:
    """A single unit of work in the task DAG."""
    id: str
    description: str
    agent_type: str                                 # which specialist handles this
    dependencies: list[str] = field(default_factory=list)  # subtask IDs that must complete first
    inputs: dict[str, str] = field(default_factory=dict)   # maps param → dependency_id


@dataclass
class TaskDAG:
    """Directed acyclic graph of subtasks for parallel execution.

    The Supervisor generates this from an LLM call. The orchestrator executes
    it in topological order.
    """
    goal: str
    subtasks: list[SubTask] = field(default_factory=list)

    def topological_batches(self) -> list[list[SubTask]]:
        """Yield batches of subtasks that can run in parallel.

        Each batch contains only subtasks whose dependencies have all been
        satisfied by previous batches.
        """
        if not self.subtasks:
            return []

        # Build adjacency info
        task_map = {st.id: st for st in self.subtasks}
        in_degree: dict[str, int] = {st.id: len(st.dependencies) for st in self.subtasks}
        dependents: dict[str, list[str]] = {st.id: [] for st in self.subtasks}
        for st in self.subtasks:
            for dep in st.dependencies:
                if dep in dependents:
                    dependents[dep].append(st.id)

        completed: set[str] = set()
        batches: list[list[SubTask]] = []

        while len(completed) < len(self.subtasks):
            # Find all tasks with in_degree 0 that aren't completed
            batch_ids = [
                tid for tid, deg in in_degree.items()
                if deg == 0 and tid not in completed
            ]
            if not batch_ids:
                # Cycle detected or invalid DAG
                remaining = [tid for tid in task_map if tid not in completed]
                raise ValueError(
                    f"Cycle detected in task DAG. Remaining: {remaining}"
                )

            batch = [task_map[tid] for tid in batch_ids]
            batches.append(batch)
            completed.update(batch_ids)

            # Decrease in_degree for dependents
            for tid in batch_ids:
                for dependent in dependents[tid]:
                    in_degree[dependent] -= 1

        return batches

    def validate(self) -> list[str]:
        """Validate the DAG. Returns list of error messages (empty = valid)."""
        errors: list[str] = []
        ids = {st.id for st in self.subtasks}
        for st in self.subtasks:
            for dep in st.dependencies:
                if dep not in ids:
                    errors.append(f"SubTask {st.id!r} depends on unknown {dep!r}")
                if dep == st.id:
                    errors.append(f"SubTask {st.id!r} depends on itself")
        # Check for cycles via topological sort
        try:
            self.topological_batches()
        except ValueError as e:
            errors.append(str(e))
        return errors
