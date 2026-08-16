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
            task_id = task_info["task_id"]
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
                tasks = resp.json()
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
                    table.add_row(t["id"], t["state"], req, t["source"], t["created_ts"][:19])
                console.print(table)

    _run(go())


@app.command("shell")
def shell_cmd() -> None:
    """Enter the interactive ATLAS shell."""
    from atlas_cli.shell import interactive_shell

    _run(interactive_shell())


@app.command("events")
def events_cmd(
    action: str = typer.Argument("stream", help="Action: stream or search"),
    task_id: str = typer.Option(None, "--task-id", "-t", help="Filter by task ID"),
    event_type: str = typer.Option(None, "--event-type", "-e", help="Filter by event type (e.g., tool.completed)"),
    from_time: str = typer.Option(None, "--from", help="Start timestamp (ISO format)"),
    to_time: str = typer.Option(None, "--to", help="End timestamp (ISO format)"),
    limit: int = typer.Option(100, "--limit", "-n", help="Max results (default: 100)"),
    offset: int = typer.Option(0, "--offset", help="Pagination offset (default: 0)"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
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
        else:
            console.print(f"[red]Unknown action:[/] {action}")
            console.print("[dim]Available actions: stream, search[/]")

    _run(go())


if __name__ == "__main__":
    app()
