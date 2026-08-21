# Public APIs — capability discovery funnel

ATLAS gains new *data* capabilities without new code: a bundled catalog of
public HTTP APIs, discovered by intent, validated by probe, executed under
the same safety/honesty rules as every other body.

Code: `src/atlas/capabilities/public_api/`

```
catalog.py        bundled catalog (data/catalog.json, 14 APIs)
connector.py      lifecycle registry (DISCOVERED → … → AVAILABLE)
retrieval.py      intent → ranked candidates
validation.py     HTTPS/keyless/bounded-GET probe
normalization.py  bounded, untrusted, provenanced results
platform.py       PublicAPIPlatform facade
```

## Lifecycle

```
DISCOVERED → CANDIDATE → EXPERIMENTAL → VALIDATED → AVAILABLE
                                        ↘ DISABLED
```

- Every API seeds as `DISCOVERED` — catalog presence is NOT capability.
- Only `VALIDATED` / `AVAILABLE` are executable (`_EXECUTABLE`).
- Promotion to an executable status REQUIRES an evidence note
  (`ValueError: requires validation evidence` otherwise) — status without
  proof cannot be fabricated.

## Discovery

`platform.discover(intent, limit=5)` ranks catalog entries by keyword overlap
with honest bonuses: free-first `+0.05`, no-key `+0.05`; validated connectors
outrank unvalidated ones. `"weather forecast"` surfaces Open-Meteo and
wttr.in; keyed/private APIs stay discoverable but report their limits via
`platform.explain_limitation(intent)`.

## Validation

`platform.validate(api_id, probe_path="")` runs ONE bounded probe:

1. HTTPS-only — plain HTTP is refused
2. keyless — APIs requiring credentials are not auto-validated
3. one GET request; `status < 500` counts as reachable

Successful validation promotes the connector with the probe recorded as
evidence; failed validation demotes it back to `DISCOVERED`. Nothing is
cached as "works" forever — validation is a claim tied to evidence.

## Execution

`platform.execute(api_id, params)` on a non-executable connector raises
`ConnectorNotExecutableError` — the message states
`DISCOVERED/UNVALIDATED — execution refused`. No network call happens.
This is acceptance scenario 6: an unknown/unvalidated API is honestly
refused, never half-attempted.

Successful execution returns normalized data:

- bounded: depth ≤ 6, lists ≤ 25 items, strings ≤ 2000 chars
- `trust="untrusted"` — web data never masquerades as first-party truth
- `Provenance(provider, source_kind=WEB, uri, retrieved_ts)` — every byte
  traceable to where it came from

## Testing

`tests/connectors/` covers catalog loading, lifecycle transitions (including
the evidence requirement), retrieval ranking, the validation probe against a
scripted fetcher, and the DISCOVERED-refusal E2E — all deterministic, no
network.

## Extending the catalog

Add an entry to `data/catalog.json` (id, name, base_url, endpoints,
keywords, auth, pricing). It appears at `DISCOVERED` on next load and must
earn `VALIDATED` through the probe like everything else.
