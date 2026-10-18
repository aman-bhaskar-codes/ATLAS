from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.infra.types import CostClass
from atlas.intelligence.health.health_monitor import HealthMonitor
from atlas.intelligence.registry.provider_registry import ProviderRegistry
from atlas.interfaces.api.routes_providers import router


class UnavailableProvider:
    name = "gemini"
    is_local = False

    async def health(self) -> bool:
        return False


async def test_provider_health_probes_adapter_and_uses_actual_latency_snapshot() -> None:
    registry = ProviderRegistry()
    registry.register(UnavailableProvider())
    health = HealthMonitor()
    health.record("gemini", ok=True, latency_ms=12)
    app = FastAPI()
    app.include_router(router)
    app.state.atlas = SimpleNamespace(
        gateway=SimpleNamespace(_runtime=SimpleNamespace(_providers=registry, _health=health))
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/providers/health")
    assert response.status_code == 200
    assert response.json()[0]["healthy"] is False
    assert response.json()[0]["avg_latency_ms"] == 12


async def test_paid_provider_quota_does_not_make_it_free() -> None:
    registry = ProviderRegistry()
    registry.register(UnavailableProvider())
    app = FastAPI()
    app.include_router(router)
    app.state.atlas = SimpleNamespace(
        gateway=SimpleNamespace(
            _runtime=SimpleNamespace(
                _providers=registry,
                _health=HealthMonitor(),
                _quota=SimpleNamespace(remaining=lambda _: {"pct_remaining": 100}),
            )
        ),
        model_registry=SimpleNamespace(all=lambda: [SimpleNamespace(provider="gemini", cost_class=CostClass.PAID)]),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/providers/free")
    assert response.status_code == 200
    assert response.json() == []
