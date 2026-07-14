"""Typer CLI — the Phase 1 control surface.

WHY an EchoTool lives here: Phase 1 has no real tools, but we must be able to
drive a ToolRequest through the Safety Engine end-to-end. EchoTool is a test
affordance, not a product tool — it declares tool='filesystem' so manifest rules
apply.
"""

from __future__ import annotations

import asyncio
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from atlas.app import build
from atlas.diagnostics.doctor import exit_code, run_doctor
from atlas.infra.types import InboundEvent, ModelRequest, SideEffect, ToolRequest, ToolResult
from atlas.safety.engine import DeniedError, HaltedError

app = typer.Typer(add_completion=False, help="ATLAS control CLI (Phase 1)")
console = Console()


class EchoTool:
    name = "filesystem"

    def dry_run(self, args: dict[str, Any]) -> str:
        return f"echo {args} (no real side effect in Phase 1)"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=True, output={"echo": args},
            side_effects=(SideEffect(kind="noop", target=str(args), reversible=True),),
        )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@app.command()
def doctor(verify_manifest: bool = typer.Option(False, "--verify-manifest")) -> None:
    async def go() -> int:
        atlas = await build()
        await atlas.db.start()
        results = await run_doctor(atlas, verify_manifest_only=verify_manifest)
        table = Table("check", "status", "detail")
        for r in results:
            color = {"pass": "green", "warn": "yellow", "fail": "red"}[r.status]
            table.add_row(r.name, f"[{color}]{r.status}[/]", r.detail)
        console.print(table)
        code = exit_code(results)
        await atlas.close()
        return code
    raise typer.Exit(_run(go()))


@app.command("fs")
def filesystem(
    operation: str,
    path: str,
    query: str = typer.Option("", "--query"),
    content: str = typer.Option("", "--content"),
) -> None:
    """Phase 2: drive filesystem_tool through the Safety Engine."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        tool = atlas.tools["filesystem"]
        args: dict[str, Any] = {"operation": operation, "path": path,
                                "query": query, "content": content}
        if operation == "delete":
            # bridge dry_run count -> classifier for mass_deletion decisions
            count = tool._count_delete_targets(path)  # type: ignore[attr-defined]
            args["target_count"] = count
        op_tier = "read" if operation in ("read", "search") else \
                  ("delete" if operation == "delete" else "write")
        req = ToolRequest(correlation_id=atlas.ids.correlation_id(),
                          tool="filesystem", operation=op_tier, args=args)
        try:
            result = await atlas.safety.guard(req, tool)
            if result.ok:
                console.print(f"[green]OK[/] {result.output}")
            else:
                console.print(f"[red]{result.error}[/]")
        except Exception as exc:
            console.print(f"[red]{type(exc).__name__}[/] {exc}")
        await atlas.close()
    _run(go())


@app.command("sh")
def shell(command: str) -> None:
    """Phase 2: run an allowlisted command in the sandbox via the Safety Engine."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        tool = atlas.tools["shell"]
        first = command.strip().split()[0] if command.strip() else ""
        read_only = first in {"ls", "cat", "grep", "find", "git"}
        req = ToolRequest(correlation_id=atlas.ids.correlation_id(), tool="shell",
                          operation="read_only" if read_only else "side_effect",
                          args={"command": command})
        try:
            result = await atlas.safety.guard(req, tool)
            console.print(result.output if result.ok else f"[red]{result.error}[/]")
        except Exception as exc:
            console.print(f"[red]{type(exc).__name__}[/] {exc}")
        await atlas.close()
    _run(go())


@app.command("remember")
def remember(text: str, kind: str = "fact") -> None:
    """Directly add a semantic fact (Tier-1 explicit user edit)."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        from atlas.memory.types import FactKind
        fid = await atlas.semantic.add_fact(text, FactKind(kind), confidence=1.0,
                                            salience=0.7, sources=())
        console.print(f"[green]remembered[/] {fid}")
        await atlas.close()
    _run(go())


@app.command("recall")
def recall(query: str) -> None:
    """Show what memory would surface for a query (inspect retrieval)."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        ctx = await atlas.retriever.retrieve(query)
        console.print(ctx.render())
        console.print(f"[dim]~{ctx.token_estimate} tokens[/]")
        await atlas.close()
    _run(go())


@app.command("consolidate")
def consolidate() -> None:
    """Run the distillation loop manually (nightly job in Phase 8)."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        stats = await atlas.consolidator.run()
        console.print(f"[green]consolidated[/] {stats}")
        await atlas.close()
    _run(go())


@app.command("prune")
def prune() -> None:
    """Run auto-cleaning manually (scheduled in Phase 8)."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        stats = await atlas.pruner.run()
        console.print(f"[green]pruned[/] {stats}")
        await atlas.close()
    _run(go())


