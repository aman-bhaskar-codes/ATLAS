# MCP Configuration (§6-§8)

`config/mcp.yaml` — credential REFERENCES only, never secrets:

```yaml
mcp:
  servers:
    github:
      enabled: true          # owner-controlled (§28/§93)
      transport: stdio
      startup: lazy          # §63 lazy | §64 eager
      command: npx
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_PERSONAL_ACCESS_TOKEN:
          credential_ref: github:default   # resolved from the vault at connect
      trust_level: local_user_configured
      timeout_s: 30          # connect/discovery (§77)
      tool_call_timeout_s: 120
      reconnect: {max_restarts: 3, restart_window_s: 60, initial_backoff_s: 1.0, max_backoff_s: 30}
```

HTTP variant: `transport: streamable_http` + `url` + optional `auth: {mode: oauth,
credential_ref}` + `endpoint_policy` (§20). Loaded by `load_mcp_server_definitions`
using the existing config-loader conventions — no second loader (§7).
