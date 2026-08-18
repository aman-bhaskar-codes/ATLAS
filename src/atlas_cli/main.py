"""ATLAS CLI — The AI Operating System Canonical Interface."""

import asyncio
from typing import Any

import typer
from rich.console import Console

from atlas_cli.client import AtlasClient
from atlas_cli.render import TaskRenderer

app = typer.Typer(add_completion=False, help="ATLAS CLI")

console = Console()
client = AtlasClient()


def _run(coro: Any) -> None:
    asyncio.run(coro)


@app.command("run")
def run_task(
    request: str,
    watch: bool = typer.Option(True, "--watch/--no-watch", help="Show live progress (default: True)"),
    json_output: bool = typer.Option(False, "--json", help="Output final result as JSON"),
) -> None:
    """Execute a task through the orchestration runtime.

    By default, shows live progress as the task executes. Use --no-watch to
    get the old blocking behavior (wait for completion without live updates).

    Examples:
      atlas run "list files in current directory"       # Live progress
      atlas run "calculate 2+2" --no-watch              # Wait silently
      atlas run "search for Python tutorials" --json    # JSON output
    """

    async def go() -> None:
        try:
            # 1. Start the task
            console.print("[dim]Creating task...[/]")
            task_info = await client.create_task(request)
            task_id = task_info["id"]
            console.print(f"[green]✓ Task created:[/] [cyan]{task_id}[/]\n")

            if watch:
                # 2. Stream events with Rich renderer
                console.print("[dim]Streaming events...[/]\n")
                with TaskRenderer(task_id) as renderer:
                    async for event in client.stream_task_events(task_id):
                        renderer.process_event(event)

                        # Check if task completed
                        kind = event.get("kind", "")
                        if kind in ["task.completed", "task.failed"]:
                            break

                console.print()  # Add spacing
            else:
                # Old blocking behavior - just wait
                console.print("[dim]Waiting for task to complete...[/]")
                import asyncio

                # Poll task status until complete
                while True:
                    await asyncio.sleep(1)
                    task = await client.get_task(task_id)
                    if task["state"] in ["completed", "failed"]:
                        break

            # 3. Fetch final result
            final_task = await client.get_task(task_id)

            if json_output:
                console.print_json(data=final_task)
            else:
                state = final_task["state"]
                if state == "completed":
                    payload = final_task.get("payload", {})
                    if isinstance(payload, str):
                        import json

                        try:
                            payload = json.loads(payload)
                        except Exception:
                            pass

                    if isinstance(payload, dict) and "answer" in payload:
                        from rich.markdown import Markdown

                        console.print("\n[bold green]Result:[/]")
                        console.print(Markdown(payload["answer"]))
                    else:
                        console.print("\n[bold green]Task completed successfully.[/]")
                elif state == "failed":
                    console.print(f"\n[bold red]Task failed with state: {state}[/]")
                    if isinstance(final_task.get("payload"), dict):
                        error = final_task["payload"].get("error", "Unknown error")
                        console.print(f"[red]Error:[/] {error}")
                else:
                    console.print(f"\n[yellow]Task state:[/] {state}")

        except Exception as exc:
            console.print(f"[bold red]Error:[/] {exc}")
            import traceback

            console.print("[dim]" + traceback.format_exc() + "[/]")

    _run(go())


@app.command("task")
def task_cmd(action: str = typer.Argument("list"), task_id: str = "") -> None:
    """Manage tasks: list or watch."""

    async def go() -> None:
        if action == "watch":
            if not task_id:
                console.print("[red]Task ID required to watch.[/]")
                raise typer.Exit(1)
            with TaskRenderer(task_id) as renderer:
                async for event in client.stream_task_events(task_id):
                    renderer.process_event(event)
        elif action == "list":
            import httpx

            async with httpx.AsyncClient() as c:
                resp = await c.get(f"{client.base_url}/api/v1/tasks")
                
                data = resp.json()
                tasks = data.get("items", []) if isinstance(data, dict) else data

                from rich.table import Table

                table = Table("ID", "State", "Request", "Source", "Created")
                for t in tasks[:10]:
                    req = ""
                    p = t.get("payload", {})
                    if isinstance(p, str):
                        import json

                        try:
                            p = json.loads(p)
                        except Exception:
                            pass

                    if isinstance(p, dict):
                        req = p.get("request", "")[:40]

                    # Note: API might return created_at instead of created_ts
                    created_time = t.get("created_ts", t.get("created_at", ""))[:19]
                    table.add_row(t["id"], t["state"], req, t["source"], created_time)

                console.print(table)

    _run(go())


