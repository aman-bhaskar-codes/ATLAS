# Connection Lifecycle (§9/§63-§67/§122-§125)

States: CONFIGURED → VALIDATING → CONNECTING → (AUTHENTICATING) → NEGOTIATING →
READY → {DEGRADED → READY | RECONNECTING} → DISCONNECTING → DISCONNECTED; FAILED
from most states; DISABLED as an operator gate. Illegal transitions raise
(§9). Lazy servers connect on first use (`ensure_ready`, §63/§65); eager servers
connect at startup with discovery before READY (§64). Reconnect on loss:
degraded → bounded backoff → re-negotiate → re-discover → catalog diff — the
tool list is NEVER assumed unchanged (§67). Shutdown: stop refreshers → close
sessions → terminate children (via the SDK contexts) → no orphans (§66).
Disconnect keeps config/history/credentials; remove drops the runtime
registration while retaining audit history (§123-§125).
