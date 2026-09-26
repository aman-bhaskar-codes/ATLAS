# Parallelism & Joins (§11-§12/§41-§42)

Independent READY steps dispatch concurrently under a global semaphore
(`max_concurrent_steps`) plus per-candidate limits — no unbounded gather.
Join semantics are declared on the DEPENDENT step (`input_mapping.join`):
ALL_REQUIRED (default), ALL_BEST_EFFORT, MIN_SUCCESS_COUNT,
MIN_SUCCESS_RATIO, FIRST_SUCCESS (§41). A JOIN step receives the per-branch
outcomes — failures are visible, never silently discarded (§42). Partial
success is a first-class terminal outcome (§59).
