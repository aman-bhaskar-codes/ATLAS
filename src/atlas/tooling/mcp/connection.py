"""MCP connection — one server's session lifecycle (Part 5 §9/§15/§62/§66-§68).

The SDK's transports are async context managers, so each connection runs a
background task that owns the transport + `ClientSession` contexts for the
connection's lifetime; tool calls use the shared session handle. On unexpected
task death the connection is marked DEGRADED and reconnects per policy with
bounded restarts (§16) — catalog entries are retained (§15/§43).

The negotiated protocol version, server info, and capabilities are captured at
initialize (§30-§31) — never hard-coded.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from atlas.infra.logging import get_logger
from atlas.tooling.mcp.errors import (
    MCPConnectionError,
    MCPDisabledError,
    MCPDiscoveryError,
    MCPToolCallError,
)
from atlas.tooling.mcp.models import (
    ConnectionState,
    MCPServerDefinition,
    MCPServerInfo,
    transition_connection,
)
from atlas.tooling.mcp.security import EndpointPolicy, EnvironmentPolicy, StdioCommandPolicy

_log = get_logger("atlas.tooling.mcp.connection")


def _root_cause(exc: BaseException) -> BaseException:
    """Unpack anyio ExceptionGroups recursively to the first leaf."""
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            return _root_cause(sub)
    return exc


class MCPConnection:
    """One configured server's live connection."""

    def __init__(
        self,
        definition: MCPServerDefinition,
        *,
        identity: Any | None = None,  # IdentityPlatform for credential resolution (§24)
        publisher: Any | None = None,  # MCPEventPublisher
        command_policy: StdioCommandPolicy | None = None,
        on_list_changed: Any | None = None,  # callback(server_id) — debounced by the manager
    ) -> None:
        self.definition = definition
        self._identity = identity
        self._publisher = publisher
        self._command_policy = command_policy or StdioCommandPolicy()
        self._env_policy = EnvironmentPolicy()
        self._endpoint_policy = EndpointPolicy(definition.endpoint_policy)
        self._on_list_changed = on_list_changed

        self.state = ConnectionState.CONFIGURED
        self.info: MCPServerInfo | None = None
        self.session: Any | None = None  # mcp ClientSession — INSIDE this package only (§131)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._restarts: list[float] = []
        self._lock = asyncio.Lock()
        # §60: per-server health counters (reuses the log/metrics fabric, §128)
        self.counters: dict[str, int] = {
            "connect_count": 0,
            "reconnect_count": 0,
            "discovery_count": 0,
            "discovery_failures": 0,
            "tool_calls": 0,
            "tool_failures": 0,
            "timeouts": 0,
            "auth_failures": 0,
            "crashes": 0,
        }
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_failure: str | None = None

    # ── State machine ─────────────────────────────────────────────── #

    def _transition(self, new: ConnectionState) -> None:
        self.state = transition_connection(self.state, new)

    # ── Lifecycle (§63-§67) ───────────────────────────────────────── #

    async def connect(self) -> MCPServerInfo:
        """Validate → connect → (authenticate) → negotiate → READY (§122)."""
        async with self._lock:
            if not self.definition.enabled:
                raise MCPDisabledError(f"server {self.definition.server_id!r} is disabled")
            if self.state == ConnectionState.READY:
                return self.info  # type: ignore[return-value]
            if self._task is not None and not self._task.done():
                # a reconnect task may already be dialing
                await self._wait_ready()
                return self.info  # type: ignore[return-value]

            self._validate_definition()
            if self.state == ConnectionState.CONFIGURED:
                self._transition(ConnectionState.VALIDATING)
            else:  # reconnect path: DISCONNECTED/FAILED → CONNECTING (§9)
                self._transition(ConnectionState.CONNECTING)
            await self._publish("mcp.server.connecting")
            self._stop = asyncio.Event()
            self._task = asyncio.ensure_future(self._run_session())
            await self._wait_ready()
            self.counters["connect_count"] += 1
            return self.info  # type: ignore[return-value]

    async def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.definition.timeout_s  # §77: connect ≠ tool-call timeout
        while time.monotonic() < deadline:
            if self.state == ConnectionState.READY:
                return
            if self.state in (ConnectionState.FAILED, ConnectionState.DISABLED):
                raise MCPConnectionError(f"connection to {self.definition.server_id!r} failed: {self.last_failure}")
            await asyncio.sleep(0.02)
        raise MCPConnectionError(
            f"connection to {self.definition.server_id!r} timed out after {self.definition.timeout_s}s"
        )

    def _validate_definition(self) -> None:
        if self.definition.transport == "stdio":
            self._command_policy.validate(
                command=self.definition.command or "",
                args=self.definition.args,
                cwd=self.definition.cwd,
            )
        else:
            self._endpoint_policy.validate_url(self.definition.url or "")

    async def _run_session(self) -> None:
        """Owns the SDK transport + session contexts for the connection's
        lifetime. Unexpected exit → bounded reconnect (§15/§16)."""
        from mcp import ClientSession

        try:
            async with self._open_transport() as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    message_handler=self._on_message,
                ) as session:
                    self.session = session
                    # §9/§31: CONNECTING → NEGOTIATING → READY via the SDK's
                    # automatic protocol negotiation (initialize). VALIDATING
                    # (initial connect) also permits the jump to NEGOTIATING.
                    if self.state == ConnectionState.CONNECTING:
                        self._transition(ConnectionState.NEGOTIATING)
                    else:
                        self._transition(ConnectionState.CONNECTING)
                        self._transition(ConnectionState.NEGOTIATING)
                    init = await asyncio.wait_for(session.initialize(), timeout=self.definition.timeout_s)
                    capabilities = (
                        init.capabilities.model_dump(exclude_none=True) if init.capabilities is not None else {}
                    )
                    self.info = MCPServerInfo(
                        server_id=self.definition.server_id,
                        server_name=init.server_info.name,
                        server_version=init.server_info.version,
                        protocol_version=init.protocol_version,
                        capabilities=capabilities,
                        instructions=init.instructions or "",
                        transport=self.definition.transport,
                        connected_at=datetime.now(UTC),
                    )
                    self._transition(ConnectionState.READY)
                    self.last_success_at = datetime.now(UTC)
                    self.last_failure = None
                    await self._publish("mcp.server.ready", protocol_version=init.protocol_version)
                    # Hold the contexts open until stop — calls use self.session.
                    await self._stop.wait()
        except asyncio.CancelledError:
            self.session = None
            raise
        except BaseException as exc:
            self.session = None
            # §66/§123: a graceful stop unwinds the transport contexts, which
            # anyio reports as cancellation / ExceptionGroups — that is NOT a
            # crash. Only an unexpected unwind while NOT stopping reconnects.
            root = _root_cause(exc)
            if self._stop.is_set() or isinstance(root, asyncio.CancelledError):
                if self.state != ConnectionState.DISABLED:
                    self._transition(ConnectionState.DISCONNECTED)
                await self._publish("mcp.server.disconnected")
                return
            message = f"{type(root).__name__}: {root}"
            self.counters["crashes"] += 1
            self.last_failure_at = datetime.now(UTC)
            self.last_failure = message
            auth_markers = ("401", "unauthorized", "credential", "oauth")
            if any(m in message.lower() for m in auth_markers):
                self.counters["auth_failures"] += 1
                await self._publish("mcp.auth.failed", error=self.last_failure)
            _log.error(
                "mcp.connection.lost",
                event_type="mcp",
                server_id=self.definition.server_id,
                error=message,
            )
            await self._handle_connection_loss(root)
            return
        # graceful stop
        self.session = None
        if self.state != ConnectionState.DISABLED:
            self._transition(ConnectionState.DISCONNECTED)
        await self._publish("mcp.server.disconnected")

    def _open_transport(self) -> Any:
        """Open the SDK transport context for the configured transport (§10)."""
        definition = self.definition
        if definition.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=definition.command or "",
                args=list(definition.args),
                env=self._env_policy.build(definition.env),  # §13: allowlist, never full env
                cwd=definition.cwd,
            )
            return stdio_client(params)
        url = definition.url or ""
        self._endpoint_policy.validate_resolved(url)  # §21: best-effort resolution check
        if definition.transport == "streamable_http":
            from mcp.client.streamable_http import streamable_http_client

            return streamable_http_client(url)
        from mcp.client.sse import sse_client

        return sse_client(url)  # §10: legacy compatibility only

    async def _handle_connection_loss(self, exc: BaseException) -> None:
        """§15/§16: mark degraded, keep catalog entries, bounded reconnect."""
        if self.state in (ConnectionState.READY, ConnectionState.DEGRADED, ConnectionState.NEGOTIATING):
            try:
                self._transition(ConnectionState.RECONNECTING)
            except Exception:
                pass
        else:
            try:
                self._transition(ConnectionState.FAILED)
            except Exception:
                pass
        await self._publish("mcp.server.failed", error=str(exc))

        now = time.monotonic()
        window = self.definition.reconnect_policy.restart_window_s
        self._restarts = [t for t in self._restarts if now - t < window]
        self._restarts.append(now)
        if len(self._restarts) > self.definition.reconnect_policy.max_restarts:
            _log.error(
                "mcp.reconnect.circuit_open",
                event_type="mcp",
                server_id=self.definition.server_id,
                restarts=len(self._restarts),
            )
            try:
                self._transition(ConnectionState.FAILED)
            except Exception:
                pass
            return
        backoff = min(
            self.definition.reconnect_policy.initial_backoff_s * (2 ** (len(self._restarts) - 1)),
            self.definition.reconnect_policy.max_backoff_s,
        )
        await self._publish("mcp.server.reconnecting", backoff_s=backoff)
        await asyncio.sleep(backoff)
        if self._stop.is_set():
            return
        self.counters["reconnect_count"] += 1
        self._task = asyncio.ensure_future(self._run_session())

    def _on_message(self, message: Any) -> Any:
        """SDK notification hook: tools-list-changed → debounced refresh (§40)."""
        kind = type(message).__name__
        if "ToolListChanged" in kind and self._on_list_changed is not None:
            self._on_list_changed(self.definition.server_id)
        return message

    # ── Operations (§32/§45-§46/§53/§129) ─────────────────────────── #

    def _require_ready(self) -> Any:
        if not self.definition.enabled:
            raise MCPDisabledError(f"server {self.definition.server_id!r} is disabled")
        if self.state != ConnectionState.READY or self.session is None:
            raise MCPConnectionError(f"server {self.definition.server_id!r} is not READY")
        return self.session

    async def discover_tools(self) -> list[Any]:
        """§32-§33: paginated tools/list via the official client (§3)."""
        session = self._require_ready()
        from mcp.types import PaginatedRequestParams

        tools: list[Any] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params = PaginatedRequestParams(cursor=cursor) if cursor else None
            page = await asyncio.wait_for(session.list_tools(params=params), timeout=self.definition.timeout_s)
            tools.extend(page.tools)
            self.counters["discovery_count"] += 1
            cursor = page.next_cursor
            if not cursor:
                break
            if cursor in seen_cursors:  # §107: a looping cursor is a protocol anomaly
                raise MCPDiscoveryError(f"server {self.definition.server_id!r} returned a repeating cursor")
            seen_cursors.add(cursor)
        self.last_success_at = datetime.now(UTC)
        return tools

    def check_duplicates(self, tools: list[Any]) -> list[str]:
        """§85: duplicate tool names from one server are an anomaly — recorded,
        never silently merged."""
        names = [t.name for t in tools]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            self.counters["discovery_failures"] += 0  # anomaly is not a failure; recorded below
            _log.warning(
                "mcp.discovery.duplicate_tools",
                event_type="mcp",
                server_id=self.definition.server_id,
                duplicates=duplicates,
            )
        return duplicates

    async def discover_resources(self) -> list[Any]:
        """§45: only when the server advertises resources."""
        session = self._require_ready()
        caps = (self.info.capabilities if self.info else {}).get("resources")
        if not caps:
            return []
        page = await asyncio.wait_for(session.list_resources(), timeout=self.definition.timeout_s)
        return list(getattr(page, "resources", ()))

    async def discover_prompts(self) -> list[Any]:
        """§46: prompts are cataloged as prompts, never as tools (§83)."""
        session = self._require_ready()
        caps = (self.info.capabilities if self.info else {}).get("prompts")
        if not caps:
            return []
        page = await asyncio.wait_for(session.list_prompts(), timeout=self.definition.timeout_s)
        return list(getattr(page, "prompts", ()))

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> Any:
        """§53/§76: tools/call with the TOOL-CALL timeout (separate from the
        connect timeout, §77). The caller owns SafetyEngine authorization —
        this method is only reachable through the governed adapter path."""
        session = self._require_ready()
        self.counters["tool_calls"] += 1
        started = time.perf_counter()
        effective = timeout_s or self.definition.tool_call_timeout_s
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments=arguments, read_timeout_seconds=effective),
                timeout=effective + 1.0,
            )
        except TimeoutError:
            self.counters["timeouts"] += 1
            raise MCPToolCallError(
                f"tools/call {tool_name!r} timed out after {effective}s on {self.definition.server_id!r}"
            ) from None
        duration_ms = int((time.perf_counter() - started) * 1000)
        if getattr(result, "is_error", False):
            self.counters["tool_failures"] += 1
        self.last_success_at = datetime.now(UTC)
        await self._publish(
            "mcp.tool.call.completed",
            tool=tool_name,
            duration_ms=duration_ms,
            is_error=bool(getattr(result, "is_error", False)),
        )
        return result

    # ── Shutdown (§66/§123) ───────────────────────────────────────── #

    async def disconnect(self) -> None:
        """§66/§123: stop the session loop; the SDK contexts terminate the
        child process; catalog history and credentials are preserved."""
        if self.state == ConnectionState.DISABLED:
            return
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            try:
                self._transition(ConnectionState.DISCONNECTING)
            except Exception:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self.session = None
        self._task = None
        if self.state != ConnectionState.DISCONNECTED:
            self._transition(ConnectionState.DISCONNECTED)
        await self._publish("mcp.server.disconnected")

    def disable(self) -> None:
        """§125: no auto-connect, no routing, no execution — config kept."""
        self._stop.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self.session = None
        self.state = transition_connection(self.state, ConnectionState.DISABLED)

    # ── Health (§60/§126) ─────────────────────────────────────────── #

    def health(self) -> dict[str, Any]:
        return {
            "server_id": self.definition.server_id,
            "state": self.state.value,
            "connected": self.state == ConnectionState.READY,
            "protocol_version": self.info.protocol_version if self.info else "",
            "counters": dict(self.counters),
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_failure": self.last_failure,
        }

    async def _publish(self, kind: str, **payload: Any) -> None:
        if self._publisher is not None:
            await self._publisher.publish(kind, server_id=self.definition.server_id, **payload)


__all__ = ["MCPConnection"]
