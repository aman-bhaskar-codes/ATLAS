from __future__ import annotations

from atlas.interfaces.api.app import create_app


def test_probe_ws_routes() -> None:
    app = create_app()
    lines = [f"{type(r).__name__} {getattr(r, 'path', '?')}" for r in app.routes]
    raise AssertionError("ROUTES:\n" + "\n".join(lines))
