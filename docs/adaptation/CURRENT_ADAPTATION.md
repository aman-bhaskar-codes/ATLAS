# CURRENT ADAPTATION STATE (Prompt 4 Phase 0 audit)

Audit of what adaptation/self-improvement machinery exists before building the
Prompt 4 control plane. Rule from spec: extend existing objects, do NOT create
an isolated SelfImprovementAgent.

## What exists today

| Component | Location | State |
|---|---|---|
| Trajectory recording | `memory/trajectory.py` + `memory/trajectory_store.py` | Real: frozen pydantic `Trajectory`/`DecisionTrace`/`FailureRecord`/`Experience`, SQLite store (736 lines) with queries, failure patterns, experience application tracking |
| Experience extraction | `memory/experience_extractor.py` (365 lines) | Real: LLM-assisted lesson extraction from trajectories |
| Skill library | `memory/skills.py` + `skills_promotion.py` | Real: `Skill` model with candidate→active→disabled lifecycle, evidence thresholds (≥3 applications, ≥0.7 success rate), versioning + supersession |
| Strategy memory | `memory/strategies.py` | Real but minimal: `Strategy(task_type_pattern, approach, model_preference, tool_preference)`; activation needs ≥3 evidence + eval_score ≥0.7. **No versioning, no performance dimensions** |
| Evaluation service | `evaluation/service.py` | Real: golden suite runs, regression detection, SQLite persistence |
| Ragas foundation | `evaluation/rag_metrics.py`, `rag_experiments.py` | Real (Prompt 3): deterministic faithfulness/answer_relevancy/context_precision/context_recall + `run_experiment` + `RegressionGate` |
| Feedback | `infra/feedback.py` | Real but minimal: thumbs ±1, comment, edited output; **not linked to decisions/strategies** |
| Scheduler | `infra/scheduler.py` `CronScheduler.register_job` | Real: in-process cron jobs (e.g. memory_consolidation @ 02:00, registered in `app.py`) |
| EventBus | `infra/bus.py` | Real: topic pub/sub + global handlers |
| Memory consolidation | `memory/consolidation.py` | Real: episodic → semantic promotion with proposals |
| World state | `memory/world_state.py` | Real: entity/attribute store |
| Learning API | `interfaces/api/routes_learning.py` | Real: skills/strategies/world/evaluation/analytics/consolidate/promote |
| Safety engine | `safety/engine.py` | Real, human-controlled; adaptation must never touch it |
| Model routing | `intelligence/selection/selector.py` + `router.py` | Real but static (capability-based); no learned per-task-class preferences |
| Tool routing | `orchestration/tool_routing.py` `ToolHealthTracker` | Real: health-based ranking; no task-class success learning |
| Knowledge fabric | `knowledge/*` (Prompt 3) | Real: telemetry records query outcomes; adapter registry for trained artifacts |

## What does NOT exist (Prompt 4 must build)

- FailureAnalyzer (symptom vs root cause) — only flat `FailureRecord.category`
- Failure clustering beyond `get_failure_patterns` string grouping
- Hypothesis model + generator (no `hypotheses` anywhere)
- Experiment engine (baseline vs candidate on datasets; only RAG-scoped
  `run_experiment` exists in `evaluation/rag_experiments.py`)
- Statistical comparison (mean/median/variance/effect size, paired eval)
- PromotionPolicy with safety-regression hard rule
- Strategy versioning / promotion states / rollback
- Shadow mode + canary rollout
- Counterfactual engine + ReplayEnvironment + DecisionPreference
- Cognitive telemetry + confidence calibration
- Adaptive model/tool routing (evidence-driven, exploration-bounded)
- Generalization gate / adversarial evaluation / synthetic variants
- LearningBudget / ResourceGovernor
- Adaptation curve / learning efficiency tracking
- `/api/v1/adaptation/*` endpoints and `atlas learn` CLI
- Frontend Learning Lab (`/lab/*`)

## Integration points chosen (no isolated agent)

- New package `src/atlas/adaptation/` sits as a CONTROL PLANE:
  reads `TrajectoryStore`, `FeedbackStore`, `RagTelemetry`; writes strategies
  through extended `StrategyStore`; schedules via existing `CronScheduler`;
  emits via existing `EventBus`; judges only through `ModelGateway`.
- importlinter layers contract gains `atlas.adaptation` between
  `atlas.diagnostics` and `atlas.evaluation` (control plane may import
  evaluation/orchestration/knowledge/memory/intelligence; nothing below it
  imports it — it observes the runtime, the runtime never waits on it).
- DB migrations continue from `008_*` (latest is `007_idempotency_keys.sql`).
