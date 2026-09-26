"""Domain registry — pluggable routing domains (Part 3 §7-§9/§42).

A domain is a DECLARATION, not code inside the router: what capabilities it
needs, which strategies it supports, whether its runtime is actually
available. No ``if domain == "research"`` anywhere in the engine — behavior
comes from these definitions (§7).

Availability is HONEST (§8): a domain registers even when its runtime is
partially built, and reports ``available=False`` instead of hallucinating
capabilities. Future domains (§9) register here without touching the engine.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from atlas.tooling.errors import ToolingError


@dataclass(frozen=True)
class DomainDefinition:
    id: str
    display_name: str
    description: str
    capabilities: tuple[str, ...] = ()  # capability names this domain routes to
    supported_strategies: tuple[str, ...] = ()  # strategy ids
    #: Optional runtime check — returns True when the domain's backing
    #: subsystem is built and enabled in THIS process (§8).
    availability_check: Callable[[], bool] | None = None
    fallback_policy: str = "deterministic_first"
    planner: str = "dynamic"  # dynamic | template:<id>
    verifier: str = "default"
    tags: tuple[str, ...] = field(default_factory=tuple)

    def is_available(self) -> bool:
        if self.availability_check is None:
            return True
        try:
            return bool(self.availability_check())
        except Exception:
            return False


class DomainRegistry:
    def __init__(self) -> None:
        self._domains: dict[str, DomainDefinition] = {}

    def register(self, definition: DomainDefinition) -> None:
        if definition.id in self._domains:
            raise ToolingError(f"domain {definition.id!r} is already registered")
        self._domains[definition.id] = definition

    def get(self, domain_id: str) -> DomainDefinition:
        domain = self._domains.get(domain_id)
        if domain is None:
            raise ToolingError(f"domain {domain_id!r} is not registered")
        return domain

    def require_available(self, domain_id: str) -> DomainDefinition:
        domain = self.get(domain_id)
        if not domain.is_available():
            raise ToolingError(f"domain {domain_id!r} is registered but not available in this runtime")
        return domain

    def list(self) -> tuple[DomainDefinition, ...]:
        return tuple(self._domains.values())

    def available(self) -> tuple[DomainDefinition, ...]:
        return tuple(d for d in self._domains.values() if d.is_available())

    def __len__(self) -> int:
        return len(self._domains)


# ── Builtin ATLAS domains (§8/§40/§41) ─────────────────────────────────── #
# Availability checks are injected at bootstrap — the routing package declares
# WHAT exists, the composition root decides whether it is live in this process.


def builtin_domains(
    *,
    knowledge_available: Callable[[], bool],
    ide_available: Callable[[], bool],
    ide_capabilities: tuple[str, ...] = (),
) -> list[DomainDefinition]:
    """The three initial domains (§8). Only capabilities that actually exist
    in the repository are declared (§8/§40/§55 of Part 2 honesty rules)."""
    research = DomainDefinition(
        id="research",
        display_name="Research",
        description=(
            "Evidence-first knowledge work over the knowledge fabric: web/academic "
            "search, source retrieval, comparison, synthesis with citations."
        ),
        capabilities=(
            "web_search",
            "academic_search",
            "document_fetch",
            "source_verification",
            "comparison",
            "synthesis",
            "citation",
        ),
        supported_strategies=("RESEARCH", "PARALLEL", "SEQUENTIAL", "DIRECT"),
        availability_check=knowledge_available,
        fallback_policy="deterministic_first",
        tags=("knowledge",),
    )
    ide = DomainDefinition(
        id="agentic_ide",
        display_name="Agentic IDE",
        description=(
            "Repository work through the ADE: inspection, editing, test execution, "
            "debugging. Routed only when the ADE runtime is enabled."
        ),
        capabilities=("repository_inspection", "editing", "test_execution", *ide_capabilities),
        supported_strategies=("SOFTWARE_ENGINEERING", "ITERATIVE", "SEQUENTIAL", "DIRECT"),
        availability_check=ide_available,
        fallback_policy="deterministic_first",
        tags=("coding",),
    )
    general = DomainDefinition(
        id="general",
        display_name="General",
        description=(
            "The default domain: single-agent reasoning over the universal tooling "
            "fabric (filesystem, shell, knowledge, capabilities). Always available."
        ),
        capabilities=("reasoning", "filesystem", "shell", "knowledge", "capability_execution"),
        supported_strategies=("DIRECT", "SINGLE_AGENT", "SEQUENTIAL", "PARALLEL", "DAG"),
        availability_check=None,  # always available (§8)
        fallback_policy="deterministic_first",
        tags=("general",),
    )
    return [general, research, ide]


__all__ = ["DomainDefinition", "DomainRegistry", "builtin_domains"]
