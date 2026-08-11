"""ATLAS CLI — The AI Operating System Canonical Interface."""

import asyncio
import typer
from rich.console import Console

from atlas_cli.client import AtlasClient
from atlas_cli.render import TaskRenderer

app = typer.Typer(add_completion=False, help="ATLAS CLI")
console = Console()
client = AtlasClient()

def _run(coro) -> None:
    asyncio.run(coro)

@app.command("run")
def run_task(request: str) -> None:
    """Execute a task through the orchestration runtime."""
    async def go() -> None:
        try:
            # 1. Start the task
            task_info = await client.create_task(request)
            task_id = task_info["task_id"]
            
            # 2. Stream events using Rich renderer
            with TaskRenderer(task_id) as renderer:
                async for event in client.stream_task_events(task_id):
                    renderer.process_event(event)
                    
            # 3. Fetch final result
            final_task = await client.get_task(task_id)
            if final_task["state"] == "completed":
                payload = final_task.get("payload", {})
                if isinstance(payload, str):
                    import json
                    try:
                        payload = json.loads(payload)
                    except:
                        pass
                
                if isinstance(payload, dict) and "answer" in payload:
                    from rich.markdown import Markdown
                    console.print(Markdown(payload["answer"]))
                else:
                    console.print("[green]Task completed successfully.[/]")
            else:
                console.print(f"[red]Task failed with state: {final_task['state']}[/]")
                
        except Exception as exc:
            console.print(f"[bold red]Error:[/] {exc}")
            
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
                        except:
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


if __name__ == "__main__":
    app()
