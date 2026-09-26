"""L4 candidate discovery + L5 hard policy filter (Part 3 §18-§21).

Discovery is deterministic and observable: tool candidates come from the
Part-2 catalog's ``find_candidates`` (NEVER raw SQL — §18), domain/strategy
candidates from their registries. AGENT/WORKFLOW/MODEL/PROVIDER candidate
types exist in the IR; their discovery attaches here when backing systems
register (§17).

The HARD FILTER runs before ANY judgment provider sees a candidate (§20):
an AI system is never asked to choose something policy has already forbidden.
Filtering uses catalog POLICY METADATA — the SafetyEngine remains the sole
execution authority (§21).
"""

from __future__ import annotations

from atlas.tooling.catalog.catalog import ToolCatalog
from atlas.tooling.catalog.models import CatalogToolRecord
from atlas.tooling.routing.models import (
    CandidateType,
    RejectedCandidate,
    RouteCandidate,
    TaskIR,
)


def tool_record_to_candidate(record: CatalogToolRecord) -> RouteCandidate:
    """Catalog record → routable candidate. Field-for-field policy mirror."""
    return RouteCandidate(
        candidate_type=CandidateType.TOOL,
        candidate_id=record.tool_id,
        display_name=record.tool_name,
        description=record.description,
        capability=record.capability,
        operations=record.operations,
        status=record.status.value,
        enabled=record.enabled,
        availability=record.availability.value,
        requires_auth=record.requires_auth,
        auth_state=record.auth_state.value,
        cost_class=record.cost_class,
        estimated_cost_usd=record.estimated_cost_usd,
        estimated_latency_ms=record.estimated_latency_ms,
        locality=record.locality.value,
        privacy_class=record.privacy_class,
        network_required=record.network_required,
        trust_level=record.trust_level,
        side_effects=record.side_effects,
        idempotent=record.idempotent,
        safety_tool=record.safety_tool,
        tags=record.tags,
    )


class CandidateDiscovery:
    """L4: TaskIR + capability requirement → deterministic candidate set."""

    def __init__(self, catalog: ToolCatalog | None) -> None:
        self._catalog = catalog

    def discover_tools(
        self,
        task: TaskIR,
        *,
        capability: str | None = None,
        operation: str | None = None,
        limit: int | None = None,
    ) -> list[RouteCandidate]:
        if self._catalog is None:
            return []
        records = self._catalog.find_candidates(
            capability=capability,
            operation=operation,
            limit=limit,
        )
        return [tool_record_to_candidate(record) for record in records]

    def discover_all(self, task: TaskIR) -> list[RouteCandidate]:
        """Every cataloged, currently-READY tool as a candidate (§19)."""
        return self.discover_tools(task, limit=None)


class HardFilter:
    """L5: policy elimination BEFORE judgment (§20). Returns survivors plus
    structured rejections for the route explanation (§68)."""

    def __init__(self, *, max_offline_free_cost: bool = True) -> None:
        self._max_offline_free_cost = max_offline_free_cost

    def apply(
        self,
        candidates: list[RouteCandidate],
        task: TaskIR,
        *,
        required_operation: str | None = None,
    ) -> tuple[list[RouteCandidate], list[RejectedCandidate]]:
        survivors: list[RouteCandidate] = []
        rejected: list[RejectedCandidate] = []

        def reject(candidate: RouteCandidate, reason: str) -> None:
            rejected.append(RejectedCandidate(candidate_id=candidate.candidate_id, reason=reason, stage="hard_filter"))

        for candidate in candidates:
            # §84 invariants: each check below is also property-tested.
            if candidate.candidate_type == CandidateType.TOOL:
                if not candidate.enabled or candidate.status == "DISABLED":
                    reject(candidate, "disabled")
                    continue
                if candidate.status in ("STALE", "UNAVAILABLE", "REMOVED"):
                    reject(candidate, f"catalog status {candidate.status}")
                    continue
                if candidate.availability == "UNAVAILABLE":
                    reject(candidate, "unavailable")
                    continue
                if required_operation is not None and required_operation not in candidate.operations:
                    reject(candidate, f"unsupported operation {required_operation!r}")
                    continue
                # §84: missing-auth candidate never selected where auth required.
                if candidate.requires_auth and candidate.auth_state in ("MISSING", "INVALID"):
                    reject(candidate, f"auth {candidate.auth_state}")
                    continue
                # §84: offline routing never selects network-only candidates.
                if task.network_policy == "offline" and candidate.network_required:
                    reject(candidate, "network policy offline")
                    continue
                # §84: privacy — a candidate may not process data ABOVE its ceiling.
                privacy_rank = {"public": 0, "internal": 1, "private": 2, "sensitive": 3, "secret": 4}
                task_rank = privacy_rank.get(task.privacy_class.value, 0)
                candidate_rank = privacy_rank.get(candidate.privacy_class, 0)
                if task_rank > candidate_rank:
                    reject(
                        candidate,
                        f"privacy: task {task.privacy_class.value} exceeds candidate ceiling {candidate.privacy_class}",
                    )
                    continue
                # §36 of Part 1 / §33 here: cost policy — paid never silently appears.
                if task.cost_policy == "zero_cost" and candidate.cost_class == "paid":
                    reject(candidate, "cost policy zero_cost forbids paid candidates")
                    continue
                if task.cost_policy == "free_only" and candidate.cost_class == "paid":
                    reject(candidate, "cost policy free_only forbids paid candidates")
                    continue
                if candidate.trust_level == "unknown":
                    reject(candidate, "trust level unknown")
                    continue
            survivors.append(candidate)
        return survivors, rejected


__all__ = ["CandidateDiscovery", "HardFilter", "tool_record_to_candidate"]
