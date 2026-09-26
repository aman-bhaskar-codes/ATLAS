# Tool Catalog — Part 2

> Describes **implemented** behavior only. Deferred items are listed at the end.

## Registry vs Catalog — the core distinction

```
             LIVE RUNTIME                    DISCOVERY LAYER
      ┌────────────────────┐          ┌──────────────────────────┐
      │  ToolingRegistry   │          │       ToolCatalog        │
      │ "what can execute  │   sync   │ "what is ATLAS aware of, │
      │  in this process   │ ───────▶ │  schemas, sources, last  │
      │  right now?"       │          │  seen, catalog state?"   │
      └────────────────────┘          └──────────────────────────┘
                                          │
                                ┌─────────┼──────────┐
                                ▼         ▼          ▼
                            structured  lexical   inspect
                              find      search    (tool/operation)
                                │         │
                                └────┬────┘
                                     ▼
                        find_candidates() → PART 3 ROUTER
```

The registry stays the authority on executability. The catalog is durable,
versioned, searchable, source-aware, operation-aware, and survives restarts.
It is NOT: a second runtime registry, an MCP-specific subsystem, an embedding
store, a safety engine, or a credential store.

## Persistence

SQLite via the existing migration system (migration #30 in `infra/db.py`),
normalized hierarchy (§16):

```
tool_sources → tool_namespaces → tool_definitions → tool_operations
```

plus `tool_sync_runs` (one row per source refresh, with full counts) and
`catalog_state` (holds the monotonic `catalog_version`). JSON columns store
only the JSON Schemas that ARE data (§17). **Secrets are never persisted** —
credential references only (§7), enforced by test.

## Fingerprints

`catalog/fingerprints.py` — canonical JSON (recursively sorted object keys,
array order preserved because JSON-Schema arrays are order-semantic) → SHA-256.

* `schema_fingerprint` — per schema (input, output, per-operation)
* `definition_fp` — over the semantic surface (id, version, description,
  operations, schemas, execution type, safety seat). Deliberately EXCLUDES
  runtime state, so metadata changes are detectable independently.

Key-order-identical schemas hash identically (tested); a changed description,
operation set, or schema yields a different fingerprint and a
`definition_version` bump.

## Sync (registry → catalog, §20/§36-§37/§56)

```
CatalogSource.discover() → UniversalToolDefinition[]
  ↓ validate (malformed → structured rejection, others continue, §48)
  ↓ normalize (search_text; original description preserved, §46/§75)
  ↓ fingerprint + diff vs persisted snapshot for that source (§52)
  ↓ ONE transaction: upsert source/namespace/tools, replace operations,
  │  mark absent-after-success STALE, record tool_sync_runs (§23)
  ↓ commit → bump catalog_version ONLY if something visibly changed (§33)
  ↓ publish tool.catalog.* events (never before commit, §56)
  ↓ rebuild in-memory index → atomic snapshot swap (§55)
```

Lifecycle rules:

* **Failed discovery** records the failure and changes nothing — absence
  after a failed refresh is NOT evidence of removal (§50/§51).
* **Absent after a SUCCESSFUL authoritative refresh** → `STALE`
  (availability `UNAVAILABLE`); never auto-deleted. `REMOVED` is an explicit
  operator action.
* **Runtime-state overlay**: registry-backed sources report runtime status;
  catalog state maps REGISTERED→DISCOVERED, READY→READY, DISABLED→DISABLED,
  FAILED→UNAVAILABLE. Catalog existence ≠ runtime executability (§18); auth
  state and availability are separate axes (§43/§44).
* **Per-source isolation** (§22): sources sync sequentially on the shared
  SQLite connection with per-source try/except — one failure logs and moves
  on; healthy sources land.

Events: topic `tool.catalog` (typed `ToolCatalogEvent`, registered in
`bootstrap/infrastructure.py`), kinds `sync.completed` / `sync.failed` /
`tool.added` / `tool.updated` / `tool.stale`, with source/tool/fingerprints/
version/reason — never credentials (§34/§35).

## Indexing & search

All queries are served from an immutable `CatalogIndex` built from SQLite and
swapped atomically after each sync (§53-§55). No per-query SQL, no embeddings
yet — Tool-RAG is Part 6.

* **Structured** `find()` / `find_candidates()`: exact filters on capability,
  operation, namespace, source, execution type, status, enabled, tag.
  `find_candidates()` is the Part-3 seam — deterministic, tool_id-ordered,
  default status READY, NO ranking (§71/§72).
* **Lexical** `search()`: deterministic field-weighted token overlap
  (name 0.35, operation 0.25, capability 0.15, tag 0.15, description 0.10,
  exact-name bonus 0.30), ties broken by tool_id, every hit explains itself
  with `matched_fields` + `score` (§26/§27). `CatalogMatch` already carries
  `semantic_score`/`embedding_score`/`rerank_score` extension fields for
  Part 6 (§28) — always `None` today.

## Inspection & surfaces

* `inspect(tool_id)` → full record; `inspect_operation(tool_id, op)` → one
  operation with schemas + UNTRUSTED MCP-style annotations (§31/§45) — hints
  never authorize, proven by test that a read-only-hinted tool is still
  SafetyEngine-denied.
* `list_namespaces()` / `search_namespaces()` / namespace health+auth
  summaries (§29).
* `to_tool_specs(shortlist)` → provider-native `ToolCallSpec` for a SELECTED
  shortlist only — the future model-facing tool surface (§73), with per-tool
  `schema_bytes` / `estimated_schema_tokens` budgeting (§74).
* `record.to_definition()` reconstructs the Part-1 `UniversalToolDefinition`
  — the router's contract bridge.
* API: `GET /tools/catalog`, `/tools/catalog/search`, `/tools/catalog/namespaces`,
  `/tools/catalog/candidates`, `/tools/catalog/tools/{id}/inspect`,
  `/tools/catalog/tools/{id}/operations/{op}/inspect`, `POST /tools/catalog/refresh`.
* CLI: `atlas tools catalog | search "<query>" | inspect <tool-id> | namespaces | refresh`.

## Integration in the composition root

`build_tooling(..., db, bus)` constructs the catalog; `ToolingFabric.catalog`
exposes it; `Atlas.start()` calls `fabric.sync_catalog()` **after**
`lifecycle.start()` + `bus.start()` (the database and bus must be live):
load persisted rows (`rebuild_index`) → reconcile with the live registry
(`refresh_all`). `GET /tools/catalog` and restart tests exercise the real path.

## Measured performance (§67/§84)

1,000 tools × 5 operations (5,000 operations), real sync engine + SQLite,
dev machine (Apple Silicon, WAL):

| Operation | Measured |
|---|---|
| Initial sync (validate+diff+commit+events) | ~480 ms |
| Index load from SQLite (`rebuild_index`) | ~130 ms |
| Exact lookup (×100) | <1 ms total |
| Structured query (×20) | ~4 ms total |
| Lexical search (×20) | ~60 ms total |
| Unchanged re-refresh (full diff) | ~210 ms |

CI guards assert order-of-magnitude regressions only — see
`tests/tooling/test_catalog_performance.py`.

## Deferred to Part 3+

Ranking/scoring of candidates (Part 3 router) · fallback selection · semantic
Tool-RAG + embeddings (Part 6) · MCP transport & discovery as a third source
(Part 4-5, plugs into `CatalogSource` with zero catalog changes) · deep
identity/credential-state integration (Part 7-8: auth_state is descriptive
`REQUIRED/NONE` today) · quota intelligence and health-based routing (Part 8).
