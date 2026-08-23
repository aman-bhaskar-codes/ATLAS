# CURRENT_EVALUATION — what already measures outcomes

**Method.** Read from the tree this pass. Files opened: `evaluation/service.py` (171 lines,
full), `evaluation/rag_metrics.py` (88 lines, full), plus the module listing and the backing
DDL in `infra/db.py`.

Spec §1 forbids creating a second evaluation system. This document establishes what the first
one does, so the engineering layer can call it.

---

## 1. Module inventory

`src/atlas/evaluation/`:

| File | Role |
|---|---|
| `evaluators.py` | `DeterministicEvaluator`, `LLMJudge`, `Evaluator` protocol, `EvalResult` |
| `golden.py` | `GoldenTask`, `load_golden_suite(path)` |
| `service.py` | `EvaluationStore`, `EvaluationService`, `RunReport`, `build_evaluation_service` |
| `rag_datasets.py` | RAG dataset loading |
| `rag_experiments.py` | RAG experiment runner |
| `rag_metrics.py` | Ragas-**style** metrics, ATLAS-native |

Backing tables: `evaluation_results`, `regression_results`, `comparison_results`,
`rag_records`, `retrieval_feedback`, `decision_quality`, `calibration_records`.

---

## 2. There is already a regression gate — ✅ and §62 should use it

`evaluation/service.py`:

```python
@dataclass
class RunReport:
    run_id: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    regressions: list[str] = field(default_factory=list)  # golden ids that previously passed
    results: dict[str, EvalResult] = field(default_factory=dict)

    @property
    def success_rate(self) -> float: ...
    @property
    def gate_passed(self) -> bool:
        return self.failed == 0 and not self.regressions
```

`run_suite` reads the baseline **before** inserting this run's result — the code carries the
comment explaining why (`"Baseline must be read BEFORE this run's result is inserted."`), and
`latest_run_passing` returns `None` when a task has never run, so "no baseline" is
distinguished from "was failing". A previously-passing task that now fails lands in
`regressions`, and `gate_passed` is false.

