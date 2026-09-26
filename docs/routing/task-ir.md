# TaskIR

`TaskIR` (`routing/models.py`) is the normalized task description every layer consumes:
`task_id`, `correlation_id`, `objective`, `source` (api/cli/scheduler/voice/system —
preserved from the ingress, §12), complexity/urgency/risk priors, and the policy fields
(`privacy_class`, `network_policy`, `cost_policy`) which come from CONFIG — a request can
never widen its own boundaries. Only fields the router consumes exist; `TaskNormalizer`
(`normalize.py`) builds it deterministically, including the complexity prior and the
§46 fast-path rules.
