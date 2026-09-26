# MCP Runtime — Architecture (Part 5)

> Describes **implemented** behavior only.

## ADR: official SDK, ATLAS-owned boundary

ATLAS uses the official `mcp` Python SDK (`mcp>=2.2.0,<3`, resolved 2.2.0) for ALL
protocol work — JSON-RPC framing, initialize negotiation, tools/list, tools/call,
Streamable HTTP, SSE-legacy, cancellation. ATLAS does NOT reimplement protocol
code (§3). The SDK is isolated inside `src/atlas/tooling/mcp/` (§4/§131): every
other subsystem depends on `MCPServerManager` and ATLAS models
(`UniversalToolDefinition` / `UniversalToolResult`), never on SDK types.

## Diagram (actual)

```
                     ATLAS
                       │
                MCPServerManager        (tooling/mcp/manager.py)
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
        stdio (§11-§16)    Streamable HTTP (§17-§21)   [SSE legacy (§10)]
            └──────────┬──────────┘
                official SDK ClientSession
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     tools/list   resources      prompts          (§32/§45/§46)
        │
   normalization (tooling/mcp/normalization.py)
        │  mcp:<server_id>:<tool> (§35)
        ▼
   UniversalToolDefinition
        │
   ToolingRegistry + ToolCatalog (Part 2 sync, §41)
        │
   RoutingEngine (Part 3 — no MCP special-casing, §73)
        │
   ExecutionEngine (Part 4 governed funnel, §74)
        │
   SafetyEngine (§53 — MCP never bypasses)
        │
   MCPToolAdapter → tools/call → UniversalToolResult
        │
   trajectory · provenance (§59) · events (§71)
```

## Modules

| File | Responsibility |
|---|---|
| `models.py` | `MCPServerDefinition` (stdio/http variants validated), `ConnectionState` machine (§9), negotiated `MCPServerInfo`, descriptors |
| `security.py` | `StdioCommandPolicy` (no shell, argv-only, allowlist), `EnvironmentPolicy` (allowlisted child env), `EndpointPolicy` (SSRF guard) |
| `connection.py` | one server's background session task; state machine; bounded reconnect; paginated discovery; tools/call with separate timeouts |
| `manager.py` | definitions, lifecycle, dynamic refresh (debounced §42), registry+catalog sync, status/call API |
| `catalog.py` | `MCPToolAdapter` (Part-1 governed path) + catalog source |
| `normalization.py` | SDK Tool → UniversalToolDefinition; CallToolResult → UniversalToolResult (content blocks, provenance §59) |
| `config.py` | `config/mcp.yaml` loading via the existing loader (§7) |
| `events.py` | `mcp.*` events on the existing bus (§71) |

Trust levels (§29): `local_user_configured` / `remote_user_configured` /
`trusted_remote` / `untrusted` / `disabled` — they inform policy metadata and
never replace the SafetyEngine (§94).