**This is §62's regression gate, already built and already correct.** The engineering layer's
repair pipeline should call `run_suite(...)` and read `gate_passed`, not implement its own
comparison. `regressions` is also the honest answer to §95 ("performance regressions become
evaluation candidates"): the concept of "this used to pass" is already persisted.

Two properties to preserve:

- **Strict LLM judging.** When `task.use_llm_judge` and a judge is configured, the result is
  `passed=result.passed and judged.passed` — the deterministic evaluator can veto the judge.
  The LLM cannot promote a failing answer. This is the same "the model proposes, ATLAS
  decides" rule that §31 applies to patches, already implemented here.
- **Answers arrive from the caller.** The docstring: "Answers arrive from the caller
  (recorded fixtures in CI, live agent runs behind the gated suite) so evaluation itself stays
  deterministic." Do not make `EvaluationService` call a model to obtain the answer it is
  grading.

Storage detail worth knowing before adding fields: `store.save` truncates `answer[:4000]` and
packs `criteria` / `failure_reason` / `judge_rationale` into a JSON `detail` column.

---

## 3. RAG metrics — ✅ deterministic, and honestly named

`evaluation/rag_metrics.py` implements `faithfulness`, `answer_relevancy`,
`context_precision`, `context_recall`, plus `evaluate_answer(...)` returning one metrics row
per answer.

The module docstring is explicit and matters for §49:

> "No LLM graders: every metric is token-overlap based, reproducible, free, and fast enough
> to run over the whole dataset on every experiment. The metric NAMES map to Ragas so results
> are comparable; the implementations are ours (§140: don't pretend to run Ragas itself)."

**Constraint this places on the new work.** §49 says "RAG observability incl. Ragas
regressions". ATLAS does **not** run Ragas. Any UI label, doc line, or incident description
must say *Ragas-style* / *Ragas-compatible metric names*, never *Ragas*. Writing "Ragas score"
in `/system/observability` would be exactly the fabricated-integration failure the truth
mandate forbids.

Mechanically, a RAG regression is well-defined today: these metrics are deterministic
functions of `(answer, query, contexts, claims, ground_truth)`, so the same dataset yields the
same numbers, and a drop is a real drop rather than grader noise. §108's RAG-regression E2E
test is therefore implementable without any new metric code.

---

## 4. Adaptation-side evaluation — ✅ extensive

Documented in `CURRENT_FAILURE_HANDLING.md` §4. Relevant here because §60–§63, §77–§80 and
§94–§96 all describe loops that already have their measurement half built:

`adaptation/experiments.py`, `shadow.py`, `canary.py`, `generalization.py`, `promotion.py`,
`comparison` (→ `comparison_results`), `decision_quality.py`, `calibration.py`,
`statistics.py`, `adversarial.py`, `replay.py`, `counterfactual.py`, `evaluation_dataset.py`,
`evaluators.py`, `routing.py`, `telemetry.py`.

Note `adaptation/statistics.py` exists — **check it before writing any new statistical helper
for §11.** §11 explicitly wants rolling baseline / moving average / percentiles / z-score /
robust deviation and warns against introducing a complex ML detector; if those primitives are
already in `adaptation/statistics.py`, §11 is a caller, not an implementation.

---

## 5. What is missing

| §  | Requirement | State |
|---|---|---|
| 35 | Generate a regression test that **fails before** and **passes after** the fix | ❌ No generator. `RunReport` can prove pass-after; nothing captures fail-before. |
| 96 | Bug → regression-benchmark pipeline | ❌ No path from a failure to a new `GoldenTask`. `load_golden_suite` reads from disk; nothing writes suites. |
| 97 | Self-expanding test suite | ❌ Same root cause: golden suites are authored, never grown. |
| 98 | Test-quality check — never accept a test merely because it passes | ❌ Absent, and the sharpest new requirement here. A generated test that asserts nothing passes trivially. The fail-before check *is* the quality check; without it, §35 and §97 are unsafe. |
| 105 | Multi-dimension `SystemScorecard`, not one collapsed score | ⚠️ The dimensions exist across tables (eval pass rate, RAG metrics, routing stats, decision quality, calibration, provider reliability); nothing assembles them, and nothing currently collapses them either — so the risk §105 warns about has not yet been introduced. |
| 9 | measured / estimated / heuristic provenance | ❌ `EvalResult` has `score` with no provenance flag. `rag_metrics` scores are *measured* (deterministic); `LLMJudge` scores are *estimated*; `chars ÷ 4` is *heuristic*. The data model does not say which. |
| 49 | RAG observability surface | ⚠️ Metrics and `rag_records` exist; no dashboard, no regression alert, no incident. |
| 76 | Evaluation centre UI | ❌ No route. |

---

## 6. Reuse map for the new layer

| Need (§) | Call this |
|---|---|
| Did the repair break anything? (§36, §62) | `EvaluationService.run_suite(...)` → `RunReport.gate_passed` |
| Was this previously passing? (§95) | `EvaluationStore.latest_run_passing(golden_id)` |
| Is retrieval quality down? (§49, §108) | `rag_metrics.evaluate_answer(...)` over `rag_records` |
| Roll out a repair gradually (§63) | `adaptation/canary.py` |
| Does the fix generalise? (§61) | `adaptation/generalization.py` |
| Promote or reject (§62) | `adaptation/promotion.py` → `promotion_decisions` |
| Rolling stats for anomaly detection (§11) | `adaptation/statistics.py` — check first |
| "What if we had chosen differently?" (§26) | `adaptation/counterfactual.py` |

Build new, in `atlas.engineering`: the fail-before capture (§35), the golden-suite **writer**
(§96/§97), the test-quality check (§98), the provenance flag (§9), and the scorecard
assembler that keeps dimensions separate (§105).
