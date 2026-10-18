"""Durable confirmations for one HTTP process; restart never resumes execution.

Only sanitized projections reach SQLite. Executable requests remain owned by the
suspended SafetyEngine guard, never by the queue. Decisions wake that one waiter
only after their conditional SQL update has committed. No background tool runner.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from atlas.infra.backends import SQLiteConnection
from atlas.infra.clock import Clock
from atlas.infra.db import Database
from atlas.infra.execution_lease import OWNER_GUARD, ExecutionLeaseStore, lease_times
from atlas.infra.ids import UuidGenerator
from atlas.infra.logging import get_logger
from atlas.infra.types import SafetyDecision, Tier, ToolRequest

_CODE = re.compile(r"Type confirmation code \[([0-9]{4})\] to proceed\.")
_log = get_logger("atlas.approvals")
_SECRET_KEY = re.compile(
    r"password|passphrase|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key", re.I
)
_PREVIEW_FIELDS = frozenset(
    {
        "command",
        "path",
        "paths",
        "source",
        "destination",
        "target",
        "recipient",
        "recipients",
        "to",
        "subject",
        "operation",
        "url",
        "cwd",
        "name",
    }
)


class ApprovalConflictError(ValueError):
    """The ticket cannot be decided, or this idempotency key was reused."""


ApprovalConflict = ApprovalConflictError


class ApprovalCodeError(ValueError):
    """The dangerous action's challenge was not matched."""


