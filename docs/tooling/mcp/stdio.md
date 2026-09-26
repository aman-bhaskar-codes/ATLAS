# stdio Transport (§11-§16)

* The server is spawned from STRUCTURED argv (`StdioServerParameters`) — ATLAS
  never builds a shell string and never uses shell=True (§11/§12).
* `StdioCommandPolicy` validates: command blocklist (metacharacters), explicit
  executable allowlist, arg count/size caps, NUL checks (§12).
* `EnvironmentPolicy` builds an ALLOWLISTED child env: explicitly configured
  values (credential-resolved) + a minimal safe set (PATH/HOME/LANG/TMPDIR/
  SHELL) — the full ATLAS environment is never passed (§13).
* stdout carries protocol only; stderr is captured separately (§14).
* Lifecycle: pid/start/exit tracked; unexpected exit → degraded + tools
  invalidated (availability, not deletion) → reconnect per policy (§15).
* Restart protection: max_restarts within a sliding window with exponential
  backoff; circuit opens instead of endless respawn (§16, tested).
