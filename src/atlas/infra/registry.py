"""Service registry + topological ordering.

WHY not a DI container framework: we only need ordered lifecycle over a handful
of services. A tiny topo-sort is explicit and debuggable; a framework would be
speculative complexity. Business logic still gets deps via constructor
injection at the composition root, NOT via registry lookup.
"""

from __future__ import annotations

from typing import Protocol

from atlas.infra.errors import RegistryError


class Service(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def health(self) -> bool: ...


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, Service] = {}
        self._deps: dict[str, tuple[str, ...]] = {}

    def register(self, name: str, service: Service, deps: tuple[str, ...] = ()) -> None:
        if name in self._services:
            raise RegistryError(f"service {name!r} already registered")
        self._services[name] = service
        self._deps[name] = deps

    def get(self, name: str) -> Service:
        try:
            return self._services[name]
        except KeyError as exc:
            raise RegistryError(f"unknown service {name!r}") from exc

    def ordered(self) -> list[tuple[str, Service]]:
        """Return services in dependency-first order (deps before dependents)."""
        visited: dict[str, int] = {}  # 0 = visiting, 1 = done
        order: list[str] = []

        def visit(node: str) -> None:
            state = visited.get(node)
            if state == 1:
                return
            if state == 0:
                raise RegistryError(f"dependency cycle at service {node!r}")
            visited[node] = 0
            for dep in self._deps.get(node, ()):
                if dep not in self._services:
                    raise RegistryError(f"service {node!r} depends on unknown {dep!r}")
                visit(dep)
            visited[node] = 1
            order.append(node)

        for name in self._services:
            visit(name)
        return [(n, self._services[n]) for n in order]
