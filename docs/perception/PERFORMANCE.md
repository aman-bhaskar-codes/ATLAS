# Performance budget — perception must stay cheap

The perceive→act→verify loop perceives at least TWICE per action (before and
after). Perception therefore has hard budgets, not hopes.

## 1. Bounded snapshots (never walk the whole world)

| Channel | Bound | Where |
| --- | --- | --- |
| browser AX slice | 200 elements | `_flatten_ax(limit=200)` in `adapters/browser.py` |
| browser visible elements | interactables only | `build_state` selector list |
| Android dump | flat node list, capped parse | `parse_uiautomator_dump` |
| macOS AX walk | depth-limited backend walk | `perception/macos_ax.py` |
| API payloads | depth ≤ 6, lists ≤ 25, strings ≤ 2000 | `public_api/normalization.py` |

Model context gets even less: `snapshot.summarize(limit=40)` for tool output,
`limit=25` for post-action summaries.

## 2. Perception cache (TTL + LRU)

`PerceptionCache` (`computer_use/cache.py`):

- key = `(substrate, surface)` — one entry per surface (URL, app, device)
- TTL 5s, max 16 entries (LRU eviction)
- ANY mutating operation invalidates first (`MUTATING_OPERATIONS`)
- the engine NEVER acts on stale perception for consequential actions

Consecutive read-only `perceive` calls within the TTL are free.

## 3. Cheap change detection

- `dom_hash` — truncated SHA-256 of body HTML; `STATE_CHANGED` expectations
  compare hashes, not full DOMs
- Android/macOS snapshots compare element counts + state dicts

## 4. Screenshots are opt-in

`visual_evidence()` is separate from `snapshot()`; nothing captures pixels on
the hot path. Redaction is string-shape based — no model calls.

## 5. One bounded probe for API validation

Connector validation performs exactly ONE GET (HTTPS, keyless) — validation
cost is constant per API, never a crawl.

## 6. Telemetry (self-observing loop)

`ComputerUseTelemetry` (`computer_use/telemetry.py`):

- ring buffer (`maxlen=512`) of `ComputerUseRecord`s: substrate, operation,
  latency_ms, ok, confidence, detail
- `timed(substrate, operation)` context manager wraps perceive/resolve/act
- `summary()` → counts, ok-rate, **p95 latency** per operation
- `bump_recovery()` counts resolution failures (degraded-strategy recoveries)

Use `engine.telemetry.summary()` to spot substrate regressions — a p95 spike
on `perceive` usually means a page/app got noisier or the cache stopped
hitting.

## Budgets to defend in review

- `perceive` (cached hit): < 1 ms
- `perceive` (cold, local app/page): hundreds of ms, not seconds
- `act` overhead beyond the primitive itself: one forced re-perceive
- tool output size: bounded by summarize limits, independent of page size

If an adapter cannot meet these, bound its walk further — do not slow the
loop.
