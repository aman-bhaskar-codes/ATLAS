# Local Mode (`local_free`)

The default development profile. Everything runs on one machine, $0, no API
keys, works with the network disconnected.

## What runs locally

- **LLM**: Ollama (`config/models.yaml`, `cost_class: local`). Verify with
  `atlas models doctor`; pull with `atlas models pull` (or `ollama pull qwen3:4b`).
- **State**: SQLite (`atlas.infra.db`) — tasks, events, schedules, memory,
  trajectory, workflow templates.
- **Vector store**: local Chroma (`.atlas/chroma`) with local embeddings.
- **Bus/queue**: SQLite-backed `MessageBus` (`infra/bus.py`) — durable event
  queue + replay log.
- **Scheduler**: in-process `CronScheduler` (`infra/scheduler.py`).
- **Browser**: local headless Playwright.
- **Sandbox**: Docker sandbox when available; native sandbox fallback.
- **Observability**: structured logs + local metrics/tracing. No paid service.
- **Notifications**: terminal/desktop/ntfy.

## Enabling

```bash
ATLAS_PROFILE=local_free          # or set profile: local_free in config/settings.yaml
ATLAS_COST_POLICY=zero_cost       # hard-blocks paid providers even if keys exist
ATLAS_NETWORK_POLICY=offline      # blocks all network egress
```

## Offline guarantee

With `network_policy: offline`, provider selection only admits local models
(`intelligence/selection/selector.py` enforces this before any request is
made). Tests run offline via `ATLAS_OFFLINE=true` and provider simulators —
no test depends on a real cloud API.
