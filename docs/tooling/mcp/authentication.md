# Authentication (§22-§27/§68-§70)

`AuthConfig` supports `none` / `bearer` / `oauth` with a `credential_ref` into
ATLAS's IdentityPlatform vault — raw tokens NEVER appear in config, catalog,
logs, events, or exceptions (§24/§27). Bearer credentials are resolved from the
vault at connect time and injected as env/headers server-scoped (§23): a
credential for server A is never sent to server B (§111). On 401: refresh once
if policy allows, else AUTH_INVALID → auth recovery (§68). Auth events:
`mcp.auth.failed` (no secrets, §70). OAuth browser flow with PKCE/state via the
SDK's `OAuthClientProvider` + ATLAS `TokenStorage` adapter is the wired
boundary; issuer-bound registration per SDK v2 (§69).