@app.command("shell")
def shell_cmd() -> None:
    """Enter the interactive ATLAS shell."""
    from atlas_cli.shell import interactive_shell

    _run(interactive_shell())


@app.command("events")
def events_cmd(
    action: str = typer.Argument("stream", help="Action: stream, search, emit, replay"),
    task_id: str = typer.Option(None, "--task-id", "-t", help="Filter by task ID"),
    event_type: str = typer.Option(None, "--event-type", "-e", help="Filter by event type (e.g., tool.completed)"),
    from_time: str = typer.Option(None, "--from", help="Start timestamp (ISO format)"),
    to_time: str = typer.Option(None, "--to", help="End timestamp (ISO format)"),
    limit: int = typer.Option(100, "--limit", "-n", help="Max results (default: 100)"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset (default: 0)"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    payload: str = typer.Option(None, "--payload", "-p", help="JSON payload string for emit"),
    event_id: str = typer.Option(None, "--event-id", help="Event ID to replay"),
) -> None:
    """Monitor global event stream or search historical events.

    Examples:
      atlas events stream                     # Watch all events live
      atlas events stream --task-id abc-123   # Filter by task
      atlas events stream --event-type tool.completed  # Filter by type

      atlas events search                     # Search last 100 events
      atlas events search --task-id abc-123   # Search by task
      atlas events search --event-type tool --limit 50  # Search by type
      atlas events search --from "2024-01-01T00:00:00Z" --to "2024-01-31T23:59:59Z"
      atlas events search --json              # JSON output
    """

    async def go() -> None:
        if action == "search":
            # NEW: Search historical events
            try:
                result = await client.search_events(
                    task_id=task_id, topic=event_type, from_ts=from_time, to_ts=to_time, limit=limit, offset=offset
                )

                events = result.get("events", [])
                total = result.get("total", 0)

                if json_output:
                    console.print_json(data=result)
                else:
                    # Display summary
                    showing = f"{offset + 1}-{offset + len(events)}" if events else "0"
                    console.print(f"[bold cyan]Found {total} events[/] (showing {showing})\n")

                    if not events:
                        console.print("[dim]No events matched your search criteria.[/]")
                        return

                    # Render events with TaskRenderer
                    with TaskRenderer(task_id or "search") as renderer:
                        for event in events:
                            renderer.process_event(event)

                    # Show pagination hint
                    if total > offset + len(events):
                        remaining = total - offset - len(events)
                        next_offset = offset + len(events)
                        console.print(f"\n[dim]... {remaining} more events (use --offset {next_offset})[/]")

            except Exception as exc:
                console.print(f"[bold red]Error:[/] {exc}")
                import traceback

                console.print("[dim]" + traceback.format_exc() + "[/]")

        elif action == "stream":
            import asyncio
            from collections import defaultdict
            from datetime import datetime

            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text

            # Stats tracking
            stats: dict[str, Any] = {
                "total": 0,
                "by_type": defaultdict(int),
                "by_task": defaultdict(int),
                "start_time": datetime.now(),
            }
            by_type: defaultdict[str, int] = stats["by_type"]
            by_task: defaultdict[str, int] = stats["by_task"]
            start_time: datetime = stats["start_time"]

            def render_stats() -> Panel:
                """Render live stats panel"""
                elapsed = (datetime.now() - start_time).total_seconds()
                total: int = stats["total"]
                rate = total / elapsed if elapsed > 0 else 0

                table = Table.grid(padding=(0, 2))
                table.add_column(style="cyan")
                table.add_column(style="white")

                table.add_row("Total Events:", f"{total}")
                table.add_row("Rate:", f"{rate:.1f}/sec")
                table.add_row("Active Tasks:", f"{len(by_task)}")

                # Top 5 event types
                top_types = sorted(by_type.items(), key=lambda x: x[1], reverse=True)[:5]
                if top_types:
                    table.add_row("", "")
                    table.add_row("Top Event Types:", "")
                    for etype, count in top_types:
                        table.add_row(f"  {etype}", f"{count}")

                return Panel(table, title="[bold cyan]Event Stream Stats[/]", border_style="cyan")

            def format_event_line(event: dict[str, Any]) -> Text:
                """Format single event as colored text line"""
                kind = event.get("kind", "unknown")
                ts = event.get("_timestamp", "")
                tid = event.get("task_id", "")[:8]

                # Get symbol and color
                symbol_map = {
                    "started": ("▶", "blue"),
                    "completed": ("✓", "green"),
                    "failed": ("✗", "red"),
                    "thought": ("💭", "cyan"),
                    "executing": ("⚙", "yellow"),
                    "classified": ("🛡", "magenta"),
                    "retrieved": ("📚", "blue"),
                }

                symbol, color = "•", "white"
                for key, (s, c) in symbol_map.items():
                    if key in kind:
                        symbol, color = s, c
                        break

                # Format timestamp
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts_short = dt.strftime("%H:%M:%S")
                except Exception:
                    ts_short = ts[:8] if len(ts) >= 8 else ts

                line = Text()
                line.append(f"{ts_short} ", style="dim")
                line.append(f"{symbol} ", style=color)
                line.append(f"{kind} ", style="bold")
                line.append(f"({tid})", style="dim")

                # Add summary if present
                metadata = event.get("metadata", {})
                if "summary" in metadata:
                    line.append(f" - {metadata['summary'][:60]}", style="dim")

                return line

            console.print("[cyan]Connecting to global event stream...[/]")

            try:
                # Start streaming
                event_buffer = []
                max_buffer = 20

                async for event in client.stream_global_events():
                    # Apply filters
                    if task_id and event.get("task_id") != task_id:
                        continue
                    if event_type and event.get("kind") != event_type:
                        continue

                    # Update stats
                    total_count: int = stats["total"]
                    stats["total"] = total_count + 1
                    kind_str = event.get("kind") or "unknown"
                    by_type[kind_str] += 1
                    task_id_str = event.get("task_id")
                    if task_id_str:
                        by_task[task_id_str] += 1

                    # Output
                    if json_output:
                        console.print_json(data=event)
                    else:
                        # Add to buffer
                        event_buffer.append(event)
                        if len(event_buffer) > max_buffer:
                            event_buffer.pop(0)

                        # Render stats + recent events
                        output = render_stats()
                        console.print(output)
                        console.print()

                        # Show recent events
                        for evt in event_buffer[-10:]:
                            console.print(format_event_line(evt))

                        # Clear screen effect (move cursor up)
                        if not json_output:
                            await asyncio.sleep(0.1)  # Small delay for readability

            except KeyboardInterrupt:
                console.print("\n[yellow]⏸ Stopped streaming[/]")
            except Exception as exc:
                console.print(f"[red]✗ Error:[/] {exc}")
                
        elif action == "emit":
            import json
            if not event_type or not payload:
                console.print("[red]--event-type and --payload are required for emit[/]")
                return
            try:
                payload_dict = json.loads(payload)
                resp = await client.emit_event(event_type, payload_dict)
                if resp.get("error"):
                    console.print(f"[red]Error:[/] {resp['error']}")
                else:
                    console.print(f"[green]✓ Emitted event '{event_type}'[/]")
                if json_output:
                    console.print_json(data=resp)
            except Exception as exc:
                console.print(f"[red]Error emitting event:[/] {exc}")
                
        elif action == "replay":
            if not event_id:
                console.print("[red]--event-id is required for replay[/]")
                return
            try:
                resp = await client.replay_event(event_id)
                if resp.get("error"):
                    console.print(f"[red]Error:[/] {resp['error']}")
                else:
                    console.print(f"[green]✓ Replayed event {event_id}[/]")
                if json_output:
                    console.print_json(data=resp)
            except Exception as exc:
                console.print(f"[red]Error replaying event:[/] {exc}")
                
        else:
            console.print(f"[red]Unknown action:[/] {action}")
            console.print("[dim]Available actions: stream, search, emit, replay[/]")

    _run(go())


# ═══════════════════════════════════════════════════════════════════════════
# Zero-Cost-First CLI Commands
# ═══════════════════════════════════════════════════════════════════════════

# ── atlas doctor ──────────────────────────────────────────────────────────

@app.command("doctor")
def doctor_cmd() -> None:
    """Run system diagnostics — verify environment, providers, models."""
    from rich.panel import Panel
    from rich.table import Table

    async def go() -> None:
        try:
            resp = await client._get("/api/v1/ops/health")
            data = resp if isinstance(resp, dict) else {"status": "unknown"}
        except Exception:
            data = {"status": "unreachable"}

        # ── Environment checks ────────────────────────────────────────
        import shutil
        import sys

        table = Table(title="ATLAS Environment", show_header=True)
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Detail", style="dim")

        # Python
        py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        table.add_row("Python", "[green]✓[/]", py_version)

        # Ollama
        import httpx
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get("http://localhost:11434/api/tags")
                models = [m["name"] for m in r.json().get("models", [])]
                table.add_row("Ollama", "[green]✓[/]", f"{len(models)} models: {', '.join(models[:3])}")
        except Exception:
            table.add_row("Ollama", "[red]✗[/]", "Not running")

        # Docker
        docker = shutil.which("docker")
        table.add_row("Docker", "[green]✓[/]" if docker else "[yellow]○[/] optional", str(docker or "not found"))

        # Playwright
        try:
            import playwright  # noqa: F401
            table.add_row("Browser", "[green]✓[/]", "Playwright installed")
        except ImportError:
            table.add_row("Browser", "[yellow]○[/] optional", "pip install playwright")

        # API server
        api_status = data.get("status", "unknown")
        table.add_row(
            "API Server",
            "[green]✓[/]" if api_status == "ok" else "[red]✗[/]",
            f"Status: {api_status}",
        )

        console.print(table)

        # ── Profile info ──────────────────────────────────────────────
        from atlas.infra.config import load_settings
        from atlas.infra.profiles import resolve_profile

        try:
            settings = load_settings()
            profile = resolve_profile(settings.profile)
            console.print()
            console.print(Panel(
                f"[bold]Profile:[/] {profile.profile.value}\n"
                f"[bold]Cost Policy:[/] {profile.cost_policy.value}\n"
                f"[bold]Network Policy:[/] {profile.network_policy.value}\n"
                f"[bold]Cloud Allowed:[/] {profile.allow_cloud}\n"
                f"[bold]Quota Governor:[/] {profile.enable_quota_governor}",
                title="[bold cyan]Active Profile[/]",
                border_style="cyan",
            ))
        except Exception:
            pass

    _run(go())


# ── atlas providers ───────────────────────────────────────────────────────

providers_app = typer.Typer(help="Manage providers: list, health, free, quota")
app.add_typer(providers_app, name="providers")


@providers_app.command("list")
def providers_list() -> None:
    """List all registered providers with health and cost class."""
    from rich.table import Table

    async def go() -> None:
        try:
            data = await client._get("/api/v1/ops/models")
        except Exception as exc:
            console.print(f"[red]Error fetching models:[/] {exc}")
            console.print("[dim]Falling back to config file...[/]")
            data = []

        if not data:
            # Fallback: read directly from models.yaml
            from pathlib import Path

            import yaml

            config_path = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
            if config_path.exists():
                raw = yaml.safe_load(config_path.read_text())
                data = raw.get("models", [])

        table = Table(title="Provider Registry")
        table.add_column("Model", style="cyan")
        table.add_column("Provider", style="bold")
        table.add_column("Cost Class", style="bold")
        table.add_column("Quality", justify="right")
        table.add_column("Enabled", justify="center")
        table.add_column("Context", justify="right")

        for m in data:
            cost_class = m.get("cost_class", "paid")
            color = {"local": "green", "free": "blue", "free_quota": "yellow", "paid": "red"}.get(cost_class, "white")
            enabled = m.get("enabled", False)
            table.add_row(
                m.get("id", "?"),
                m.get("provider", "?"),
                f"[{color}]{cost_class.upper()}[/{color}]",
                f"{m.get('quality_score', 0):.2f}",
                "[green]✓[/]" if enabled else "[dim]○[/]",
                f"{m.get('context_length', 0):,}",
            )
        console.print(table)

    _run(go())


@providers_app.command("free")
def providers_free() -> None:
    """Show only free and local providers."""
    from pathlib import Path

    import yaml
    from rich.table import Table

    config_path = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
    raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    models = raw.get("models", [])

    table = Table(title="Free & Local Providers")
    table.add_column("Model", style="cyan")
    table.add_column("Provider", style="bold")
    table.add_column("Cost Class", style="bold")
    table.add_column("Capabilities", style="dim")
    table.add_column("Enabled", justify="center")

    for m in models:
        cc = m.get("cost_class", "paid")
        if cc in ("local", "free", "free_quota"):
            color = {"local": "green", "free": "blue", "free_quota": "yellow"}.get(cc, "white")
            caps = ", ".join(m.get("capabilities", [])[:4])
            enabled = m.get("enabled", False)
            table.add_row(
                m.get("id", "?"),
                m.get("provider", "?"),
                f"[{color}]{cc.upper()}[/{color}]",
                caps,
                "[green]✓[/]" if enabled else "[dim]○[/]",
            )
    console.print(table)


@providers_app.command("health")
def providers_health() -> None:
    """Show provider health + quota status."""
    from rich.table import Table

    async def go() -> None:
        try:
            data = await client._get("/api/v1/providers/health")
        except Exception:
            console.print("[yellow]API not available. Showing static config.[/]")
            providers_list()
            return

        table = Table(title="Provider Health")
        table.add_column("Provider", style="cyan")
        table.add_column("Health", justify="center")
        table.add_column("Quota", justify="right")
        table.add_column("Latency", justify="right")

        for p in data if isinstance(data, list) else []:
            health = p.get("healthy", False)
            table.add_row(
                p.get("name", "?"),
                "[green]✓[/]" if health else "[red]✗[/]",
                f"{p.get('quota_pct', 100)}%",
                f"{p.get('avg_latency_ms', 0)}ms",
            )
        console.print(table)

    _run(go())


@providers_app.command("verify")
def providers_verify() -> None:
    """Verify free-tier provider availability (OpenRouter discovery + local Ollama)."""
    from rich.table import Table

    async def go() -> None:
        table = Table(title="Free-Tier Verification")
        table.add_column("Check", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Detail", style="dim")

        # Ollama local check
        import httpx
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get("http://localhost:11434/api/tags")
                n = len(r.json().get("models", []))
            table.add_row("Ollama (local)", "[green]✓[/]", f"{n} models installed")
        except Exception:
            table.add_row("Ollama (local)", "[red]✗[/]", "not running (local_free still works once started)")

        # OpenRouter free-model discovery
        from atlas.intelligence.providers.openrouter_free import discover_free_models
        discovery = await discover_free_models()
        if discovery.ok:
            table.add_row(
                "OpenRouter free models",
                "[green]✓[/]",
                f"{len(discovery.models)} free now · verified {discovery.verified_at:%Y-%m-%d %H:%M} UTC",
            )
            for m in discovery.models[:5]:
                table.add_row(f"  {m.id}", "[blue]FREE[/]", m.name)
            if len(discovery.models) > 5:
                table.add_row("  ...", "", f"+{len(discovery.models) - 5} more")
        else:
            table.add_row(
                "OpenRouter free models",
                "[yellow]○ unreachable[/]",
                "degraded to static config (offline is fine)",
            )

        console.print(table)

    _run(go())


# ── atlas automations ─────────────────────────────────────────────────────

automations_app = typer.Typer(help="Manage automations")
app.add_typer(automations_app, name="automations")

@automations_app.command("list")
def automations_list(enabled_only: bool = False) -> None:
    """List automations."""
    import httpx
    from rich.table import Table

    async def go() -> None:
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"{client.base_url}/api/v1/automations", params={"enabled_only": enabled_only})
            if resp.is_error:
                console.print(f"[red]Error:[/] {resp.text}")
                return
            
            data = resp.json()
            table = Table(title="Automations Registry")
            table.add_column("ID", style="cyan")
            table.add_column("Name", style="bold")
            table.add_column("Event Type")
            table.add_column("Action")
            table.add_column("Enabled")
            
            for auto in data:
                trigger = auto.get("trigger_config", {})
                action = auto.get("action_config", {})
                table.add_row(
                    auto.get("id"),
                    auto.get("name"),
                    trigger.get("event_type", ""),
                    action.get("type", ""),
                    "[green]✓[/]" if auto.get("enabled") else "[red]✗[/]"
                )
            console.print(table)
    _run(go())

@automations_app.command("create")
def automations_create(
    name: str = typer.Option(..., "--name", "-n"),
    description: str = typer.Option("", "--desc"),
    event_type: str = typer.Option(..., "--event-type", "-e"),
    action_type: str = typer.Option("task", "--action-type"),
    request_template: str = typer.Option(..., "--template", "-t"),
) -> None:
    """Create a new automation."""
    import httpx
    async def go() -> None:
        payload = {
            "name": name,
            "description": description,
            "trigger_config": {"event_type": event_type, "filters": {}},
            "action_config": {"type": action_type, "request_template": request_template}
        }
        async with httpx.AsyncClient() as c:
            resp = await c.post(f"{client.base_url}/api/v1/automations", json=payload)
            if resp.is_error:
                console.print(f"[red]Error:[/] {resp.text}")
            else:
                console.print(f"[green]✓ Created automation:[/] {resp.json().get('id')}")
    _run(go())
    
@automations_app.command("toggle")
def automations_toggle(auto_id: str) -> None:
    """Toggle an automation enabled state."""
    import httpx
    async def go() -> None:
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"{client.base_url}/api/v1/automations/{auto_id}")
            if resp.is_error:
                console.print(f"[red]Error:[/] {resp.text}")
                return
            auto = resp.json()
            auto["enabled"] = not auto["enabled"]
            resp = await c.put(f"{client.base_url}/api/v1/automations/{auto_id}", json=auto)
            if resp.is_error:
                console.print(f"[red]Error:[/] {resp.text}")
            else:
                state = "enabled" if auto["enabled"] else "disabled"
                console.print(f"[green]✓ Automation {auto_id} is now {state}.[/]")
    _run(go())