class ApprovalTicket(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: int = 1
    id: str
    task_id: str | None = None
    correlation_id: str
    execution_id: str | None = None
    capability: str
    operation: str
    tier: int
    prompt: str
    preview: str
    exact_preview: str
    warnings: tuple[str, ...]
    policy_version: str
    created_at: datetime
    expires_at: datetime
    status: Literal["pending", "approved", "denied", "expired"] = "pending"
    decision_source: Literal["dashboard", "telegram", "cli"] | None = None
    code_required: bool = False
    confirmation_code: str | None = None


def safe_preview(req: ToolRequest, secret_values: tuple[str, ...] = ()) -> str:
    """Allowlisted action context; omit bodies, headers, env, content and credentials.

    Scrub embedded credential syntax and known secret values even when they appear
    inside a legitimate command/path. File/message bodies show only their size.
    """
    hidden = list(secret_values)

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if _SECRET_KEY.search(str(key)) and isinstance(item, str) and item:
                    hidden.append(item)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(req.args)

    def scrub(value: str) -> str:
        for secret in sorted(hidden, key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(Bearer\s+)[^\s\"\']+", r"\1[REDACTED]", value, flags=re.I)
        value = re.sub(r"(?:sk-|ghp_|gho_|github_pat_)[A-Za-z0-9_-]+", "[REDACTED]", value)
        value = re.sub(r"(https?://)[^/\s@]+@", r"\1[REDACTED]@", value)
        value = re.sub(
            r'((?:password|secret|token|api[_-]?key|authorization|cookie|credential)[\w-]*\s*[=:]\s*)(?:"[^"]*"|\'[^\']*\'|[^\s;&]+)',
            r"\1[REDACTED]",
            value,
            flags=re.I,
        )
        value = re.sub(
            r'(--(?:password|secret|token|api-key|authorization|cookie)\s+)(?:"[^"]*"|\'[^\']*\'|\S+)',
            r"\1[REDACTED]",
            value,
            flags=re.I,
        )
        # High-entropy credential-shaped strings are safer to omit than display.
        value = re.sub(r"(?<![\w])[A-Za-z0-9_+/=-]{32,}(?![\w])", "[REDACTED]", value)
        return value[:1500]

    projected: dict[str, object] = {"tool": scrub(req.tool), "operation": scrub(req.operation)}
    for key in sorted(_PREVIEW_FIELDS & req.args.keys()):
        value = req.args[key]
        if isinstance(value, str):
            projected[key] = scrub(value)
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            projected[key] = [scrub(item) for item in value[:20]]
    for key in ("content", "body", "text", "patch"):
        if isinstance(req.args.get(key), str):
            projected[f"{key}_characters"] = len(req.args[key])
    return json.dumps(projected, ensure_ascii=False, indent=2)


class ApprovalQueue:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        *,
        timeout_s: float = 120,
        policy_version: str = "1",
        secret_values: tuple[str, ...] = (),
        lease_ttl_s: float = 30.0,
        lease_renewal_s: float = 5.0,
        shutdown_timeout_s: float = 5.0,
    ) -> None:
        self.db = db
        self.clock = clock
        self.timeout_s = max(0.01, min(timeout_s, 3600))
        self.policy_version = policy_version
        self._secret_values = secret_values
        self._waiters: dict[str, asyncio.Future[bool]] = {}
        self._drained = asyncio.Event()
        self._drained.set()
        self._lock = asyncio.Lock()
        self._started = False
        self._cancelled: set[str] = set()
        self._owner_id = str(UuidGenerator().execution_id())
        self._lease_ttl_s = lease_ttl_s
        self._lease_renewal_s = lease_renewal_s
        self._shutdown_timeout_s = shutdown_timeout_s
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def _leases(self) -> ExecutionLeaseStore:
        return ExecutionLeaseStore(SQLiteConnection(self.db.conn))

    def _owner_params(self) -> tuple[str, str, str]:
        return "apiapprovals", self._owner_id, self.clock.now().isoformat()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self._lease_renewal_s)
            try:
                now, until = lease_times(self.clock, self._lease_ttl_s)
                async with asyncio.timeout(self._lease_renewal_s):
                    live = await self._leases.renew("apiapprovals", self._owner_id, now=now, lease_until=until)
                if live:
                    continue
            except Exception:
                _log.exception("approvals.owner_renewal_failed")
            # The stale queue cannot accept decisions or enqueue more work. Only
            # its in-memory waiters are denied; a newer owner's tickets are untouched.
            self._closed = True
            self._started = False
            for future in self._waiters.values():
                if not future.done():
                    future.set_result(False)
            return

    async def start(self) -> None:
        """Single HTTP owner only: expire prior-process tickets, never execute them."""
        async with self._lock:
            if self._closed:
                raise ApprovalConflict("Approval queue is closed")
            if self._started:
                return
            if self._lease_ttl_s <= 0 or not 0 < self._lease_renewal_s < self._lease_ttl_s:
                raise ValueError("approval lease renewal must be shorter than ownership TTL")
            now, until = lease_times(self.clock, self._lease_ttl_s)
            if not await self._leases.claim("apiapprovals", self._owner_id, now=now, lease_until=until):
                raise ApprovalConflict("Approval queue already has a live owner")
            try:
                await self.db.conn.execute(
                    "UPDATE approval_tickets SET status='expired' WHERE status='pending' AND " + OWNER_GUARD,
                    self._owner_params(),
                )
                await self.db.conn.commit()
                self._started = True
                self._heartbeat_task = asyncio.create_task(self._heartbeat())
            except BaseException:
                await self._leases.release("apiapprovals", self._owner_id)
                raise

    async def close(self) -> None:
        self._closed = True
        self._started = False
        # Admission and in-memory denial are synchronous; even a stalled store or
        # lock cannot keep a shutdown confirmation alive indefinitely.
        for future in self._waiters.values():
            if not future.done():
                future.set_result(False)
        try:
            async with asyncio.timeout(self._shutdown_timeout_s):
                await self._close_owned()
        except Exception:
            _log.exception("approvals.close_failed")

    async def _close_owned(self) -> None:
        heartbeat = self._heartbeat_task
        if heartbeat is not None:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self._heartbeat_task = None
        async with self._lock:
            self._started = False
            try:
                async with asyncio.timeout(self._shutdown_timeout_s):
                    await self.db.conn.execute(
                        "UPDATE approval_tickets SET status='expired' WHERE status='pending' AND " + OWNER_GUARD,
                        self._owner_params(),
                    )
                    await self.db.conn.commit()
            except Exception:
                _log.exception("approvals.close_expiry_failed")
            finally:
                for future in self._waiters.values():
                    if not future.done():
                        future.set_result(False)
        # Let confirm() finish its durable cleanup before the composition root closes SQLite.
        try:
            async with asyncio.timeout(self._shutdown_timeout_s):
                await self._drained.wait()
                await self._leases.release("apiapprovals", self._owner_id)
        except Exception:
            _log.exception("approvals.close_drain_failed")

    async def cancel(self, correlation_id: str) -> int:
        """Cancel pending approvals and reject later confirmations for this run."""
        async with self._lock:
            self._cancelled.add(correlation_id)
            cursor = await self.db.conn.execute(
                "UPDATE approval_tickets SET status='expired' WHERE status='pending' "
                "AND json_extract(ticket,'$.correlation_id')=? AND " + OWNER_GUARD + " RETURNING id",
                (correlation_id, *self._owner_params()),
            )
            rows = list(await cursor.fetchall())
            await cursor.close()
            await self.db.conn.commit()
            for row in rows:
                future = self._waiters.get(row["id"])
                if future is not None and not future.done():
                    future.set_result(False)
            return len(rows)

    async def _expire_locked(self) -> None:
        cursor = await self.db.conn.execute(
            "UPDATE approval_tickets SET status='expired' WHERE status='pending' AND expires_at <= ? AND "
            + OWNER_GUARD
            + " RETURNING id",
            (self.clock.now().isoformat(), *self._owner_params()),
        )
        expired = await cursor.fetchall()
        await cursor.close()
        await self.db.conn.commit()
        for row in expired:
            future = self._waiters.get(row["id"])
            if future is not None and not future.done():
                future.set_result(False)

    async def _read(self, approval_id: str) -> ApprovalTicket | None:
        cursor = await self.db.conn.execute("SELECT ticket,status FROM approval_tickets WHERE id=?", (approval_id,))
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        data = json.loads(row["ticket"])
        data["status"] = row["status"]
        if row["status"] in {"approved", "denied"}:
            data["decision_source"] = "dashboard"
        return ApprovalTicket.model_validate(data)

    async def pending(self) -> list[ApprovalTicket]:
        async with self._lock:
            await self._expire_locked()
            cursor = await self.db.conn.execute(
                "SELECT id FROM approval_tickets WHERE status='pending' ORDER BY expires_at,id LIMIT 200"
            )
            rows = list(await cursor.fetchall())
            await cursor.close()
            tickets = [await self._read(row["id"]) for row in rows]
            return [ticket for ticket in tickets if ticket is not None]

    async def count_pending(self) -> int:
        async with self._lock:
            await self._expire_locked()
            row = await (
                await self.db.conn.execute("SELECT COUNT(*) AS count FROM approval_tickets WHERE status='pending'")
            ).fetchone()
            return int(row["count"]) if row else 0

    async def get(self, approval_id: str) -> ApprovalTicket | None:
        async with self._lock:
            await self._expire_locked()
            return await self._read(approval_id)

    async def confirm(self, prompt: str, decision: SafetyDecision, req: ToolRequest) -> bool:
        if decision.decision != "require_confirm" or decision.tier >= Tier.BLOCK:
            return False
        codes = _CODE.findall(prompt)
        code = codes[-1] if codes else None
        if decision.tier >= Tier.DANGEROUS and code is None:
            return False
        now = self.clock.now()
        preview = safe_preview(req, self._secret_values)
        metadata = json.loads(preview)
        ticket = ApprovalTicket(
            id=uuid4().hex,
            correlation_id=req.correlation_id,
            capability=metadata["tool"],
            operation=metadata["operation"],
            tier=int(decision.tier),
            prompt=f"Approve {metadata['tool']}.{metadata['operation']} once?",
            preview=preview,
            exact_preview=preview,
            warnings=(
                "Preview omits credential values and message/file bodies. Review the action context before deciding.",
            ),
            policy_version=self.policy_version,
            created_at=now,
            expires_at=now + timedelta(seconds=self.timeout_s),
            code_required=decision.tier >= Tier.DANGEROUS,
            confirmation_code=code,
        )
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        async with self._lock:
            if not self._started or req.correlation_id in self._cancelled:
                return False
            cursor = await self.db.conn.execute(
                "SELECT id FROM tasks WHERE json_valid(payload) AND json_extract(payload,'$.correlation_id')=? "
                "ORDER BY created_ts DESC LIMIT 1",
                (req.correlation_id,),
            )
            task = await cursor.fetchone()
            await cursor.close()
            if task is not None:
                ticket = ticket.model_copy(update={"task_id": task["id"]})
            self._waiters[ticket.id] = future
            self._drained.clear()
            try:
                cursor = await self.db.conn.execute(
                    "INSERT INTO approval_tickets(id,ticket,status,expires_at) SELECT ?,?,?,? WHERE " + OWNER_GUARD,
                    (
                        ticket.id,
                        ticket.model_dump_json(),
                        ticket.status,
                        ticket.expires_at.isoformat(),
                        *self._owner_params(),
                    ),
                )
                await self.db.conn.commit()
                if cursor.rowcount != 1:
                    self._waiters.pop(ticket.id, None)
                    if not self._waiters:
                        self._drained.set()
                    return False
            except BaseException:
                self._waiters.pop(ticket.id, None)
                if not self._waiters:
                    self._drained.set()
                raise
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.timeout_s)
        except TimeoutError:
            future.cancel()
            return False
        finally:
            async with self._lock:
                self._waiters.pop(ticket.id, None)
                try:
                    await self.db.conn.execute(
                        "UPDATE approval_tickets SET status='expired' WHERE id=? AND status='pending'", (ticket.id,)
                    )
                    await self.db.conn.commit()
                finally:
                    if not self._waiters:
                        self._drained.set()

    async def decide(
        self,
        approval_id: str,
        decision: Literal["approve", "deny"],
        idempotency_key: str,
        confirmation_code: str | None = None,
    ) -> ApprovalTicket:
        async with self._lock:
            await self._expire_locked()
            ticket = await self._read(approval_id)
            if ticket is None:
                raise LookupError("Approval not found")
            if decision not in {"approve", "deny"} or not (16 <= len(idempotency_key) <= 200):
                raise ApprovalConflict("Invalid approval decision")
            code_fingerprint = hashlib.sha256(json.dumps(confirmation_code).encode()).hexdigest()
            cursor = await self.db.conn.execute(
                "SELECT decision_key,decision_value,decision_code FROM approval_tickets WHERE id=?", (approval_id,)
            )
            existing = await cursor.fetchone()
            await cursor.close()
            assert existing is not None
            if existing["decision_key"] == idempotency_key:
                if existing["decision_value"] != decision or existing["decision_code"] != code_fingerprint:
                    raise ApprovalConflict("Idempotency key was reused with a different decision")
                return ticket
            if (
                ticket.status != "pending"
                or approval_id not in self._waiters
                or self._waiters[approval_id].done()
                or not self._started
            ):
                raise ApprovalConflict("Approval expired or was already decided")
            if decision == "approve" and ticket.tier >= Tier.BLOCK:
                raise ApprovalConflict("Blocked actions cannot be approved")
            if (
                decision == "approve"
                and ticket.code_required
                and not secrets.compare_digest(
                    (confirmation_code or "").encode(), (ticket.confirmation_code or "").encode()
                )
            ):
                raise ApprovalCodeError("Enter the displayed confirmation code to approve this high-impact action")
            other = await (
                await self.db.conn.execute("SELECT id FROM approval_tickets WHERE decision_key=?", (idempotency_key,))
            ).fetchone()
            if other is not None:
                raise ApprovalConflict("Idempotency key belongs to a different approval")
            status = "approved" if decision == "approve" else "denied"
            cursor = await self.db.conn.execute(
                "UPDATE approval_tickets SET status=?,decision_key=?,decision_value=?,decision_code=? "
                "WHERE id=? AND status='pending' AND expires_at>? AND " + OWNER_GUARD + " RETURNING id",
                (
                    status,
                    idempotency_key,
                    decision,
                    code_fingerprint,
                    approval_id,
                    self.clock.now().isoformat(),
                    *self._owner_params(),
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
            await self.db.conn.commit()
            if row is None:
                raise ApprovalConflict("Approval expired or was already decided")
            result = await self._read(approval_id)
            assert result is not None
            future = self._waiters.get(approval_id)
            if future is not None and not future.done():
                future.set_result(decision == "approve")
            return result
