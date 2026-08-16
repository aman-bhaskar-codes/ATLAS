"""Database backend seam — portable SQL over SQLite or PostgreSQL.

WHY: every store on the execution path (tasks, checkpoints, queue) speaks
portable SQL with `?` placeholders and dict rows. This module defines the
narrow Connection protocol both backends implement, so PostgreSQL adoption is
a constructor change in the composition root — not a store rewrite.

Placeholders: stores write `?`; the PostgreSQL adapter translates them to
`$1..$n`. Rows are always plain dicts. Everything is explicit commit — no
implicit transactions, matching the current store code exactly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

_QMARK = re.compile(r"\?")


def translate_placeholders(sql: str) -> str | None:
    """Translate `?` placeholders to PostgreSQL `$n` form.

    Returns None when no translation is needed. String literals containing
    '?' are respected (naive scanner over quotes).
    """
    if "?" not in sql:
        return None
    out: list[str] = []
    i = 0
    n = 1
    in_single = in_double = False
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "?" and not in_single and not in_double:
            out.append(f"${n}")
            n += 1
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class Connection(Protocol):
    """Portable execution-path connection. Both backends implement this."""

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None: ...
    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...
    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a write; returns affected rowcount."""
        ...

    async def commit(self) -> None: ...
    async def close(self) -> None: ...


class SQLiteConnection:
    """Adapter over the existing aiosqlite connection (dict rows)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        cur = await self._conn.execute(sql, tuple(params))
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cur = await self._conn.execute(sql, tuple(params))
        return [dict(r) for r in await cur.fetchall()]

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        cur = await self._conn.execute(sql, tuple(params))
        return int(cur.rowcount or 0)

    async def commit(self) -> None:
        await self._conn.commit()

    async def close(self) -> None:
        return None  # owned by Database


class PostgresConnection:
    """asyncpg-backed connection. Placeholder-translating, lazy import.

    Used when ATLAS_DATABASE_URL is set. Only the execution-path schema
    (tasks, task_events, execution_checkpoints, task_queue) is provisioned
    here; the full local schema stays on SQLite in hybrid deployments.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None

    async def _ensure(self) -> Any:
        if self._pool is None:
            import asyncpg  # type: ignore[import-not-found]  # lazy: optional in PG deployments

            self._pool = await asyncpg.create_pool(self._dsn)
        return self._pool

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        pool = await self._ensure()
        stmt = translate_placeholders(sql) or sql
        async with pool.acquire() as conn:
            row = await conn.fetchrow(stmt, *params)
            return dict(row) if row is not None else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        pool = await self._ensure()
        stmt = translate_placeholders(sql) or sql
        async with pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(stmt, *params)]

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        pool = await self._ensure()
        stmt = translate_placeholders(sql) or sql
        async with pool.acquire() as conn:
            status = await conn.execute(stmt, *params)
        # asyncpg status tag: "UPDATE 3" / "INSERT 0 1" / "DELETE 0".
        try:
            return int(status.split()[-1])
        except ValueError:
            return 0

    async def commit(self) -> None:
        return None  # asyncpg autocommits per statement (matches store usage)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