# ── atlas cost ────────────────────────────────────────────────────────────

cost_app = typer.Typer(help="View and manage cost controls")
app.add_typer(cost_app, name="cost")


@cost_app.command("show")
def cost_show() -> None:
    """Show current cost summary."""
    from rich.panel import Panel

    async def go() -> None:
        try:
            data = await client._get("/api/v1/ops/cost")
        except Exception:
            data = {}

        today = data.get("today_usd", 0.0)
        week = data.get("week_usd", 0.0)
        month = data.get("month_usd", 0.0)
        policy = data.get("cost_policy", "unknown")

        content = (
            f"[bold]Cost Policy:[/] {policy}\n"
            f"\n"
            f"[bold]Today:[/]  ${today:.4f}\n"
            f"[bold]Week:[/]   ${week:.4f}\n"
            f"[bold]Month:[/]  ${month:.4f}\n"
        )

        if policy == "zero_cost":
            content += "\n[bold green]$0 ENFORCED — paid providers blocked[/]"

        console.print(Panel(content, title="[bold cyan]Cost Summary[/]", border_style="cyan"))

    _run(go())


@cost_app.command("enforce")
def cost_enforce(
    mode: str = typer.Argument(
        "zero_cost",
        help="Cost mode: zero_cost|free_only|free_preferred|balanced|unrestricted",
    ),
) -> None:
    """Set cost enforcement mode."""
    valid = {"zero_cost", "free_only", "free_preferred", "balanced", "unrestricted"}
    if mode not in valid:
        console.print(f"[red]Invalid mode.[/] Choose: {', '.join(sorted(valid))}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] Set ATLAS_COST_POLICY={mode}")
    console.print(f"[dim]To persist: export ATLAS_COST_POLICY={mode}[/]")


