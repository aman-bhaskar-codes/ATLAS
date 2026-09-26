# Streamable HTTP Transport (§17-§21)

Primary HTTP transport via the SDK's `streamable_http_client`. ATLAS controls
the URL, endpoint policy, and timeouts at the adapter layer. Separate
timeouts: connect/discovery (`timeout_s`, §77) vs tool-call
(`tool_call_timeout_s`, §18 — calls stream longer than connects).
Redirect policy and TLS remain SDK/HTTPX concerns; ATLAS preserves the
origin-security property (§19).
