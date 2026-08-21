# CURRENT EVALUATION STATE (Prompt 4 Phase 0 audit)

## Existing evaluation stack

### 1. Golden evaluation (`evaluation/service.py`, `evaluators.py`, `golden.py`)
- `GoldenTask` suite loaded from `eval/golden_tasks/`.
- `DeterministicEvaluator` — criteria checks (contains/exact/regex style).
- `LLMJudge` — judges through `ModelGateway` (never direct provider calls).
- `EvaluationService.run_suite` → `RunReport` with **regression detection**
  (baseline read before insert; previously-passing task now failing = regression).
- Persisted in `evaluation_results` table; CI entry via `scripts/eval_gate.py`.

### 2. Ragas foundation (Prompt 3: `evaluation/rag_metrics.py`, `rag_experiments.py`, `rag_datasets.py`)
- Deterministic, dependency-free metrics named after Ragas:
  `faithfulness`, `answer_relevancy`, `context_precision`, `context_recall`.
- `run_experiment(query_fn, dataset, variant)` → `ExperimentResult`
  (answered_rate + metric means).
- `RegressionGate.check(baseline, candidate)` → ship/block.
- `EvalDataset`/`EvalEntry` + JSONL loader + builtin smoke dataset.

### 3. Verification (`orchestration/verification.py`)
- `GroundingVerifier` — in-loop evidence-based goal verification (level 2
  programmatic verification already exists).

### 4. Knowledge telemetry (`knowledge/telemetry.py`)
- `RagTelemetry` records per-query records (answered, degraded, confidence,
  steps) into SQLite — already a learning-evidence source.

## Coverage against the Prompt 4 six-level hierarchy

| Level | Spec | Current |
|---|---|---|
| 1 Deterministic checks | required | ✅ `DeterministicEvaluator`, deterministic Ragas proxies |
| 2 Programmatic verification | required | ✅ `GroundingVerifier`, regression gate |
| 3 Domain-specific evaluator | required | ⚠️ partial: RAG evaluators exist; coding/browser/computer-use evaluators do not |
| 4 Ragas for knowledge | required | ✅ Prompt 3 metrics + experiments |
| 5 LLM judge via ModelGateway | required | ✅ `LLMJudge(gateway)`; must stay gateway-only (§84) |
| 6 Human feedback | required | ⚠️ `FeedbackStore` records ±1 but is not wired into evaluation decisions |

## Evaluation data storage
- `evaluation_results` (golden runs), `rag_evaluations`-style records inside
  fabric telemetry, `benchmarks/report.json` (CPU stage trends).

## Gaps Prompt 4 fills
1. Per-trajectory multi-dimension evaluation (goal/correctness/completeness/
   efficiency/... beyond binary success).
2. Evaluator selection discipline: deterministic first, LLM judge only when
   determinism cannot answer (§5).
3. Evaluation hierarchy registry keyed by task type (§5, §43).
4. Linking human feedback into evaluation evidence (§61).
5. Adversarial + long-horizon + generalization datasets (§40, §42, §38).
6. Statistical comparison machinery (§22) — currently only gate comparison.