# ── atlas models ──────────────────────────────────────────────────────────

models_app = typer.Typer(help="Manage models: list, doctor, pull")
app.add_typer(models_app, name="models")


@models_app.command("list")
def models_list() -> None:
    """Show all configured models and availability."""
    providers_list()  # reuse the same table


@models_app.command("doctor")
def models_doctor() -> None:
    """Verify all configured local models exist in Ollama."""
    from pathlib import Path

    import yaml

    async def go() -> None:
        config_path = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
        raw = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
        local_models = [m for m in raw.get("models", []) if m.get("cost_class") == "local"]

        import httpx
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get("http://localhost:11434/api/tags")
                installed = {m["name"] for m in r.json().get("models", [])}
        except Exception:
            console.print("[red]Ollama is not running.[/]")
            return

        for m in local_models:
            model_name = m.get("provider_model", "")
            found = model_name in installed or any(model_name in i for i in installed)
            status = "[green]✓ installed[/]" if found else "[red]✗ missing[/]"
            console.print(f"  {model_name:30s} {status}")

        missing = [
            m
            for m in local_models
            if m.get("provider_model", "") not in installed
            and not any(m.get("provider_model", "") in i for i in installed)
        ]
        if missing:
            console.print("\n[yellow]Run to install missing models:[/]")
            for m in missing:
                console.print(f"  ollama pull {m.get('provider_model', '')}")

    _run(go())


