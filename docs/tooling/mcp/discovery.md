# Discovery (§32-§44/§104-§107)

`tools/list` runs through the official client WITH pagination handling (§32-§33:
cursor loop with repeating-cursor anomaly detection). Each tool is normalized to
`UniversalToolDefinition` with stable id `mcp:<server_id>:<tool>` (§35), exact
input schema persisted (§38), output schema when present (§39), and annotations
preserved as UNTRUSTED metadata (§37/§94). Duplicate names within one server are
recorded as anomalies — first kept, never silently merged (§85); across servers
the ATLAS id disambiguates (§84). Discovery failure retains the last known
catalog and marks DEGRADED (§43); connected-but-discovery-failed is explicit
(§44). Resources and prompts are discovered/c cataloged as SEPARATE entity
types when the server advertises them (§45-§46/§83).
