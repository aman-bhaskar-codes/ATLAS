"""Deterministic failure clustering (Prompt 4 §8).

"Use deterministic clustering first. Use embeddings only when useful."
Repeated failures are grouped by exact (class, component/tool, model, task
type) keys — cheap, stable, reproducible. A cluster with at least
MIN_EVIDENCE_DEFAULT members becomes candidate evidence for hypothesis
generation (§16); a single noisy event never does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas.adaptation.domain import MIN_EVIDENCE_DEFAULT
from atlas.adaptation.taxonomy import FailureClass, FailureTaxonomy, domain_of
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import Trajectory

_log = get_logger("atlas.adaptation.clustering")


@dataclass(frozen=True)
class ClusterKey:
    """Deterministic identity of a failure pattern."""

    failure_class: str
    component: str = ""  # tool name / provider / component that failed
    model: str = ""  # model_version of the trajectories involved
    task_class: str = ""  # coarse task class (goal prefix) when known


@dataclass(frozen=True)
class FailureCluster:
    """A repeated failure pattern — candidate evidence for adaptation."""

    key: ClusterKey
    failure_ids: tuple[str, ...] = ()
    trajectory_ids: tuple[str, ...] = ()
    first_seen_ts: str = ""
    last_seen_ts: str = ""

    @property
    def count(self) -> int:
        return len(self.trajectory_ids)

    @property
    def is_candidate_evidence(self) -> bool:
        """§16: only repeated patterns may seed hypotheses."""
        return self.count >= MIN_EVIDENCE_DEFAULT


@dataclass
class ClusterIndex:
    """Mutable accumulator used while scanning failures."""

    clusters: dict[ClusterKey, list[FailureTaxonomy]] = field(default_factory=dict)


def _task_class(trajectory: Trajectory | None) -> str:
    """Coarse, deterministic task class: first token-bucket of the goal."""
    if trajectory is None or not trajectory.goal:
        return ""
    head = trajectory.goal.strip().lower().split(maxsplit=1)[0]
    return head[:32]


def cluster_failures(
    failures: tuple[FailureTaxonomy, ...],
    trajectories: dict[str, Trajectory] | None = None,
    *,
    components: dict[str, str] | None = None,
) -> tuple[FailureCluster, ...]:
    """Group classified failures into deterministic clusters.

    `components` optionally maps failure_id → component/tool name (extracted
    by the caller from failure records); `trajectories` maps trajectory_id →
    trajectory for model/task-class enrichment.
    """
    trajectories = trajectories or {}
    components = components or {}
    index = ClusterIndex()
    for failure in failures:
        trajectory = trajectories.get(failure.trajectory_id)
        key = ClusterKey(
            failure_class=failure.failure_class.value,
            component=components.get(failure.failure_id, ""),
            model=(trajectory.model_version or "") if trajectory else "",
            task_class=_task_class(trajectory),
        )
        index.clusters.setdefault(key, []).append(failure)

    result: list[FailureCluster] = []
    for key, members in index.clusters.items():
        timestamps = sorted(m.created_ts for m in members)
        result.append(
            FailureCluster(
                key=key,
                failure_ids=tuple(m.failure_id for m in members),
                trajectory_ids=tuple(dict.fromkeys(m.trajectory_id for m in members)),
                first_seen_ts=timestamps[0],
                last_seen_ts=timestamps[-1],
            )
        )
    result.sort(key=lambda c: c.count, reverse=True)
    candidates = [c for c in result if c.is_candidate_evidence]
    top_domain = domain_of(FailureClass(result[0].key.failure_class)).value if result else ""
    _log.info(
        "failures.clustered",
        event_type="adaptation",
        failures=len(failures),
        clusters=len(result),
        candidate_clusters=len(candidates),
        top_domain=top_domain,
    )
    return tuple(result)


def candidate_clusters(clusters: tuple[FailureCluster, ...]) -> tuple[FailureCluster, ...]:
    """Only clusters with enough repeated evidence (§16)."""
    return tuple(c for c in clusters if c.is_candidate_evidence)


__all__ = ["ClusterKey", "FailureCluster", "candidate_clusters", "cluster_failures"]
