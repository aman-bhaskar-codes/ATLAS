"""CLI test — `atlas tools list` renders the real registry (§64/§65/§76).

The Typer command talks to the API over HTTP exactly as in production; here the
HTTP client is replaced with a stub serving the same payload shape the real
endpoint produces (covered against the real app in test_acceptance_startup).
"""

from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from atlas_cli.main import app

runner = CliRunner()


class StubClient:
    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    async def _get(self, path: str) -> Any:
        assert path == "/api/v1/tools"
        return self._payload


def _tool(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "native:atlas:filesystem",
        "name": "filesystem",
        "namespace": "native",
        "execution_type": "native",
        "adapter": "native",
        "capability": None,
        "operations": ["read", "list", "write"],
        "status": "ready",
        "status_detail": "",
        "requires_auth": False,
        "default_tier": 1,
        "default_tier_name": "NOTIFY",
        "trust_level": "system_builtin",
        "locality": "local",
        "cost_class": "local",
        "description": "Filesystem tool",
    }
    payload.update(overrides)
    return payload


def test_tools_list_renders_id_type_status_auth_tier(monkeypatch: Any) -> None:
    stub = StubClient(
        [
            _tool(),
            _tool(
                id="capability:atlas:email",
                name="email",
                namespace="capability",
                execution_type="capability",
                capability="email",
                operations=["read", "search", "send"],
                status="ready",
                requires_auth=True,
                default_tier_name="NOTIFY",
                locality="remote",
            ),
        ]
    )
    monkeypatch.setattr("atlas_cli.main.client", stub)
    monkeypatch.setenv("COLUMNS", "250")  # non-tty default (80) truncates IDs
    result = runner.invoke(app, ["tools", "list"])
    assert result.exit_code == 0, result.output
    assert "native:atlas:filesystem" in result.output
    assert "capability:atlas:email" in result.output
    assert "READY" in result.output
    assert "NOTIFY" in result.output


def test_tools_list_survives_server_error(monkeypatch: Any) -> None:
    class BrokenClient:
        async def _get(self, path: str) -> Any:
            raise RuntimeError("server unreachable")

    monkeypatch.setattr("atlas_cli.main.client", BrokenClient())
    result = runner.invoke(app, ["tools", "list"])
    assert result.exit_code != 0  # the error surfaces, silently showing nothing is forbidden
