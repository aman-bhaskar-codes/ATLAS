# Troubleshooting

| Symptom | Meaning | Action |
|---|---|---|
| `CONNECTING` stuck → timeout | server not starting / wrong command | check `command`/`args`; run it manually; `atlas mcp status <id>` shows last_failure |
| `mcp.discovery.failed` retained old tools | discovery failed, catalog kept (§43) | fix server; `atlas mcp refresh <id>` |
| `mcp.auth.failed` | missing/expired credential | put the credential in the vault under the configured `credential_ref` |
| tools missing after connect | server advertised none, or duplicates recorded | `atlas mcp tools <id>`; check server logs (stderr) |
| `FAILED` after repeated crashes | restart circuit open (§16) | fix the server; reconnect re-arms |
| tool denied before call | manifest seat `mcp` tier CONFIRM (§94) | owner raises the tier in permissions.yaml deliberately |
