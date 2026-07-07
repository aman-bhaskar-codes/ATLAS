from __future__ import annotations

import pytest

from atlas.infra.errors import RegistryError
from atlas.infra.registry import ServiceRegistry


class DummyService:
    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    async def health(self) -> bool: return True


def test_registry_ordering() -> None:
    reg = ServiceRegistry()
    s1, s2, s3 = DummyService(), DummyService(), DummyService()
    
    # s3 depends on s2 which depends on s1
    reg.register("s3", s3, deps=("s2",))
    reg.register("s1", s1)
    reg.register("s2", s2, deps=("s1",))

    order = reg.ordered()
    names = [n for n, _ in order]
    assert names == ["s1", "s2", "s3"]


def test_registry_cycle() -> None:
    reg = ServiceRegistry()
    s1, s2 = DummyService(), DummyService()
    reg.register("s1", s1, deps=("s2",))
    reg.register("s2", s2, deps=("s1",))

    with pytest.raises(RegistryError, match="dependency cycle"):
        reg.ordered()
