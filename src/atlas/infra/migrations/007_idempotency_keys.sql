-- 007_idempotency_keys.sql

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_ts DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idempotency_keys_created_ts ON idempotency_keys(created_ts);
