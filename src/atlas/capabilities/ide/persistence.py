"""IDE session persistence — durable, resumable workspaces (Phase 17/42).

WHAT: a narrow store that outlives the process, so a `WorkspaceId` minted in one
invocation (CLI, API request, frontend) is addressable in the next. Without it
the `IDEService` registry is per-process and a workspace id is a dead handle the
moment the command exits — the frontend/API could never resume a session.

WHY a Protocol + one impl: `IDESessionStore` is the seam. Today the only impl is
`SqliteIDESessionStore` over the existing `infra/db.py` substrate (one DB for the
whole runtime — Constitution: one persistence layer, no side doors). A future
paid backend (Neon/Supabase Postgres) is a second impl behind the SAME protocol;
nothing in `IDEService` changes. The migration DDL (`ide_workspaces`,
`ide_sessions`) is deliberately dialect-neutral for exactly that port.

The store persists the FULL pydantic model as JSON (`payload`) and reconstructs
via `model_validate_json`, so the schema grows with the ADE phases (terminals,
processes, debug/browser sessions) without a migration. Indexed scalar columns
are only what we list/query on.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from atlas.capabilities.ide.contracts import (
    IDESession,
    IDESessionId,
    WorkspaceId,
    WorkspaceRef,
)
from atlas.infra.db import Database
from atlas.infra.logging import get_logger

_log = get_logger("atlas.ide.persistence")


@runtime_checkable
class IDESessionStore(Protocol):
    """Durable store for workspaces + their session aggregates. Async because a
    remote (Postgres) impl is I/O-bound; the SQLite impl is too via aiosqlite."""

    async def save_workspace(self, ref: WorkspaceRef) -> None: ...

    async def load_workspace(self, workspace_id: WorkspaceId) -> WorkspaceRef | None: ...

    async def list_workspaces(self) -> tuple[WorkspaceRef, ...]: ...

    async def delete_workspace(self, workspace_id: WorkspaceId) -> None: ...

    async def save_session(self, session: IDESession) -> None: ...

    async def load_session(self, session_id: IDESessionId) -> IDESession | None: ...

    async def sessions_for_workspace(self, workspace_id: WorkspaceId) -> tuple[IDESession, ...]: ...


class SqliteIDESessionStore:
    """`IDESessionStore` over the shared `infra/db.py` SQLite substrate. Upserts
    are idempotent (`INSERT ... ON CONFLICT DO UPDATE`) so re-opening a workspace
    or re-saving a session after every edit is safe and cheap."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- workspaces -----------------------------------------------------
    async def save_workspace(self, ref: WorkspaceRef) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO ide_workspaces (id, name, root_paths, payload, created_ts, last_opened_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                root_paths=excluded.root_paths,
                payload=excluded.payload,
                last_opened_ts=excluded.last_opened_ts
            """,
            (
                str(ref.id),
                ref.name,
                json.dumps(list(ref.root_paths)),
                ref.model_dump_json(),
                ref.created_ts,
                ref.last_opened_ts,
            ),
        )
        await self._db.conn.commit()
        _log.info("ide.workspace.persisted", event_type="db", workspace_id=str(ref.id))

    async def load_workspace(self, workspace_id: WorkspaceId) -> WorkspaceRef | None:
        cur = await self._db.conn.execute("SELECT payload FROM ide_workspaces WHERE id=?", (str(workspace_id),))
        row = await cur.fetchone()
        if row is None:
            return None
        return WorkspaceRef.model_validate_json(row["payload"])

    async def list_workspaces(self) -> tuple[WorkspaceRef, ...]:
        cur = await self._db.conn.execute("SELECT payload FROM ide_workspaces ORDER BY last_opened_ts DESC")
        rows = await cur.fetchall()
        return tuple(WorkspaceRef.model_validate_json(r["payload"]) for r in rows)

    async def delete_workspace(self, workspace_id: WorkspaceId) -> None:
        # Sessions belong to the workspace; drop them together so no orphan rows
        # survive a delete (there is no FK cascade — the two tables are decoupled
        # for the future Postgres port).
        await self._db.conn.execute("DELETE FROM ide_sessions WHERE workspace_id=?", (str(workspace_id),))
        await self._db.conn.execute("DELETE FROM ide_workspaces WHERE id=?", (str(workspace_id),))
        await self._db.conn.commit()
        _log.info("ide.workspace.deleted", event_type="db", workspace_id=str(workspace_id))

    # ---- sessions -------------------------------------------------------
    async def save_session(self, session: IDESession) -> None:
        await self._db.conn.execute(
            """
            INSERT INTO ide_sessions (id, workspace_id, payload, created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload,
                updated_ts=excluded.updated_ts
            """,
            (
                str(session.id),
                str(session.workspace.id),
                session.model_dump_json(),
                session.created_ts,
                session.updated_ts,
            ),
        )
        await self._db.conn.commit()
        _log.info("ide.session.persisted", event_type="db", session_id=str(session.id))

    async def load_session(self, session_id: IDESessionId) -> IDESession | None:
        cur = await self._db.conn.execute("SELECT payload FROM ide_sessions WHERE id=?", (str(session_id),))
        row = await cur.fetchone()
        if row is None:
            return None
        return IDESession.model_validate_json(row["payload"])

    async def sessions_for_workspace(self, workspace_id: WorkspaceId) -> tuple[IDESession, ...]:
        cur = await self._db.conn.execute(
            "SELECT payload FROM ide_sessions WHERE workspace_id=? ORDER BY updated_ts DESC",
            (str(workspace_id),),
        )
        rows = await cur.fetchall()
        return tuple(IDESession.model_validate_json(r["payload"]) for r in rows)
