# Checkpointing & Resume (§19-§21/§68-§71)

Checkpoints are append-only, versioned (`checkpoint_schema_version = 1`) JSON
snapshots written at: run start, step completion/failure, fallback, replan,
human wait, pause, run finish. They carry the run state AND the plan, so a
fresh process can resume with zero in-memory state. Resume (§68): load latest
checkpoint → validate schema version (unknown versions are refused loudly) →
restore → continue; SUCCEEDED steps are never re-run (§70/§118-tested).
Crash between checkpoints = at-least-once semantics; see idempotency.md.