# ── atlas profile ─────────────────────────────────────────────────────────

@app.command("profile")
def profile_cmd(
    name: str = typer.Argument(None, help="Profile to show/set: local_free|free_hybrid|free_demo|production"),
) -> None:
    """Show or set the operating profile."""
    from rich.table import Table

    from atlas.infra.profiles import list_profiles, resolve_profile

    if name is None:
        # Show all profiles
        table = Table(title="Operating Profiles")
        table.add_column("Profile", style="cyan")
        table.add_column("Cost Policy", style="bold")
        table.add_column("Network", style="bold")
        table.add_column("Cloud", justify="center")
        table.add_column("Budget/day", justify="right")

        for p in list_profiles():
            table.add_row(
                p.profile.value,
                p.cost_policy.value,
                p.network_policy.value,
                "[green]✓[/]" if p.allow_cloud else "[dim]○[/]",
                f"${p.daily_usd:.2f}",
            )
        console.print(table)
        console.print("\n[dim]Set profile: export ATLAS_PROFILE=<name>[/]")
    else:
        p = resolve_profile(name)
        console.print(f"[bold]Profile:[/] {p.profile.value}")
        console.print(f"  Cost Policy:      {p.cost_policy.value}")
        console.print(f"  Network Policy:   {p.network_policy.value}")
        console.print(f"  Cloud Allowed:    {p.allow_cloud}")
        console.print(f"  Quota Governor:   {p.enable_quota_governor}")
        console.print(f"  Budget (daily):   ${p.daily_usd:.2f}")
        console.print(f"  Allowed Classes:  {', '.join(sorted(p.allowed_cost_classes))}")
        console.print(f"\n[dim]Activate: export ATLAS_PROFILE={name}[/]")


if __name__ == "__main__":
    app()

