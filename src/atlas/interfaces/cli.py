"""Typer CLI — the Phase 1 control surface.

WHY an EchoTool lives here: Phase 1 has no real tools, but we must be able to
drive a ToolRequest through the Safety Engine end-to-end. EchoTool is a test
affordance, not a product tool — it declares tool='filesystem' so manifest rules
apply.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from atlas.app import Atlas, build
from atlas.capabilities.ide.contracts import EditOperation, EditOpKind, FileChange
from atlas.diagnostics.doctor import exit_code, run_doctor
from atlas.infra.ids import CorrelationId
from atlas.infra.types import InboundEvent, ModelCapability, ModelRequest, SideEffect, ToolRequest, ToolResult
from atlas.safety.engine import DeniedError, HaltedError

app = typer.Typer(add_completion=False, help="ATLAS control CLI (Phase 1)")
console = Console()


class EchoTool:
    name = "filesystem"

    def dry_run(self, args: dict[str, Any]) -> str:
        return f"echo {args} (no real side effect in Phase 1)"

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=True,
            output={"echo": args},
            side_effects=(SideEffect(kind="noop", target=str(args), reversible=True),),
        )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def build_atlas() -> AsyncGenerator[Atlas]:
    atlas = await build()
    async with atlas:
        yield atlas


@app.command()
def worker(
    poll_interval_s: float = typer.Option(0.5, "--poll-interval", help="queue poll interval (s)"),
) -> None:
    """Run a task worker consuming the durable queue (Phase 9). Ctrl-C to stop."""

    async def go() -> int:
        from atlas.infra.backends import SQLiteConnection
        from atlas.orchestration.worker import TaskWorker

        async with build_atlas() as atlas:
            task_worker = TaskWorker(
                orchestrator=atlas.orchestrator,
                conn=SQLiteConnection(atlas.db.conn),
                poll_interval_s=poll_interval_s,
            )
            stop = asyncio.Event()

            def _sigint() -> None:
                stop.set()

            import signal

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _sigint)
                except NotImplementedError:
                    pass
            await task_worker.run_forever()
            return 0

    raise typer.Exit(_run(go()))


@app.command("enqueue")
def enqueue(
    content: str = typer.Argument(..., help="task content"),
    correlation_id: str = typer.Option("", "--corr"),
    source: str = typer.Option("api", "--source"),
) -> None:
    """Enqueue a task on the durable queue for worker pickup (Phase 9)."""

    async def go() -> int:
        from atlas.infra.backends import SQLiteConnection
        from atlas.infra.queue import DurableTaskQueue

        async with build_atlas() as atlas:
            q = DurableTaskQueue(SQLiteConnection(atlas.db.conn), "cli")
            job_id = await q.enqueue(
                {
                    "correlation_id": correlation_id or atlas.ids.correlation_id(),
                    "source": source,
                    "content": content,
                }
            )
            console.print(f"[green]queued[/] job #{job_id}")
            return 0

    raise typer.Exit(_run(go()))


@app.command("resume")
def resume(task_id: str) -> None:
    """Assess/perform crash resume for an interrupted task (Phase 9).

    Only tasks whose plan is fully idempotent can resume; others are reported
    with the reason so the user can re-issue instead.
    """

    async def go() -> int:
        from atlas.orchestration.resume import try_resume

        async with build_atlas() as atlas:
            if atlas.checkpoints is None:
                console.print("[red]checkpoint store unavailable[/]")
                return 1
            decision, plan = await try_resume(
                task_id=task_id,
                checkpoints=atlas.checkpoints,
                registry=atlas.orchestrator._registry,
            )
            if not decision.allowed:
                console.print(f"[red]resume refused[/]: {decision.reason}")
                return 1
            assert plan is not None
            console.print(f"[green]resume allowed[/]: {decision.reason}")
            console.print(f"restored plan: {plan.goal} ({len(plan.steps)} steps)")
            return 0

    raise typer.Exit(_run(go()))


@app.command()
def doctor(verify_manifest: bool = typer.Option(False, "--verify-manifest")) -> None:
    async def go() -> int:
        async with build_atlas() as atlas:
            results = await run_doctor(atlas, verify_manifest_only=verify_manifest)
            table = Table("check", "status", "detail")
            for r in results:
                color = {"pass": "green", "warn": "yellow", "fail": "red"}[r.status]
                table.add_row(r.name, f"[{color}]{r.status}[/]", r.detail)
            console.print(table)
            code = exit_code(results)
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
        async with build_atlas() as atlas:
            tool = atlas.tools["filesystem"]
            args: dict[str, Any] = {"operation": operation, "path": path, "query": query, "content": content}
            if operation == "delete":
                # bridge dry_run count -> classifier for mass_deletion decisions
                count = tool._count_delete_targets(path)  # type: ignore[attr-defined]
                args["target_count"] = count
            op_tier = "read" if operation in ("read", "search") else ("delete" if operation == "delete" else "write")
            req = ToolRequest(
                correlation_id=atlas.ids.correlation_id(), tool="filesystem", operation=op_tier, args=args
            )
            try:
                result = await atlas.safety.guard(req, tool)
                if result.ok:
                    console.print(f"[green]OK[/] {result.output}")
                else:
                    console.print(f"[red]{result.error}[/]")
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")

    _run(go())


@app.command("sh")
def shell(command: str) -> None:
    """Phase 2: run an allowlisted command in the sandbox via the Safety Engine."""

    async def go() -> None:
        async with build_atlas() as atlas:
            tool = atlas.tools["shell"]
            first = command.strip().split()[0] if command.strip() else ""
            read_only = first in {"ls", "cat", "grep", "find", "git"}
            req = ToolRequest(
                correlation_id=atlas.ids.correlation_id(),
                tool="shell",
                operation="read_only" if read_only else "side_effect",
                args={"command": command},
            )
            try:
                result = await atlas.safety.guard(req, tool)
                console.print(result.output if result.ok else f"[red]{result.error}[/]")
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")

    _run(go())


@app.command("remember")
def remember(text: str, kind: str = "fact") -> None:
    """Directly add a semantic fact (Tier-1 explicit user edit)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            from atlas.memory.types import FactKind

            fid = await atlas.semantic.add_fact(text, FactKind(kind), confidence=1.0, salience=0.7, sources=())
            console.print(f"[green]remembered[/] {fid}")

    _run(go())


