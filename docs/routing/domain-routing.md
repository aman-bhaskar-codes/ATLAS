# Domain Routing (L1)

Domains are declarations (`DomainDefinition`), never `if domain == ...` branches. The
registry ships **general** (always available), **research** (available when the knowledge
tool is live), and **agentic_ide** (available only when the ADE runtime is enabled) —
availability is injected at bootstrap and checked live, so the router knows what exists
without hallucinating capabilities. Future domains (§9) register the same way with zero
engine changes.

Cascade (§14/§45): deterministic fast-path rules (exact, small, tested) → judgment
provider question "Which domain best matches?" → low-confidence falls to the safe default
`general` (recorded in the reasons) or `ask_user` when no safe default applies (§64).
