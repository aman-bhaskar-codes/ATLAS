# CURRENT STRATEGIES & SKILLS STATE (Prompt 4 Phase 0 audit)

## Skills (`memory/skills.py`, `skills_promotion.py`)
- `Skill` frozen model: name, description, procedure_steps, version, status
  (candidate/active/disabled), success_rate, usage_count, confidence,
  preferred_tools, known_failure_modes, source_experience_ids, superseded_by.
- Evidence-gated promotion: ≥3 applications AND ≥0.7 success rate → active;
  active with ≥5 applications and <0.3 success rate → disabled.
- `new_version()` + `supersede()` already implement immutable versioning for skills.
- `to_prompt_fragment()` injects skills into planning context only (never safety).
- `SkillPromoter.promote_from_experiences` turns repeated experiences into
  candidate skills; exposed via `POST /api/v1/learning/promote`.

## Strategies (`memory/strategies.py`)
- `Strategy`: task_type_pattern (fnmatch), approach text, model_preference,
  tool_preference, status candidate/active/retired, success_rate,
  evidence_count, eval_score.
- Activation: ≥3 evidence AND ≥0.7 success rate AND eval_score ≥0.7 — i.e. a
  live-only strategy can never activate (offline eval evidence required).
- `active_for(task_type)` matches strategies by pattern; `record_outcome`
  updates running statistics.

## What is missing vs Prompt 4 §12-14

1. **No strategy versioning** — Strategy rows are updated in place
   (`INSERT OR REPLACE`); spec requires immutable `StrategyVersion` rows with
   change_reason + source_experiments.
2. **No rich performance model** — only success_rate; spec requires
   quality_score, latency, cost, recovery_rate, verification_rate,
   generalization, user_feedback.
3. **No promotion state machine** — no PROPOSED→TESTING→VALIDATED→CANDIDATE→
   PROMOTED or REJECTED/ROLLBACK.
4. **No rollback** — retiring a strategy does not restore a prior version.
5. **No policy layer** — nothing decides WHEN a strategy applies beyond the
   fnmatch pattern; no strategy-conflict handling (§96).
6. **No domain scoping** — promotion is global; spec §53 wants per-domain
   scoped promotion.

## Reuse decision
Extend `memory/strategies.py` with additive fields where safe and add the
versioned store + performance tracking in `adaptation/` (new tables), keeping
the existing `strategies` table as the live-selection surface so
`active_for(task_type)` consumers keep working unchanged.