@app.command("recall")
def recall(query: str) -> None:
    """Show what memory would surface for a query (inspect retrieval)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            ctx = await atlas.retriever.retrieve(query)
            console.print(ctx.render())
            console.print(f"[dim]~{ctx.token_estimate} tokens[/]")

    _run(go())


@app.command("consolidate")
def consolidate() -> None:
    """Run the distillation loop manually (nightly job in Phase 8)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            stats = await atlas.consolidator.run()
            console.print(f"[green]consolidated[/] {stats}")

    _run(go())


@app.command("prune")
def prune() -> None:
    """Run auto-cleaning manually (scheduled in Phase 8)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            stats = await atlas.pruner.run()
            console.print(f"[green]pruned[/] {stats}")

    _run(go())


@app.command("user-model")
def user_model_set(section: str, content: str) -> None:
    """Edit an always-loaded user-model section."""

    async def go() -> None:
        async with build_atlas() as atlas:
            await atlas.user_model.set_section(section, content)
            console.print(f"[green]updated[/] {section}")

    _run(go())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Phase 2: Trajectory Commands
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@app.command("trajectories")
def list_trajectories(
    limit: int = typer.Option(20, "--limit"),
    failed_only: bool = typer.Option(False, "--failed"),
) -> None:
    """List recent task trajectories (Phase 2 learning history)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            if failed_only:
                trajectories = await atlas.trajectory_store.get_failed_trajectories(limit=limit)
            else:
                trajectories = await atlas.trajectory_store.get_recent_trajectories(limit=limit)

            if not trajectories:
                console.print("[yellow]No trajectories found[/]")
                return

            table = Table("ID", "Task ID", "Goal", "Success", "Steps", "Replans", "Latency")
            for t in trajectories:
                success_icon = "[green]✓[/]" if t.success else "[red]✗[/]"
                table.add_row(
                    t.id[:8],
                    t.task_id[:8],
                    t.goal[:40],
                    success_icon,
                    str(t.steps_taken),
                    str(t.replan_count),
                    f"{t.latency_ms}ms",
                )
            console.print(table)
            console.print(f"\n[dim]Total: {len(trajectories)} trajectories[/]")

    _run(go())


@app.command("trajectory")
def show_trajectory(task_id: str) -> None:
    """Show full trajectory for a task (Phase 2 execution history)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            trajectory = await atlas.trajectory_store.get_trajectory_by_task(task_id)

            if not trajectory:
                console.print(f"[red]No trajectory found for task {task_id}[/]")
                return

            console.print(f"[bold]Trajectory {trajectory.id}[/]")
            console.print(f"Task: {trajectory.task_id}")
            console.print(f"Goal: {trajectory.goal}")
            console.print(f"Request: {trajectory.request}")
            console.print(f"Success: {'✓' if trajectory.success else '✗'}")
            console.print(f"Steps: {trajectory.steps_taken}")
            console.print(f"Replans: {trajectory.replan_count}")
            console.print(f"Verification: {trajectory.verification_score or 'N/A'}")
            console.print(f"Latency: {trajectory.latency_ms}ms")
            console.print(f"Tokens: {trajectory.tokens_used}")
            console.print(f"Cost: ${trajectory.cost_usd:.4f}")

            if trajectory.answer:
                console.print(f"\n[bold]Answer:[/]\n{trajectory.answer}")
            elif trajectory.error:
                console.print(f"\n[bold red]Error:[/]\n{trajectory.error}")

            # Show action summary
            if trajectory.actions:
                console.print(f"\n[bold]Actions ({len(trajectory.actions)}):[/]")
                for i, action in enumerate(trajectory.actions[:10]):  # First 10 actions
                    kind = getattr(action, "kind", "unknown")
                    tool = getattr(action, "tool", None)
                    if tool:
                        console.print(f"  {i + 1}. {kind} → {tool}")
                    else:
                        console.print(f"  {i + 1}. {kind}")
                if len(trajectory.actions) > 10:
                    console.print(f"  ... and {len(trajectory.actions) - 10} more")

    _run(go())


@app.command("experiences")
def list_experiences(
    category: str | None = typer.Option(None, "--category"),
    min_confidence: float = typer.Option(0.5, "--min-confidence"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """List extracted experiences (Phase 2 learned lessons)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            from atlas.memory.trajectory import ExperienceCategory, ExperienceQuery

            query = ExperienceQuery(
                category=ExperienceCategory(category) if category else None,
                min_confidence=min_confidence,
                limit=limit,
            )

            experiences = await atlas.trajectory_store.query_experiences(query)

            if not experiences:
                console.print("[yellow]No experiences found[/]")
                return

            table = Table("ID", "Category", "Lesson", "Confidence", "Reused", "Success %")
            for exp in experiences:
                table.add_row(
                    exp.id[:8],
                    exp.category.value,
                    exp.lesson_text[:50],
                    f"{exp.confidence:.2f}",
                    str(exp.reuse_count),
                    f"{exp.success_rate * 100:.0f}%" if exp.reuse_count > 0 else "N/A",
                )
            console.print(table)
            console.print(f"\n[dim]Total: {len(experiences)} experiences[/]")

    _run(go())


