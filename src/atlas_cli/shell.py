"""Interactive shell for ATLAS CLI."""

import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from atlas_cli.client import AtlasClient
from atlas_cli.render import TaskRenderer

console = Console()
client = AtlasClient()

async def interactive_shell() -> None:
    session: PromptSession = PromptSession(history=InMemoryHistory())
    console.print("[bold cyan]ATLAS Shell[/] (type 'exit' or 'quit' to exit)")
    
    while True:
        try:
            with patch_stdout():
                text = await session.prompt_async("atlas> ")
        except (EOFError, KeyboardInterrupt):
            break
            
        text = text.strip()
        if not text:
            continue
        if text.lower() in ("exit", "quit"):
            break
            
        try:
            task_info = await client.create_task(text, source="cli_shell")
            task_id = task_info["task_id"]
            
            with TaskRenderer(task_id) as renderer:
                async for event in client.stream_task_events(task_id):
                    renderer.process_event(event)
                    
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
                    console.print("[green]Ok.[/]")
            else:
                console.print(f"[red]Failed with state: {final_task['state']}[/]")
        except Exception as exc:
            console.print(f"[bold red]Error:[/] {exc}")

if __name__ == "__main__":
    asyncio.run(interactive_shell())