@app.command("user-model")
def user_model_set(section: str, content: str) -> None:
    """Edit an always-loaded user-model section."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        await atlas.user_model.set_section(section, content)
        console.print(f"[green]updated[/] {section}")
        await atlas.close()
    _run(go())


@app.command("run-tool")
def run_tool(
    tool: str, operation: str,
    arg: list[str] = typer.Option([], "--arg", help="key=value, repeatable"),  # noqa: B008
) -> None:
    args: dict[str, Any] = {}
    for a in arg:
        k, _, v = a.partition("=")
        args[k] = v

    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        req = ToolRequest(correlation_id=atlas.ids.correlation_id(),
                          tool=tool, operation=operation, args=args)
        try:
            result = await atlas.safety.guard(req, EchoTool())
            console.print(f"[green]OK[/] {result.output}")
        except HaltedError as exc:
            console.print(f"[red]HALTED[/] {exc}")
        except DeniedError as exc:
            console.print(f"[red]DENIED[/] tier={exc.decision.tier.name} :: {exc.decision.reason}")
        await atlas.close()
    _run(go())


@app.command()
def model(prompt: str, deep: bool = typer.Option(False)) -> None:
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        req = ModelRequest(correlation_id=atlas.ids.correlation_id(),
                           prompt=prompt, needs_deep_reasoning=deep)
        resp = await atlas.gateway.complete(req)
        console.print(f"[dim]{resp.model} · {resp.target.name} · {resp.latency_ms}ms[/]")
        console.print(resp.text)
        await atlas.close()
    _run(go())


@app.command("know")
def know(query: str, official_only: bool = typer.Option(False)) -> None:
    """Obtain knowledge from memory + official + web, ranked with confidence."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        from atlas.capabilities.domain.knowledge import KnowledgeQuery
        ans = await atlas.knowledge_platform.obtain_knowledge(
            KnowledgeQuery(text=query, prefer_official=True), atlas.ids.correlation_id())
        console.print(ans.text)
        console.print(f"[dim]confidence {ans.confidence.score:.2f} ({ans.confidence.basis}) · "
                      f"{len(ans.sources)} sources[/]")
        for s in ans.sources[:5]:
            console.print(f"  [{s.provenance.source_kind.value}] {s.title} {s.url or ''}")
        await atlas.close()
    _run(go())


@app.command()
def kill() -> None:
    async def go() -> None:
        atlas = await build()
        atlas.killswitch.trip()
        console.print("[red bold]KILL SWITCH TRIPPED[/] — STOP.flag created")
        await atlas.close()
    _run(go())


@app.command()
def revive() -> None:
    async def go() -> None:
        atlas = await build()
        atlas.killswitch.reset()
        console.print("[green]kill switch cleared[/]")
        await atlas.close()
    _run(go())


@app.command("audit")
def audit_tail(limit: int = 30) -> None:
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        rows = await atlas.audit.tail(limit)
        table = Table("ts", "actor", "action", "tool", "tier", "decision", "outcome")
        for r in rows:
            table.add_row(
                str(r.get("ts", ""))[11:19], str(r.get("actor", "")), str(r.get("action", "")),
                str(r.get("tool") or ""), str(r.get("tier") if r.get("tier") is not None else ""),
                str(r.get("decision") or ""), str(r.get("outcome") or ""),
            )
        console.print(table)
        console.print(f"[dim]cost today: ${await atlas.audit.cost_today():.4f}[/]")
        await atlas.close()
    _run(go())


@app.command("run")
def run_task(request: str) -> None:
    """Execute a task through the orchestration runtime."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        event = InboundEvent(
            correlation_id=atlas.ids.correlation_id(),
            source="cli",
            content=request,
        )
        try:
            result = await atlas.orchestrator.run(event)
            if result.ok:
                console.print(f"[green]Completed in {result.steps_taken} steps[/]")
                console.print(result.answer)
            else:
                console.print(f"[red]Failed in {result.steps_taken} steps: {result.error}[/]")
        except Exception as exc:
            console.print(f"[red]Error:[/] {exc}")
        finally:
            await atlas.close()
    _run(go())


@app.command("cal")
def cal(
    action: str = typer.Argument(..., help="list|free|search|create"),
    query: str = typer.Option("", help="search query"),
    start: str = typer.Option("", help="start datetime (ISO)"),
    end: str = typer.Option("", help="end datetime (ISO)"),
    title: str = typer.Option("", help="event title"),
    to: str = typer.Option("", help="comma-separated attendee emails"),
    minutes: int = typer.Option(30, help="min free slot length in minutes"),
) -> None:
    """Calendar: list / free-busy / search / create events."""
    async def go() -> None:
        from datetime import datetime, timezone
        atlas = await build()
        await atlas.db.start()
        cp = atlas.calendar_platform

        def _parse(s: str) -> datetime:
            if not s:
                return datetime.now(timezone.utc)
            return datetime.fromisoformat(s) if "+" in s or s.endswith("Z") \
                else datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

        s_dt = _parse(start)
        e_dt = _parse(end) if end else _parse(start) if start else \
            datetime.now(timezone.utc).replace(hour=23, minute=59)

        if action == "list":
            for ev in await cp.list_events(start=s_dt, end=e_dt):
                console.print(f"  {ev.when.render()}  {ev.title}")
        elif action == "free":
            for slot in await cp.find_free_slots(start=s_dt, end=e_dt, min_minutes=minutes):
                console.print(f"  free: {slot.start:%a %H:%M}–{slot.end:%H:%M}")
        elif action == "search":
            for ev in await cp.search(query, limit=10):
                console.print(f"  {ev.when.render()}  {ev.title}")
        elif action == "create":
            from atlas.capabilities.domain.calendar import Attendee, EventDraft, EventTime
            draft = EventDraft(
                title=title,
                when=EventTime(start_dt=s_dt, end_dt=e_dt),
                attendees=tuple(Attendee(email=a.strip()) for a in to.split(",") if a.strip()),
            )
            try:
                eid = await cp.commit(draft, atlas.ids.correlation_id())
                console.print(f"[green]created[/] {eid}")
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")
        else:
            console.print(f"[red]unknown action:[/] {action}")
        await atlas.close()
    _run(go())


@app.command("contacts")
def contacts(
    action: str = typer.Argument("search", help="search"),
    query: str = typer.Argument("", help="search query"),
) -> None:
    """Contacts: search your contacts."""
    async def go() -> None:
        atlas = await build()
        await atlas.db.start()
        for c in await atlas.contacts_platform.search(query, limit=10):
            primary = c.primary_email() or ""
            console.print(f"  {c.name}  {primary}  {c.org or ''}")
        await atlas.close()
    _run(go())


if __name__ == "__main__":
    app()
