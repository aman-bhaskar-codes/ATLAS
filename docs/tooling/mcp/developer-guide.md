# Developer Guide

* Add a server: edit `config/mcp.yaml` (credential refs only) → `atlas mcp
  connect <id>` → tools flow into catalog/router/execution with ZERO ATLAS code
  (§139's purpose).
* Tests: `tests/tooling/test_mcp_runtime.py` (real stdio via the official SDK —
  connect/negotiate/discover/call/dynamic), `test_mcp_dynamic_e2e.py`
  (catalog sync + Part-4 execution), `test_mcp_security.py`, `test_mcp_e2e.py`
  (real composition root), `test_mcp_performance.py` (measured latencies).
* Fixtures: `tests/tooling/mcp_fixtures/*.py` are REAL MCP servers (official
  SDK) run as child processes — no public servers in CI (§102).
* SDK boundary: import SDK types ONLY inside `tooling/mcp/`; normalize at the
  edge (§131/§132). The `mcp>=2.2.0,<3` constraint lives in pyproject.toml.
