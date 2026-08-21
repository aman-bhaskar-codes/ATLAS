"""AdaptationEngine — orchestrates the learning lifecycle (Prompt 4 §15).

observe → diagnose → generate hypothesis → create experiment → execute →
compare → generalize → apply promotion policy → promote/reject → rollback.

It orchestrates the lifecycle; it does not implement every subsystem itself.
And per §48: when no hypothesis passes the evidence thresholds, the cycle
does NOTHING — no fake activity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas.adaptation.clustering import FailureCluster, candidate_clusters, cluster_failures
from atlas.adaptation.domain import (
    Hypothesis,
    HypothesisStatus,
    LearningState,
    PromotionDecision,
)
from atlas.adaptation.experiments import ArmRunner, ExperimentEngine
from atlas.adaptation.failure_analyzer import FailureAnalyzer
from atlas.adaptation.hypotheses import HypothesisGenerator, HypothesisStore
from atlas.adaptation.promotion import PromotionManager
from atlas.adaptation.store import AdaptationStore
from atlas.adaptation.taxonomy import FailureTaxonomy
from atlas.infra.logging import get_logger
from atlas.memory.trajectory import DecisionTrace, FailureRecord, Trajectory

_log = get_logger("atlas.adaptation.engine")


@dataclass
class CycleReport:
    """What one learning cycle actually did — empty means no fake activity."""

    trajectories_analyzed: int = 0
    clusters_found: int = 0
    candidate_clusters: int = 0
    hypotheses_proposed: tuple[str, ...] = ()
    experiments_run: tuple[str, ...] = ()
    decisions: tuple[PromotionDecision, ...] = ()
    state: LearningState = LearningState.IDLE
    notes: list[str] = field(default_factory=list)


class AdaptationEngine:
    def __init__(
        self,
        *,
        adaptation_store: AdaptationStore,
        hypothesis_store: HypothesisStore,
        experiment_engine: ExperimentEngine,
        promotion: PromotionManager,
        analyzer: FailureAnalyzer | None = None,
        generator: HypothesisGenerator | None = None,
    ) -> None:
        self._store = adaptation_store
        self._hypotheses = hypothesis_store
        self._experiments = experiment_engine
        self._promotion = promotion
        self._analyzer = analyzer or FailureAnalyzer()
        self._generator = generator or HypothesisGenerator()

    async def observe(
        self,
        trajectories: tuple[tuple[Trajectory, tuple[DecisionTrace, ...], tuple[FailureRecord, ...]], ...],
    ) -> tuple[FailureCluster, ...]:
        """Observe + diagnose: classify failures, analyze root causes,
        cluster repeated patterns. Returns candidate clusters."""
        await self._set_state(LearningState.OBSERVING)
        classified: list[FailureTaxonomy] = []
        for trajectory, traces, records in trajectories:
            if trajectory.success and not records:
                continue
            # §7: symptom vs root cause — analysis stored for the record.
            analysis = self._analyzer.analyze(trajectory, traces, records)
            await self._store.save_analysis(analysis)
            taxonomy = FailureTaxonomy.create(
                trajectory.id,
                analysis.primary_cause,
                root_cause_candidate=True,
                evidence=analysis.evidence[:4],
                recoverable=analysis.avoidable,
            )
            await self._store.save_failure(taxonomy)
            classified.append(taxonomy)

        await self._set_state(LearningState.DIAGNOSING)
        clusters = cluster_failures(tuple(classified))
        candidates = candidate_clusters(clusters)
        _log.info(
            "engine.observed",
            event_type="adaptation",
            trajectories=len(trajectories),
            classified=len(classified),
            clusters=len(clusters),
            candidates=len(candidates),
        )
        return candidates

    async def hypothesize(self, clusters: tuple[FailureCluster, ...]) -> tuple[Hypothesis, ...]:
        """§16: hypotheses only from repeated evidence; deduplicated per
        component. No clusters over threshold → nothing proposed (§48)."""
        await self._set_state(LearningState.HYPOTHESIZING)
        proposed: list[Hypothesis] = []
        for cluster in clusters:
            hypothesis = self._generator.from_failure_cluster(cluster)
            if hypothesis is None:
                continue
            if await self._hypotheses.exists_for_component(hypothesis.affected_component):
                continue
            await self._hypotheses.save(hypothesis)
            await self._store.record_event("hypothesis_proposed", hypothesis.hypothesis_id, {"title": hypothesis.title})
            proposed.append(hypothesis)
        return tuple(proposed)

    async def run_cycle(
        self,
        trajectories: tuple[tuple[Trajectory, tuple[DecisionTrace, ...], tuple[FailureRecord, ...]], ...],
        *,
        runner: ArmRunner | None = None,
        dataset_version: str = "golden_v1",
        pipeline_version: str = "pipeline_v1",
        atlas_version: str = "0.1.0",
    ) -> CycleReport:
        """One full background learning cycle. When nothing passes the
        evidence thresholds the report is empty — that is correct behavior."""
        report = CycleReport(trajectories_analyzed=len(trajectories))
        clusters = await self.observe(trajectories)
        report.clusters_found = len(clusters)
        report.candidate_clusters = len(clusters)

        proposed = await self.hypothesize(clusters)
        report.hypotheses_proposed = tuple(h.hypothesis_id for h in proposed)
        if not proposed:
            report.notes.append("no hypothesis passed evidence thresholds — cycle idle (§48)")
            await self._set_state(LearningState.IDLE)
            report.state = LearningState.IDLE
            return report

        if runner is None:
            report.notes.append("hypotheses queued — no experiment runner available")
            for hypothesis in proposed:
                await self._hypotheses.set_status(hypothesis.hypothesis_id, HypothesisStatus.QUEUED)
            report.state = LearningState.HYPOTHESIZING
            return report

        await self._set_state(LearningState.EXPERIMENTING)
        for hypothesis in proposed:
            experiment = await self._experiments.create(
                hypothesis,
                dataset_version=dataset_version,
                pipeline_version=pipeline_version,
                atlas_version=atlas_version,
            )
            await self._hypotheses.set_status(
                hypothesis.hypothesis_id, HypothesisStatus.RUNNING, experiment_id=experiment.experiment_id
            )
            completed = await self._experiments.run(experiment, runner)
            report.experiments_run += (completed.experiment_id,)

            await self._set_state(LearningState.EVALUATING)
            comparisons = await self._experiments.comparisons_for(completed.experiment_id)
            decision = await self._promotion.decide(completed, comparisons)
            report.decisions += (decision,)
        report.state = LearningState.EVALUATING
        return report

    async def _set_state(self, state: LearningState) -> None:
        await self._store.record_event("learning_state", "", {"state": state.value})


__all__ = ["AdaptationEngine", "CycleReport"]
