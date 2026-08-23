"""Status fields must come from runtime state, never from literals.

THE BUGS THIS PINS — four hardcoded values in ``facade.py``:

* ``version="1.0.0"`` — a literal, while ``app.state.version`` already held
  ``importlib.metadata.version("atlas")``. It would have read 1.0.0 forever.
* ``runtime_health()`` reported ONE check, "database", derived from
  ``Database.health()`` — which was literally ``return self._conn is not None``.
  Every failure that does not drop the connection object (a corrupt file, a
  locked WAL, a failed migration) reported healthy, so the Command Center's
  health strip was green by construction. A real ``RuntimeSupervisor`` with
  per-component checks already existed and already backed /live, /ready, /health.
* ``get_capabilities()`` returned ``state="ready"``, ``providers=1``,
  ``healthy_providers=1``, ``requires_auth=False`` for all seven capabilities
  regardless of runtime state — including ``requires_auth``, which is a real,
  varying field on ``CapabilitySpec``.

Per the project's truth mandate, status shown in the UI must be backed by
something checkable. These tests check that the numbers move with reality.
"""

from __future__ import annotations

from importlib.metadata import version as pkg_version
from pathlib import Path
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient

from tests.api.conftest import app_client


async def test_version_is_the_installed_package_version(api_client: AsyncClient) -> None:
    """Not a literal, and specifically not the "1.0.0" that used to be hardcoded."""
    body = (await api_client.get("/api/v1/runtime/status")).json()

    assert body["version"] == pkg_version("atlas")
    assert body["version"] != "1.0.0" or pkg_version("atlas") == "1.0.0", (
        "the hardcoded 1.0.0 is back"
    )


async def test_version_falls_back_to_unknown_not_to_a_plausible_number(tmp_path: Path) -> None:
    """With no version on app.state the answer is "unknown", never a made-up number.

    A getattr default of "0.1.0" or "1.0.0" would be indistinguishable from a real
    reading. "unknown" is checkable; a plausible number is not.
    """
    async with app_client(tmp_path) as (app, client):
        del app.state.version
        body = (await client.get("/api/v1/runtime/status")).json()

    assert body["version"] == "unknown"


async def test_health_reports_every_supervised_component(api_client: AsyncClient) -> None:
    """More than one check, and they are the supervisor's real component names."""
    body = (await api_client.get("/api/v1/runtime/health")).json()

    names = [c["name"] for c in body["checks"]]
    assert len(names) > 1, (
        f"only {names} reported — the single hardcoded 'database' check is back"
    )
    assert len(names) == len(set(names)), "duplicate component names"
    assert all(c["status"] in {"pass", "warn", "fail"} for c in body["checks"])


async def test_health_degrades_when_a_component_fails(tmp_path: Path) -> None:
    """Forcing one supervised component down must show up in the response.

    Reaching for ``_set_component_health`` on purpose: that is the seam the
    supervisor's own 60s monitor writes through, so driving it is the only way to
    simulate a component failure without breaking a real dependency.
    """
    from atlas.bootstrap.runtime import ComponentStatus

    async with app_client(tmp_path) as (app, client):
        supervisor = app.state.atlas.runtime_supervisor
        assert supervisor is not None, "the managed startup path must install a supervisor"
        supervisor._set_component_health(
            "database", ComponentStatus.UNAVAILABLE, detail="forced down by test"
        )
        body = (await client.get("/api/v1/runtime/health")).json()

    database = next(c for c in body["checks"] if c["name"] == "database")
    assert database["status"] == "fail", "a downed component still reported as passing"
    assert database["detail"] == "forced down by test"


async def test_health_falls_back_to_a_real_query_without_a_supervisor(tmp_path: Path) -> None:
    """No supervisor => one check, and it must be an executed query.

    ``Database.health()`` now runs ``SELECT 1``. Patching it False must flip the
    endpoint to unavailable; before the fix the endpoint could not observe any
    database failure that left the connection object in place.
    """
    async with app_client(tmp_path) as (app, client):
        app.state.atlas.runtime_supervisor = None
        with patch("atlas.infra.db.Database.health", AsyncMock(return_value=False)):
            body = (await client.get("/api/v1/runtime/health")).json()

    assert body["overall"] == "unavailable"
    assert [c["name"] for c in body["checks"]] == ["database"]
    assert body["checks"][0]["status"] == "fail"


async def test_capabilities_report_real_provider_counts(api_client: AsyncClient) -> None:
    """Counts come from the provider registry, so they are NOT all 1."""
    caps = (await api_client.get("/api/v1/capabilities")).json()

    assert caps, "no capabilities registered — this test would pass vacuously"
    counts = {(c["providers"], c["healthy_providers"]) for c in caps}
    assert counts != {(1, 1)}, "every capability reports 1/1 — the literals are back"
    for cap in caps:
        assert cap["healthy_providers"] <= cap["providers"], (
            f"{cap['name']}: more healthy providers than registered ones"
        )


async def test_capabilities_report_real_auth_requirements(api_client: AsyncClient) -> None:
    """requires_auth varies across the registry; a blanket False is a lie."""
    caps = (await api_client.get("/api/v1/capabilities")).json()

    flags = {c["requires_auth"] for c in caps}
    assert flags != {False}, (
        "no capability requires auth — email/calendar/contacts declare "
        "requires_auth=True in bootstrap/capabilities.py"
    )


async def test_capability_state_is_derived_not_assumed(api_client: AsyncClient) -> None:
    """State must be one of the derived values, and not uniformly 'ready'."""
    caps = (await api_client.get("/api/v1/capabilities")).json()

    for cap in caps:
        assert cap["state"] in {"ready", "degraded", "unavailable", "planned"}
        # A capability with providers is ready only if some are healthy.
        if cap["providers"] and not cap["healthy_providers"]:
            assert cap["state"] == "degraded", (
                f"{cap['name']} has {cap['providers']} providers, none healthy, "
                f"yet reports {cap['state']!r}"
            )


async def test_degraded_provider_chain_shows_as_degraded(tmp_path: Path) -> None:
    """Open every circuit breaker: the provider-backed capability must degrade.

    This is the assertion the hardcoded ``state="ready"`` could never fail. Only
    KNOWLEDGE is provider-backed today (bootstrap/capabilities.py registers
    providers for it alone), so it is the one capability whose state can move.
    """
    async with app_client(tmp_path) as (app, client):
        atlas = app.state.atlas
        with patch.object(atlas.cap_providers, "healthy_for_capability", return_value=[]):
            caps = (await client.get("/api/v1/capabilities")).json()

    backed = [c for c in caps if c["providers"] > 0]
    assert backed, "no capability is provider-backed — nothing to degrade"
    for cap in backed:
        assert cap["state"] == "degraded", f"{cap['name']} kept reporting {cap['state']!r}"
        assert cap["healthy_providers"] == 0