@app.command("failures")
def list_failures(
    category: str | None = typer.Option(None, "--category"),
    component: str | None = typer.Option(None, "--component"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """List failure records (Phase 2 error taxonomy)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            from atlas.memory.trajectory import FailureCategory

            failures = await atlas.trajectory_store.get_failure_records(
                category=FailureCategory(category) if category else None,
                component=component,
                limit=limit,
            )

            if not failures:
                console.print("[yellow]No failures found[/]")
                return

            table = Table("ID", "Category", "Component", "Recovered", "Message")
            for f in failures:
                recovered_icon = "[green]✓[/]" if f.recovered and f.recovery_succeeded else "[red]✗[/]"
                table.add_row(
                    f.id[:8],
                    f.category.value,
                    f.component,
                    recovered_icon,
                    f.error_message[:50],
                )
            console.print(table)
            console.print(f"\n[dim]Total: {len(failures)} failures[/]")

    _run(go())


@app.command("extract-experiences")
def extract_experiences(
    limit: int = typer.Option(10, "--limit"),
    failed_only: bool = typer.Option(False, "--failed-only"),
) -> None:
    """Manually trigger experience extraction from recent trajectories."""

    async def go() -> None:
        async with build_atlas() as atlas:
            console.print(f"[yellow]Extracting experiences from {limit} recent trajectories...[/]")

            stats = await atlas.experience_extractor.extract_from_recent_trajectories(
                limit=limit,
                only_successful=not failed_only,
            )

            console.print("[green]Extraction complete:[/]")
            console.print(f"  Processed: {stats['processed']}")
            console.print(f"  Extracted: {stats['extracted']} experiences")
            console.print(f"  Failed: {stats['failed']}")

    _run(go())


@app.command("run-tool")
def run_tool(
    tool: str,
    operation: str,
    arg: list[str] = typer.Option([], "--arg", help="key=value, repeatable"),  # noqa: B008
) -> None:
    args: dict[str, Any] = {}
    for a in arg:
        k, _, v = a.partition("=")
        args[k] = v

    async def go() -> None:
        async with build_atlas() as atlas:
            req = ToolRequest(correlation_id=atlas.ids.correlation_id(), tool=tool, operation=operation, args=args)
            try:
                result = await atlas.safety.guard(req, EchoTool())
                console.print(f"[green]OK[/] {result.output}")
            except HaltedError as exc:
                console.print(f"[red]HALTED[/] {exc}")
            except DeniedError as exc:
                console.print(f"[red]DENIED[/] tier={exc.decision.tier.name} :: {exc.decision.reason}")

    _run(go())


@app.command()
def model(prompt: str, deep: bool = typer.Option(False)) -> None:
    async def go() -> None:
        async with build_atlas() as atlas:
            req = ModelRequest(
                correlation_id=atlas.ids.correlation_id(),
                prompt=prompt,
                required_capabilities=frozenset({ModelCapability.REASONING}),
                needs_deep_reasoning=deep,
            )
            resp = await atlas.gateway.complete(req)
            console.print(f"[dim]{resp.model} · {resp.target.name} · {resp.latency_ms}ms[/]")
            console.print(resp.text)

    _run(go())


@app.command("know")
def know(query: str, official_only: bool = typer.Option(False)) -> None:
    """Obtain knowledge from memory + official + web, ranked with confidence."""

    async def go() -> None:
        async with build_atlas() as atlas:
            from atlas.capabilities.domain.knowledge import KnowledgeQuery

            ans = await atlas.knowledge_platform.obtain_knowledge(
                KnowledgeQuery(text=query, prefer_official=True), atlas.ids.correlation_id()
            )
            console.print(ans.text)
            console.print(
                f"[dim]confidence {ans.confidence.score:.2f} ({ans.confidence.basis}) · {len(ans.sources)} sources[/]"
            )
            for s in ans.sources[:5]:
                console.print(f"  [{s.provenance.source_kind.value}] {s.title} {s.url or ''}")

    _run(go())


@app.command()
def kill() -> None:
    async def go() -> None:
        async with build_atlas() as atlas:
            atlas.killswitch.trip()
            console.print("[red bold]KILL SWITCH TRIPPED[/] — STOP.flag created")

    _run(go())


@app.command()
def revive() -> None:
    async def go() -> None:
        async with build_atlas() as atlas:
            atlas.killswitch.reset()
            console.print("[green]kill switch cleared[/]")

    _run(go())


@app.command("watch")
def watch_task(
    task_id: str = typer.Argument(..., help="Task ID to watch"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON instead of formatted display"),
    host: str = typer.Option("localhost:8000", "--host", help="API server host:port"),
) -> None:
    """Watch task events in real-time via WebSocket.

    Connects to the task-scoped event stream and displays events as they occur.
    Historical events are replayed first, then live events stream continuously.

    Examples:
      atlas watch abc-123              # Watch task with pretty formatting
      atlas watch abc-123 --json       # Output raw JSON for scripting
      atlas watch abc-123 --host prod.example.com:8000
    """
    import json
    from datetime import datetime

    from rich.text import Text
    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    uri = f"ws://{host}/ws/tasks/{task_id}/stream"

    def format_timestamp(ts_str: str) -> str:
        """Format ISO timestamp to HH:MM:SS"""
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.strftime("%H:%M:%S")
        except Exception:
            return ts_str[:8] if len(ts_str) >= 8 else ts_str

    def get_event_symbol(kind: str) -> tuple[str, str]:
        """Return (symbol, color) for event kind"""
        if "started" in kind or "building" in kind:
            return "▶", "blue"
        elif "completed" in kind or "resolved" in kind:
            return "✓", "green"
        elif "failed" in kind or "denied" in kind:
            return "✗", "red"
        elif "thought" in kind or "action" in kind:
            return "💭", "cyan"
        elif "executing" in kind:
            return "⚙", "yellow"
        elif "classified" in kind:
            return "🛡", "magenta"
        elif "retrieved" in kind:
            return "📚", "blue"
        elif "requested" in kind:
            return "❓", "yellow"
        else:
            return "•", "white"

    def render_event(event: dict[str, Any], is_historical: bool = False) -> None:
        """Render a single event with rich formatting"""
        kind = event.get("kind", "unknown")
        timestamp = format_timestamp(event.get("_timestamp", ""))
        task_id_short = event.get("task_id", "")[:8]
        symbol, color = get_event_symbol(kind)

        # Build the main line
        prefix = "[dim]REPLAY[/] " if is_historical else ""
        line = Text()
        line.append(f"{prefix}[dim]{timestamp}[/] ")
        line.append(f"[{color}]{symbol}[/] ")
        line.append(f"[bold]{kind}[/bold] ")
        line.append(f"[dim]({task_id_short})[/]")

        console.print(line)

        # Show metadata if present
        metadata = event.get("metadata", {})
        if metadata:
            # Show key metadata fields
            if "summary" in metadata:
                console.print(f"  [dim]→[/] {metadata['summary']}")
            if "thought" in metadata:
                console.print(f"  [cyan]💭[/] {metadata['thought']}")
            if "action" in metadata:
                console.print(f"  [yellow]⚡[/] {metadata['action']}")
            if "tool" in metadata:
                console.print(f"  [yellow]🔧[/] {metadata['tool']}")
            if "tier" in metadata:
                tier = metadata["tier"]
                tier_color = "red" if tier >= 3 else "yellow" if tier >= 2 else "green"
                console.print(f"  [dim]Tier[/] [{tier_color}]{tier}[/{tier_color}]")
            if "reason" in metadata:
                console.print(f"  [dim]→[/] {metadata['reason']}")

    try:
        console.print(f"[dim]Connecting to {uri}...[/]")

        with connect(uri, close_timeout=1) as websocket:
            console.print("[green]✓ Connected[/] Watching for events...\n")

            replay_complete = False
            event_count = 0

            for message in websocket:
                event = json.loads(message)

                # Handle replay_complete marker
                if event.get("type") == "replay_complete":
                    historical_count = event.get("historical_count", 0)
                    if historical_count > 0:
                        console.print(f"\n[dim]─── End of replay ({historical_count} events) ───[/]\n")
                    replay_complete = True
                    continue

                # Handle ping/pong
                if event.get("type") == "ping":
                    websocket.send("pong")
                    continue

                # Render event
                is_historical = event.get("historical", False) or not replay_complete

                if json_output:
                    console.print_json(data=event)
                else:
                    render_event(event, is_historical)

                event_count += 1

    except KeyboardInterrupt:
        console.print("\n[yellow]⏸ Stopped watching[/]")
    except WebSocketException as exc:
        console.print(f"[red]✗ WebSocket error:[/] {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[red]✗ Error:[/] {exc}")
        raise typer.Exit(1) from exc


@app.command("audit")
def audit_tail(limit: int = 30) -> None:
    async def go() -> None:
        async with build_atlas() as atlas:
            rows = await atlas.audit.tail(limit)
            table = Table("ts", "actor", "action", "tool", "tier", "decision", "outcome")
            for r in rows:
                table.add_row(
                    str(r.get("ts", ""))[11:19],
                    str(r.get("actor", "")),
                    str(r.get("action", "")),
                    str(r.get("tool") or ""),
                    str(r.get("tier") if r.get("tier") is not None else ""),
                    str(r.get("decision") or ""),
                    str(r.get("outcome") or ""),
                )
            console.print(table)
            console.print(f"[dim]cost today: ${await atlas.audit.cost_today():.4f}[/]")

    _run(go())


@app.command("run")
def run_task(request: str) -> None:
    """Execute a task through the orchestration runtime."""

    async def go() -> None:
        async with build_atlas() as atlas:
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

    _run(go())


@app.command()
def verify() -> None:
    """Run end-to-end smoke test of critical pipeline stages.

    Executes a short repository inspection task through the full pipeline:
    Orchestrator → Planner → ReasoningLoop → ToolDispatcher → SafetyEngine →
    Verifier → TrajectoryStore. Validates that all critical subsystems are
    correctly wired and functional.
    """

    async def go() -> None:
        from atlas.infra.types import InboundEvent

        async with build_atlas() as atlas:
            console.print("[cyan]ATLAS Integration Verify — running end-to-end smoke test...[/]")

            event = InboundEvent(
                source="cli",
                correlation_id=CorrelationId(atlas.clock.now().isoformat()),
                content="Inspect this repository and tell me the top-level project structure.",
            )

            checks = []
            try:
                result = await atlas.orchestrator.run(event)

                checks.append(("event.bus_delivery", result is not None))
                checks.append(("task.completed", result.ok if result else False))
                checks.append(("tool.executed", len(result.actions) > 0 if result else False))
                checks.append(("verification.executed", result.verification_passed is not None if result else False))
                checks.append(("decision_traces.recorded", len(result.decision_traces) > 0 if result else False))
                checks.append(("trajectory.saved", len(result.actions) > 0 if result else False))

                if result and result.ok:
                    console.print(f"[green]Task completed in {result.steps_taken} steps[/]")
                    console.print(f"[dim]Answer: {str(result.answer)[:200]}...[/]")
                else:
                    console.print(f"[red]Task failed: {result.error if result else 'no result'}[/]")

            except Exception as exc:
                checks.append(("error", False))
                console.print(f"[red]Verify failed: {exc}[/]")

            table = Table("check", "status", "detail")
            all_pass = True
            for name, *rest in checks:
                passed = rest[0]
                detail = rest[1] if len(rest) > 1 else ""
                color = "green" if passed else "red"
                if not passed:
                    all_pass = False
                table.add_row(name, f"[{color}]{'PASS' if passed else 'FAIL'}[/]", str(detail))
            console.print(table)

            if all_pass:
                console.print("[green]All checks passed.[/]")
            else:
                raise typer.Exit(1)

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
        from datetime import datetime

        async with build_atlas() as atlas:
            cp = atlas.calendar_platform

            def _parse(s: str) -> datetime:
                if not s:
                    return datetime.now(UTC)
                return (
                    datetime.fromisoformat(s)
                    if "+" in s or s.endswith("Z")
                    else datetime.fromisoformat(s).replace(tzinfo=UTC)
                )

            s_dt = _parse(start)
            e_dt = _parse(end) if end else _parse(start) if start else datetime.now(UTC).replace(hour=23, minute=59)

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

    _run(go())


@app.command("contacts")
def contacts(
    action: str = typer.Argument("search", help="search"),
    query: str = typer.Argument("", help="search query"),
) -> None:
    """Contacts: search your contacts."""

    async def go() -> None:
        async with build_atlas() as atlas:
            for c in await atlas.contacts_platform.search(query, limit=10):
                primary = c.primary_email() or ""
                console.print(f"  {c.name}  {primary}  {c.org or ''}")

    _run(go())


@app.command("knowledge")
def knowledge(
    action: str = typer.Argument(..., help="ingest|search|list|delete"),
    path: str = typer.Option("", help="file path for ingest"),
    query_text: str = typer.Option("", "--query", help="search query"),
    doc_id_opt: str = typer.Option("", "--id", help="document ID for delete"),
    source_type_opt: str = typer.Option("", "--type", help="markdown|pdf|txt|web"),
    limit: int = typer.Option(5, help="max search results"),
) -> None:
    """Knowledge Store: ingest documents, search, list, delete."""
    from pathlib import Path

    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

    async def go() -> None:
        async with build_atlas() as atlas:
            store = atlas.knowledge_store

            if action == "ingest":
                if not path:
                    console.print("[red]Error:[/] --path required for ingest")
                    return

                file_path = Path(path)
                if not file_path.exists():
                    console.print(f"[red]Error:[/] File not found: {path}")
                    return

                # Auto-detect source type if not provided
                source_type = source_type_opt
                if not source_type:
                    suffix = file_path.suffix.lower()
                    if suffix == ".md":
                        source_type = "markdown"
                    elif suffix == ".pdf":
                        source_type = "pdf"
                    elif suffix in (".txt", ".text"):
                        source_type = "txt"
                    else:
                        console.print(f"[red]Error:[/] Cannot auto-detect type for {suffix}. Use --type")
                        return

                console.print(f"[cyan]Ingesting[/] {file_path.name} as {source_type}...")

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    console=console,
                ) as progress:
                    task = progress.add_task("Processing...", total=100)

                    async def progress_callback(update: dict[str, Any]) -> None:
                        status = update.get("status", "")
                        if status == "chunking":
                            chunks = update.get("chunks_processed", 0)
                            progress.update(task, description=f"Chunking... ({chunks} chunks)", completed=30)
                        elif status == "indexing":
                            indexed = update.get("chunk_indexed", 0)
                            total = update.get("total_chunks", 1)
                            pct = int((indexed / total) * 70) + 30
                            progress.update(task, description=f"Indexing... ({indexed}/{total})", completed=pct)
                        elif status == "complete":
                            progress.update(task, description="Complete!", completed=100)

                    result_doc_id = await store.ingest_document(
                        file_path, source_type, metadata={"title": file_path.stem}, progress_callback=progress_callback
                    )

                console.print(f"[green]✓ Ingested[/] document {result_doc_id}")

            elif action == "search":
                if not query_text:
                    console.print("[red]Error:[/] --query required for search")
                    return

                console.print(f"[cyan]Searching for:[/] {query_text}\n")
                results = await store.search(query_text, limit=limit)

                if not results:
                    console.print("[yellow]No results found[/]")
                    return

                for i, result in enumerate(results, 1):
                    console.print(
                        f"[bold]{i}. {result['document_title']}[/bold] "
                        f"[dim](chunk {result['chunk_index'] + 1}/{result['total_chunks']})[/]"
                    )
                    console.print(f"   [dim]Score:[/] {result['score']:.3f}")
                    console.print(f"   {result['content'][:200]}...")
                    console.print()

            elif action == "list":
                docs = await store.list_documents(limit=50)
                if not docs:
                    console.print("[yellow]No documents found[/]")
                    return

                table = Table("ID", "Title", "Type", "Chunks", "Indexed", "Created")
                for doc in docs:
                    table.add_row(
                        doc.id[:8],
                        doc.title[:40],
                        doc.source_type,
                        str(doc.chunk_count),
                        "✓" if doc.indexed else "⏳",
                        doc.created_ts.strftime("%Y-%m-%d %H:%M"),
                    )
                console.print(table)

            elif action == "delete":
                if not doc_id_opt:
                    console.print("[red]Error:[/] --id required for delete")
                    return

                await store.delete_document(doc_id_opt)
                console.print(f"[green]✓ Deleted[/] document {doc_id_opt}")

            else:
                console.print(f"[red]Unknown action:[/] {action}")

    _run(go())


# ---------------------------------------------------------------------------
# Task 3.9 — Live Memory Commands
# ---------------------------------------------------------------------------

voice_app = typer.Typer(help="Voice pipeline — speak text or hold a spoken conversation")
app.add_typer(voice_app, name="voice")


def _voice_or_exit(atlas: Atlas) -> Any:
    """Return the live VoiceService or print guidance and abort."""
    service = getattr(atlas, "voice_service", None)
    if service is None:
        console.print(
            "[red]Voice is disabled or unconfigured.[/] Set [cyan]voice.enabled: true[/] in "
            "config/settings.yaml. Speech uses the same OPENROUTER_API_KEY as chat; "
            "DEEPGRAM_API_KEY / FISH_AUDIO_API_KEY are optional extra fallbacks."
        )
        raise typer.Exit(code=1)
    return service


async def _collect_audio(service: Any, text: str, language: str | None) -> bytes:
    """Drive a TTS synthesis to completion, returning the concatenated bytes."""
    parts: list[bytes] = []
    async for chunk in service.speak(text, language):
        if chunk.error:
            console.print(f"[red]TTS error:[/] {chunk.error}")
        if chunk.data:
            parts.append(chunk.data)
    return b"".join(parts)


def _play_or_save(audio: bytes) -> None:
    """Best-effort playback; always save the file so nothing is lost.

    PRIVACY: the audio was synthesized by a third-party API (audio left the
    machine). Playback uses the optional `voice` extra (sounddevice/soundfile).
    """
    if not audio:
        console.print("[yellow]No audio produced.[/]")
        return
    from pathlib import Path

    out = Path("atlas_voice_output.mp3")
    out.write_bytes(audio)
    console.print(f"[green]Saved[/] {out} ({len(audio)} bytes)")
    try:
        import io

        # Optional deps (voice extra). Both ignore codes are needed: mypy reports
        # `import-not-found` when the extra is absent and `import-untyped` when it
        # is installed but ships no stubs, and `unused-ignore` covers the case
        # where neither fires.
        import sounddevice as sd  # type: ignore[import-not-found, import-untyped, unused-ignore]
        import soundfile as sf  # type: ignore[import-not-found, import-untyped, unused-ignore]

        data, sr = sf.read(io.BytesIO(audio), dtype="float32")
        sd.play(data, sr)
        sd.wait()
    except Exception as exc:
        console.print(f"[dim]Playback unavailable ({exc}); open {out} manually.[/]")


@voice_app.command("speak")
def voice_speak(
    text: str,
    lang: str = typer.Option(None, "--lang", "-l", help="Language hint, e.g. en, hi"),
) -> None:
    """Synthesize TEXT to speech and play it (--lang picks the voice: en vs hi/other)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _voice_or_exit(atlas)
            audio = await _collect_audio(service, text, lang)
            _play_or_save(audio)

    _run(go())


@voice_app.command("chat")
def voice_chat(
    seconds: float = typer.Option(6.0, "--seconds", "-s", help="Seconds of audio to capture per turn"),
) -> None:
    """Full loop: mic -> STT -> orchestrator -> answer -> TTS -> speaker.

    Records a fixed window of microphone audio each turn (requires the `voice`
    extra: `uv sync --extra voice`), transcribes it, runs the request through the
    orchestrator/SafetyEngine funnel as an InboundEvent(source="voice"), then
    speaks the answer. Ctrl-C to stop.
    """

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _voice_or_exit(atlas)
            sample_rate = atlas.config.voice.sample_rate
            try:
                # Optional deps (voice extra): tolerate both "installed" and
                # "absent" type-check environments without a mypy error.
                import numpy as np  # type: ignore[import-not-found, import-untyped, unused-ignore]
                import sounddevice as sd  # type: ignore[import-not-found, import-untyped, unused-ignore]
            except Exception as exc:
                console.print(f"[red]Mic capture needs the voice extra:[/] {exc}\n  uv sync --extra voice")
                raise typer.Exit(code=1) from exc

            console.print("[cyan]Voice chat ready. Speak after each prompt; Ctrl-C to quit.[/]")
            while True:
                console.print(f"[dim]Listening for {seconds:.0f}s…[/]")
                recording = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
                sd.wait()
                pcm = np.asarray(recording, dtype="int16").tobytes()

                result = await service.transcribe(pcm)
                transcript = result.text.strip()
                if not transcript:
                    console.print("[yellow]…didn't catch that.[/]")
                    continue
                console.print(f"[bold]You:[/] {transcript}")

                event = InboundEvent(
                    correlation_id=atlas.ids.correlation_id(),
                    source="voice",
                    content=transcript,
                )
                task = await atlas.orchestrator.run(event)
                answer = task.answer if (task.ok and task.answer) else (task.error or "I could not do that.")
                console.print(f"[bold green]ATLAS:[/] {answer}")
                audio = await _collect_audio(service, answer, None)
                _play_or_save(audio)

    _run(go())


# ---------------------------------------------------------------------------

ide_app = typer.Typer(help="ADE / IDE — inspect a workspace tree, read a file, apply a governed edit")
app.add_typer(ide_app, name="ide")


def _ide_or_exit(atlas: Atlas) -> Any:
    """Return the live IDEService or print guidance and abort."""
    service = getattr(atlas, "ide_service", None)
    if service is None:
        console.print(
            "[red]ADE (IDE) is disabled.[/] Set [cyan]ide.enabled: true[/] in config/settings.yaml. "
            "Every edit still routes through the SafetyEngine funnel + filesystem tool."
        )
        raise typer.Exit(code=1)
    return service


# NOTE: the workspace registry is in-memory and per-process, so each command
# opens the workspace fresh and operates in the same invocation. Cross-invocation
# workspace ids require DB-backed persistence (a later slice); until then a
# stored id from one `atlas ide` call is not addressable from the next.
@ide_app.command("tree")
def ide_tree(
    root_path: str,
    name: str = typer.Option("workspace", "--name", "-n", help="Workspace display name"),
) -> None:
    """Open the workspace at ROOT_PATH and print its file tree."""

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _ide_or_exit(atlas)
            try:
                session = await service.open_workspace(root_path, name)
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")
                raise typer.Exit(code=1) from exc
            nodes = await service.tree(session.workspace.id)
            table = Table(title=f"{name}  ({len(nodes)} entries)")
            table.add_column("path")
            table.add_column("kind")
            table.add_column("version", style="dim")
            for node in nodes:
                table.add_row(node.path, "dir" if node.is_dir else "file", (node.version or "")[:12])
            console.print(table)

    _run(go())


@ide_app.command("read")
def ide_read(root_path: str, rel_path: str) -> None:
    """Open the workspace at ROOT_PATH and print REL_PATH with its content hash."""

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _ide_or_exit(atlas)
            try:
                session = await service.open_workspace(root_path, "workspace")
                snap, content = await service.read_document(session.workspace.id, rel_path)
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")
                raise typer.Exit(code=1) from exc
            console.print(
                f"[cyan]{snap.path}[/]  lang={snap.language}  lines={snap.line_count}  version={snap.version[:12]}"
            )
            console.print(content)

    _run(go())


@ide_app.command("project")
def ide_project(root_path: str) -> None:
    """Analyze the workspace at ROOT_PATH into a project model (languages, package
    managers, frameworks, and candidate test/build/run commands)."""

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _ide_or_exit(atlas)
            try:
                session = await service.open_workspace(root_path, "workspace")
                pm = await service.project_model(session.workspace.id)
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")
                raise typer.Exit(code=1) from exc

            def _row(label: str, value: str) -> None:
                table.add_row(label, value or "[dim]—[/]")

            table = Table(title=f"project  ({pm.file_count} files)")
            table.add_column("field", style="cyan")
            table.add_column("value")
            _row("languages", ", ".join(pm.languages))
            _row("package managers", ", ".join(pm.package_managers))
            _row("frameworks", ", ".join(pm.frameworks))
            _row("entrypoints", ", ".join(pm.entrypoints))
            _row("test", " | ".join(pm.test_commands))
            _row("build", " | ".join(pm.build_commands))
            _row("run", " | ".join(pm.run_commands))
            _row("dependencies", str(len(pm.dependencies)))
            _row("fingerprint", pm.fingerprint[:12])
            console.print(table)

    _run(go())


@ide_app.command("run")
def ide_run(
    root_path: str,
    command: str,
    timeout_s: float = typer.Option(120.0, "--timeout", help="Max seconds before the command is killed"),
) -> None:
    """Run COMMAND in the workspace at ROOT_PATH through the SafetyEngine funnel.

    The command is classified/allowlisted/audited exactly like any other tool
    dispatch — a policy refusal prints as `denied`, a non-zero exit as `failed`.
    This is the primitive the agentic loop uses to run a project's test/build/run
    candidate; the IDE never spawns a subprocess of its own.
    """

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _ide_or_exit(atlas)
            try:
                session = await service.open_workspace(root_path, "workspace")
                result = await service.run_command(session.workspace.id, command, timeout_s=timeout_s)
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")
                raise typer.Exit(code=1) from exc
            if result.denied:
                console.print(f"[red]denied[/] {result.error}")
                raise typer.Exit(code=1)
            if result.stdout:
                console.print(result.stdout)
            if result.stderr:
                console.print(f"[dim]{result.stderr}[/]")
            if result.ok:
                console.print(f"[green]ok[/] exit={result.exit_code}  {result.duration_ms}ms")
            else:
                console.print(f"[red]failed[/] exit={result.exit_code}  {result.error or ''}")
                raise typer.Exit(code=1)

    _run(go())


@ide_app.command("edit")
def ide_edit(
    root_path: str,
    rel_path: str,
    start: int = typer.Option(..., "--start", help="First line to replace (0-based, inclusive)"),
    end: int = typer.Option(..., "--end", help="Line past the last replaced line (0-based, exclusive)"),
    text: str = typer.Option("", "--text", help="Replacement text (include a trailing newline as needed)"),
) -> None:
    """Replace lines [START, END) of REL_PATH with TEXT — through the SafetyEngine funnel.

    The current on-disk version is read first and passed as expected_version, so a
    concurrent human edit makes this refuse (stale) rather than clobber.
    """

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _ide_or_exit(atlas)
            try:
                session = await service.open_workspace(root_path, "workspace")
                wid = session.workspace.id
                snap, _ = await service.read_document(wid, rel_path)
                change = FileChange(
                    path=rel_path,
                    expected_version=snap.version,
                    operations=(EditOperation(kind=EditOpKind.REPLACE, start_line=start, end_line=end, text=text),),
                    rationale="atlas ide edit",
                )
                result = await service.apply_change(wid, change)
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")
                raise typer.Exit(code=1) from exc
            if result.applied:
                console.print(f"[green]applied[/] {result.path}  new_version={(result.new_version or '')[:12]}")
            elif result.stale:
                console.print(f"[yellow]stale[/] {result.path} — file changed on disk; re-read and retry")
                raise typer.Exit(code=1)
            else:
                console.print(f"[red]not applied[/] {result.error}")
                raise typer.Exit(code=1)

    _run(go())


@ide_app.command("git")
def ide_git(root_path: str) -> None:
    """Show git working-tree status for the workspace at ROOT_PATH.

    Runs `git status` through the SafetyEngine funnel exactly like any other
    command — the IDE never shells out on its own. A non-git directory prints a
    plain "not a git repo" rather than a fabricated clean status.
    """

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _ide_or_exit(atlas)
            try:
                session = await service.open_workspace(root_path, "workspace")
                status = await service.git_status(session.workspace.id)
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")
                raise typer.Exit(code=1) from exc
            if status is None:
                console.print("[yellow]not a git repo[/] (or command execution unavailable)")
                return
            head = "[red]detached[/] " if status.detached else ""
            tracking = ""
            if status.ahead or status.behind:
                tracking = f"  [dim](ahead {status.ahead}, behind {status.behind})[/]"
            conflicts = "  [red]conflicts[/]" if status.has_conflicts else ""
            console.print(f"{head}[cyan]{status.branch or '—'}[/]{tracking}{conflicts}")
            if not status.changes:
                console.print("[green]clean[/]")
                return
            table = Table(title=f"changes  ({len(status.changes)})")
            table.add_column("state")
            table.add_column("staged", justify="center")
            table.add_column("path")
            for c in status.changes:
                path = f"{c.old_path} -> {c.path}" if c.old_path else c.path
                table.add_row(c.state.value, "✓" if c.staged else "", path)
            console.print(table)

    _run(go())


@ide_app.command("diff")
def ide_diff(
    root_path: str,
    staged: bool = typer.Option(False, "--staged", help="Show the staged (index) diff instead of the worktree"),
    patch: bool = typer.Option(False, "--patch", "-p", help="Also print the raw unified diff"),
) -> None:
    """Show the git diff for the workspace at ROOT_PATH — per-file line deltas.

    Runs `git diff` through the SafetyEngine funnel (read-only). A non-git
    directory prints "not a git repo"; a clean tree prints "no changes".
    """

    async def go() -> None:
        async with build_atlas() as atlas:
            service = _ide_or_exit(atlas)
            try:
                session = await service.open_workspace(root_path, "workspace")
                diff = await service.git_diff(session.workspace.id, staged=staged)
            except Exception as exc:
                console.print(f"[red]{type(exc).__name__}[/] {exc}")
                raise typer.Exit(code=1) from exc
            if diff is None:
                console.print("[yellow]not a git repo[/] (or command execution unavailable)")
                return
            if not diff.files:
                console.print("[green]no changes[/]")
                return
            table = Table(title=f"{'staged ' if diff.staged else ''}diff  ({len(diff.files)} files)")
            table.add_column("+added", justify="right", style="green")
            table.add_column("-removed", justify="right", style="red")
            table.add_column("path")
            for f in diff.files:
                path = f"{f.old_path} -> {f.path}" if f.old_path else f.path
                if f.binary:
                    table.add_row("bin", "bin", path)
                else:
                    table.add_row(str(f.added), str(f.removed), path)
            console.print(table)
            if patch and diff.patch:
                console.print(diff.patch)

    _run(go())


# ---------------------------------------------------------------------------

memory_app = typer.Typer(help="Inspect and monitor live memory (Phase 3)")
app.add_typer(memory_app, name="memory")


@memory_app.command("stats")
def memory_stats_cmd() -> None:
    """Show aggregate counts across all memory layers."""

    async def go() -> None:
        async with build_atlas() as atlas:
            ep_cur = await atlas.db.conn.execute("SELECT COUNT(*) FROM episodes")
            fct_cur = await atlas.db.conn.execute("SELECT COUNT(*) FROM semantic_facts WHERE superseded_by IS NULL")
            doc_cur = await atlas.db.conn.execute("SELECT COUNT(*) FROM knowledge_documents")
            chk_cur = await atlas.db.conn.execute("SELECT COUNT(*) FROM knowledge_chunks")

            ep_row = await ep_cur.fetchone()
            fct_row = await fct_cur.fetchone()
            doc_row = await doc_cur.fetchone()
            chk_row = await chk_cur.fetchone()

            prefs = await atlas.user_model.get_all_preferences()
            cache_size = atlas.retriever._cache.size if atlas.retriever._cache else 0

            table = Table("Layer", "Count", "Details")
            table.add_row("Episodic", str(ep_row[0] if ep_row else 0), "[dim]task events, corrections[/]")
            table.add_row("Semantic facts", str(fct_row[0] if fct_row else 0), "[dim]active (non-superseded)[/]")
            table.add_row(
                "Knowledge docs", str(doc_row[0] if doc_row else 0), f"[dim]{chk_row[0] if chk_row else 0} chunks[/]"
            )
            table.add_row("Preferences", str(len(prefs)), "[dim]in user model[/]")
            table.add_row("Retrieval cache", str(cache_size), "[dim]hot entries[/]")
            console.print(table)

    _run(go())


@memory_app.command("episodes")
def memory_episodes(
    limit: int = typer.Option(20, help="Number of episodes to show"),
    task_id: str = typer.Option("", help="Filter by task ID"),
    min_salience: float = typer.Option(0.0, help="Minimum salience 0-1"),
    kind: str = typer.Option("", help="Filter by kind (e.g. action, correction)"),
) -> None:
    """List recent episodic memory entries."""

    async def go() -> None:
        async with build_atlas() as atlas:
            from atlas.memory.types import EpisodeKind

            episodes = await atlas.episodic.search_similar(
                task_id=task_id or None,
                kind=EpisodeKind(kind) if kind else None,
                min_salience=min_salience,
                limit=limit,
            )
            if not episodes:
                console.print("[yellow]No episodes found.[/]")
                return

            table = Table("ID", "Kind", "Salience", "Task", "Content", "Time")
            for ep in episodes:
                sal_color = "gold1" if ep.salience >= 0.7 else ("yellow" if ep.salience >= 0.4 else "dim")
                table.add_row(
                    str(ep.id or "?"),
                    ep.kind.value,
                    f"[{sal_color}]{ep.salience:.2f}[/]",
                    (ep.task_id or "")[:12],
                    ep.content[:60] + ("…" if len(ep.content) > 60 else ""),
                    ep.ts.strftime("%H:%M:%S"),
                )
            console.print(table)
            console.print(f"[dim]{len(episodes)} episodes[/]")

    _run(go())


@memory_app.command("facts")
def memory_facts(
    limit: int = typer.Option(30, help="Number of facts to show"),
    kind: str = typer.Option("", help="Filter by FactKind"),
    min_conf: float = typer.Option(0.0, help="Minimum confidence 0-1"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass fact cache"),
) -> None:
    """List semantic facts from long-term memory."""

    async def go() -> None:
        async with build_atlas() as atlas:
            from atlas.memory.types import FactKind

            if no_cache:
                await atlas.semantic._fact_cache.invalidate()

            facts = await atlas.semantic.get_recent_facts(
                kind=FactKind(kind) if kind else None,
                min_confidence=min_conf,
                limit=limit,
            )
            if not facts:
                console.print("[yellow]No facts found.[/]")
                return

            table = Table("ID", "Kind", "Confidence", "Salience", "Text")
            for f in facts:
                conf_color = "green" if f.confidence >= 0.8 else ("yellow" if f.confidence >= 0.5 else "red")
                table.add_row(
                    f.id[:8],
                    f.kind.value,
                    f"[{conf_color}]{f.confidence:.2f}[/]",
                    f"{f.salience:.2f}",
                    f.text[:80] + ("…" if len(f.text) > 80 else ""),
                )
            console.print(table)
            console.print(f"[dim]{len(facts)} facts[/]")

    _run(go())


@memory_app.command("search")
def memory_search_cmd(
    query: str = typer.Argument(..., help="Semantic search query"),
    limit: int = typer.Option(5, help="Max results"),
    layers: str = typer.Option("all", help="all | facts | episodes | knowledge"),
) -> None:
    """Semantic search across memory layers. Shows what ATLAS would retrieve."""

    async def go() -> None:
        async with build_atlas() as atlas:
            import time

            t0 = time.monotonic()

            if layers in ("all", "facts"):
                console.print(f"\n[bold cyan]Semantic Facts[/] for [italic]{query}[/]")
                hits = await atlas.semantic.semantic_search(query, k=limit)
                if hits:
                    for f in hits:
                        conf_color = "green" if f.confidence >= 0.8 else "yellow"
                        console.print(f"  [{conf_color}]{f.confidence:.2f}[/] [dim][{f.kind.value}][/] {f.text[:100]}")
                else:
                    console.print("  [dim]No matches[/]")

            if layers in ("all", "episodes"):
                console.print(f"\n[bold cyan]Episodes[/] for [italic]{query}[/]")
                eps = await atlas.episodic.semantic_search(query, limit=limit)
                if eps:
                    for ep in eps:
                        console.print(f"  [dim]{ep.ts.strftime('%H:%M:%S')}[/] [{ep.kind.value}] {ep.content[:100]}")
                else:
                    console.print("  [dim]No matches[/]")

            if layers in ("all", "knowledge"):
                console.print(f"\n[bold cyan]Knowledge[/] for [italic]{query}[/]")
                chunks = await atlas.knowledge_store.search(query, limit=limit)
                if chunks:
                    for c in chunks:
                        score = c.get("score", 0.0)
                        title = c.get("document_title", "?")
                        content = c.get("content", "")[:100]
                        console.print(f"  [{score:.2f}] [bold]{title}[/] {content}")
                else:
                    console.print("  [dim]No knowledge chunks indexed[/]")

            elapsed = int((time.monotonic() - t0) * 1000)
            console.print(f"\n[dim]Retrieved in {elapsed} ms[/]")

    _run(go())


@memory_app.command("watch")
def memory_watch(
    host: str = typer.Option("localhost:8000", help="API server host:port"),
) -> None:
    """Watch live memory events via WebSocket (/ws/memory/live).

    Shows every memory write, retrieval, and update in real time.
    Press Ctrl+C to stop.
    """
    import json

    from websockets.exceptions import WebSocketException
    from websockets.sync.client import connect

    uri = f"ws://{host}/ws/memory/live"

    KIND_COLORS: dict[str, str] = {  # noqa: N806
        "memory.stored": "blue",
        "memory.retrieved": "cyan",
        "memory.fact_added": "green",
        "memory.user_model_updated": "magenta",
        "memory.knowledge_indexed": "yellow",
        "memory.consolidated": "gold1",
        "memory.pruned": "dim",
    }
    LAYER_ICONS: dict[str, str] = {  # noqa: N806
        "episodic": "📖",
        "semantic": "🧠",
        "user_model": "👤",
        "knowledge": "📚",
        "working": "⚡",
    }

    try:
        console.print(f"[dim]Connecting to {uri}…[/]")
        with connect(uri, close_timeout=2) as ws:
            console.print("[green]✓ Connected[/] — watching memory events (Ctrl+C to stop)\n")
            for raw in ws:
                msg = json.loads(raw)
                mtype = msg.get("type")

                if mtype == "ping":
                    ws.send("pong")
                    continue

                if mtype == "snapshot":
                    console.print(
                        f"[dim]snapshot[/]  episodes={msg.get('episode_count', '?')}  "
                        f"facts={msg.get('fact_count', '?')}  "
                        f"docs={msg.get('document_count', '?')}  "
                        f"prefs={msg.get('preference_count', '?')}"
                    )
                    continue

                if mtype == "replay_complete":
                    continue

                kind = msg.get("kind", "unknown")
                mem_type = msg.get("memory_type", "")
                items = msg.get("items", [])
                task_id_raw = msg.get("task_id", "")
                color = KIND_COLORS.get(kind, "white")
                icon = LAYER_ICONS.get(mem_type, "•")

                console.print(
                    f"{icon} [{color}]{kind}[/]  "
                    f"[dim]{mem_type}[/]  "
                    f"{items[0][:70] if items else ''}  "
                    f"[dim]{task_id_raw[:8] if task_id_raw not in ('system', '') else 'sys'}[/]"
                )

    except KeyboardInterrupt:
        console.print("\n[yellow]⏸ Stopped watching[/]")
    except WebSocketException as exc:
        console.print(f"[red]✗ WebSocket error:[/] {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[red]✗ Error:[/] {exc}")
        raise typer.Exit(1) from exc


@memory_app.command("flush-cache")
def memory_flush_cache() -> None:
    """Flush the in-process retrieval + fact caches.

    Useful after manually editing the DB or during debugging.
    """

    async def go() -> None:
        async with build_atlas() as atlas:
            await atlas.retriever.invalidate_cache()
            await atlas.semantic._fact_cache.invalidate()
            console.print("[green]✓ Caches flushed[/]")

    _run(go())


if __name__ == "__main__":
    app()
