"""Renewable execution ownership on the existing SQLite/Postgres substrate.

The TTL bounds ownership after a crash; it does not limit execution duration.
Every guarded write checks ownership in the same SQL statement as the mutation.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta

from atlas.infra.backends import Connection
from atlas.infra.clock import Clock
from atlas.infra.logging import get_logger

_log = get_logger("atlas.execution_lease")

OWNER_GUARD = "EXISTS (SELECT 1 FROM runtime_execution_leases WHERE resource_id=? AND owner_id=? AND lease_until>?)"
ORPHAN_GUARD = "NOT EXISTS (SELECT 1 FROM runtime_execution_leases WHERE resource_id=? AND lease_until>?)"
# PostgreSQL's owner row is locked through the owned mutation. SQLite already
# serializes database writes. The materialized recovery CTE locks an existing
# expired lease before testing expiry, preventing a concurrent takeover race.
POSTGRES_OWNER_GUARD = OWNER_GUARD[:-1] + " FOR SHARE)"
POSTGRES_ORPHAN_CTE = (
    "WITH execution_owner AS MATERIALIZED (SELECT lease_until FROM runtime_execution_leases "
    "WHERE resource_id=? FOR SHARE) "
)
POSTGRES_ORPHAN_GUARD = "NOT EXISTS (SELECT 1 FROM execution_owner WHERE lease_until>?)"


class OwnershipLostError(RuntimeError):
    """An expired/replaced owner must stop work and cannot acknowledge outcomes."""


@dataclass
class ExecutionOwner:
    resource_id: str
    owner_id: str
    clock: Clock
    leases: ExecutionLeaseStore
    active: bool = True
    admission_open: Callable[[], bool] | None = None

    def parameters(self) -> tuple[str, str, str]:
        if not self.active:
            raise OwnershipLostError("execution ownership has ended")
        return self.resource_id, self.owner_id, self.clock.now().isoformat()


_execution_owner: ContextVar[ExecutionOwner | None] = ContextVar("atlas_execution_owner", default=None)


def current_execution_owner() -> ExecutionOwner | None:
    return _execution_owner.get()


@contextlib.contextmanager
def bind_execution_owner(owner: ExecutionOwner) -> Iterator[None]:
    token = _execution_owner.set(owner)
    try:
        yield
    finally:
        owner.active = False
        _execution_owner.reset(token)


async def require_execution_owner() -> None:
    owner = current_execution_owner()
    if owner is not None:
        if owner.admission_open is not None and not owner.admission_open():
            raise OwnershipLostError("execution admission is closed")
        resource, identity, now = owner.parameters()
        if not await owner.leases.is_owner(resource, identity, now=now):
            owner.active = False
            raise OwnershipLostError("execution lease expired or changed owner")


def lease_times(clock: Clock, ttl_s: float) -> tuple[str, str]:
    now = clock.now()
    return now.isoformat(), (now + timedelta(seconds=ttl_s)).isoformat()


def guarded_upsert(sql: str, *, owner_guard: str = OWNER_GUARD) -> str:
    """Guard both the insert and update arms of a dialect-neutral VALUES upsert."""
    head, remainder = sql.split("VALUES (", 1)
    values, tail = remainder.split(")", 1)
    return head + "SELECT " + values + " WHERE " + owner_guard + tail + " WHERE " + owner_guard


class ExecutionLeaseStore:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    async def claim(self, resource_id: str, owner_id: str, *, now: str, lease_until: str) -> bool:
        changed = await self.connection.execute(
            "INSERT INTO runtime_execution_leases (resource_id,owner_id,lease_until) VALUES (?,?,?) "
            "ON CONFLICT(resource_id) DO UPDATE SET owner_id=excluded.owner_id,lease_until=excluded.lease_until "
            "WHERE runtime_execution_leases.owner_id=? OR runtime_execution_leases.lease_until<=?",
            (resource_id, owner_id, lease_until, owner_id, now),
        )
        await self.connection.commit()
        return changed == 1

    async def renew(self, resource_id: str, owner_id: str, *, now: str, lease_until: str) -> bool:
        changed = await self.connection.execute(
            "UPDATE runtime_execution_leases SET lease_until=? WHERE resource_id=? AND owner_id=? AND lease_until>?",
            (lease_until, resource_id, owner_id, now),
        )
        await self.connection.commit()
        return changed == 1

    async def release(self, resource_id: str, owner_id: str) -> None:
        try:
            await self.connection.execute(
                "DELETE FROM runtime_execution_leases WHERE resource_id=? AND owner_id=?", (resource_id, owner_id)
            )
            await self.connection.commit()
        except Exception:
            pass

    async def is_live(self, resource_id: str, *, now: str) -> bool:
        row = await self.connection.fetchone(
            "SELECT owner_id FROM runtime_execution_leases WHERE resource_id=? AND lease_until>?", (resource_id, now)
        )
        return row is not None

    async def is_owner(self, resource_id: str, owner_id: str, *, now: str) -> bool:
        row = await self.connection.fetchone(
            "SELECT owner_id FROM runtime_execution_leases WHERE resource_id=? AND owner_id=? AND lease_until>?",
            (resource_id, owner_id, now),
        )
        return row is not None

    @contextlib.asynccontextmanager
    async def hold(
        self,
        resource_id: str,
        owner_id: str,
        *,
        clock: Clock,
        ttl_s: float = 30,
        renewal_s: float = 5,
        on_lost: Callable[[], None] | None = None,
    ) -> AsyncIterator[None]:
        if ttl_s <= 0 or renewal_s <= 0 or renewal_s >= ttl_s:
            raise ValueError("lease renewal must be positive and shorter than ownership TTL")
        now, until = lease_times(clock, ttl_s)
        if not await self.claim(resource_id, owner_id, now=now, lease_until=until):
            raise OwnershipLostError(f"resource {resource_id!r} has an active owner")
        worker = asyncio.current_task()

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(renewal_s)
                try:
                    now, until = lease_times(clock, ttl_s)
                    async with asyncio.timeout(renewal_s):
                        alive = await self.renew(resource_id, owner_id, now=now, lease_until=until)
                    if alive:
                        continue
                except Exception:
                    _log.exception("execution.lease_renewal_failed", resource_id=resource_id)
                if on_lost is not None:
                    on_lost()
                if worker is not None:
                    worker.cancel()
                return

        task = asyncio.create_task(heartbeat())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            try:
                await asyncio.wait_for(asyncio.shield(self.release(resource_id, owner_id)), timeout=renewal_s)
            except Exception:
                # A failed release remains safely owned until expiry.
                _log.exception("execution.lease_release_failed", resource_id=resource_id)
