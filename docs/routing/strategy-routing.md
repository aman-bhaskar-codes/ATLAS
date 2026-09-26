# Strategy Routing (L2)

`StrategyDefinition`s describe execution shape (single/sequence/parallel/dag/iterative/
recovery/human), parallelism and replanning support, and optional static templates.
Registered today: DIRECT, SINGLE_AGENT, SEQUENTIAL, PARALLEL, DAG, RESEARCH,
SOFTWARE_ENGINEERING, ITERATIVE, RECOVERY, HUMAN_REVIEW. DELEGATE/HANDOFF are
deliberately absent — no runtime exists (§88 principle). Selection is deterministic:
complexity + the domain's supported list, domain-primary strategies preferred for
non-trivial tasks.
