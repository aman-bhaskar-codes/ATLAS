# CURRENT LEARNING GAPS (Prompt 4 Phase 0 audit)

The single document that drives the Prompt 4 build list. Every gap maps to the
spec phase that closes it.

## Gap register

| # | Gap | Evidence | Closes via |
|---|---|---|---|
| G1 | No hypothesis lifecycle | no `hypotheses` table/model anywhere | §16-17 HypothesisGenerator + model |
| G2 | No experiment engine beyond RAG | only `evaluation/rag_experiments.run_experiment` | §19-22 ExperimentEngine + statistics |
| G3 | No promotion policy / states / rollback | `StrategyStore.record_outcome` activates in place | §23-24, §73 PromotionPolicy + version store |
| G4 | No counterfactual analysis or replay | nothing replays decisions | §27-30 CounterfactualEngine + ReplayEnvironment |
| G5 | No cognitive telemetry / confidence calibration | trajectory stores plan_confidence only | §32-34 CognitiveTelemetry + calibration |
| G6 | Model/tool routing is static | `ModelSelector` capability-only; `ToolHealthTracker` health-only | §35-37, §57, §69 evidence-driven routing |
| G7 | No generalization gate | RegressionGate compares same dataset only | §38-39, §94-95 generalization matrix |
| G8 | No failure root-cause analysis | flat `FailureCategory` on last error | §6-8 taxonomy + FailureAnalyzer + clustering |
| G9 | Experience validation thresholds loose | one extraction can become a skill after 3 uses | §10 evidence thresholds + validation |
| G10 | Human feedback not connected to learning | `FeedbackStore` isolated | §61-62 feedback → hypothesis evidence |
| G11 | No learning budget / resource governor | scheduler jobs unbounded | §47, §83, §86 LearningBudget + governor |
| G12 | No adaptation audit trail / negative knowledge | rejections not stored | §74, §88-89 adaptation memory |
| G13 | No shadow/canary deployment | activation is instant | §25-26, §71-72 shadow + canary |
| G14 | Safety-immutability not enforced in learning path | nothing blocks adaptation touching safety | §18, §98, §122 forbidden-target guard |
| G15 | No learning API/CLI/UI | routes_learning covers skills only | §75-79, §81, §127-130 |
| G16 | Trajectories lack reproducibility fields | no git_commit/config_hash/strategy_version | §2 trajectory extension |
| G17 | No adaptation curve / efficiency metrics | benchmarks cover CPU only | §50-51, §110-111 curve + efficiency |
| G18 | No retention policy for learning data | tables grow unbounded | §125 retention |
| G19 | RAG telemetry not feeding hypotheses | fabric telemetry written, never read by learning | §55, §105 fabric feedback |
| G20 | Evaluator independence not enforced | LLMJudge may equal generator model | §139 independence rule |

## Non-goals confirmed by audit (spec DO-NOTs)
- No isolated SelfImprovementAgent (§ architecture rule) — control plane only.
- No autonomous modification of SafetyEngine/permissions/credentials/audit
  (§18, §98) — enforced by a forbidden-target guard, tested (§122).
- No training/experiments on the hot path (§45, §82) — all background.
- No direct OpenRouter calls from learning components (§84) — ModelGateway only.
- No fake activity: if no hypothesis passes evidence thresholds, the learning
  cycle does nothing (§48).

## Build order chosen
1. Domain models (§1-3) → 2. taxonomy/analyzer/clustering (§6-8) →
3. evaluation hierarchy (§4-5) → 4. experience/skill/strategy versioning
(§9-14) → 5. hypothesis+experiment+promotion engine (§15-24) →
6. shadow/canary/counterfactual (§25-31) → 7. telemetry/calibration/routing
(§32-37) → 8. generalization/adversarial (§38-44) → 9. scheduler/budget/events
(§45-53) → 10. feedback loops + rollback + audit (§54-74) → 11. API/CLI/UI
(§75-81, §127-136) → 12. E2E demonstrations (§143-147) → docs (§142) → gates
(§148-150).
