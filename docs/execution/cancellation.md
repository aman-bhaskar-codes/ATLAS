# Cancellation (§45-§47/§75)

Cancellation is cooperative and propagates: task → run token → step watcher →
the in-flight awaited work (the adapter task is `cancel()`ed, not abandoned) →
backend. The token is checked at every step boundary and inside the
slot-acquired execution path; a cancelled run starts no new work (§118-tested).
Timeouts are enforced per step (`default_step_timeout_s` / step override); a
timed-out step's work is cancelled too (§43/§46), and timeout recovery follows
the standard classify → retry-if-safe → fallback → replan → human/terminate
ladder — never an automatic retry when side effects are uncertain (§44).
