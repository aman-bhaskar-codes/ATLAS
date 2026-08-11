"""Rich console rendering for the CLI."""

import json
from collections.abc import AsyncGenerator
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()

class TaskRenderer:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        )
        self.live = Live(self.progress, console=console, refresh_per_second=10)
        self.step_task = self.progress.add_task("[dim]Initializing...[/]", total=None)
        
    def __enter__(self):
        self.live.start()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.live.stop()
        
    def process_event(self, event: dict[str, Any]) -> None:
        """Handle an incoming event from the websocket."""
        kind = event.get("event")
        if kind == "connected":
            self.progress.update(self.step_task, description="[bold blue]Connected to ATLAS Gateway[/]")
        elif kind == "task_event":
            data = event.get("data", {})
            e_kind = data.get("kind", "")
            
            if e_kind == "task.created":
                self.progress.update(self.step_task, description="[bold cyan]Task Created[/]")
            elif e_kind == "planning.started":
                self.progress.update(self.step_task, description="[bold yellow]Planning...[/]")
            elif e_kind == "planning.finished":
                self.progress.update(self.step_task, description="[bold green]Plan Generated[/]")
            elif e_kind == "reasoning.step":
                step = data.get("step", 0)
                self.progress.update(self.step_task, description=f"[bold blue]Reasoning Step {step}[/]")
            elif e_kind == "tool.requested":
                tool = data.get("tool", "unknown")
                op = data.get("operation", "")
                self.progress.update(self.step_task, description=f"[bold magenta]Tool Requested:[/] {tool}.{op}")
            elif e_kind == "tool.result":
                tool = data.get("tool", "unknown")
                ok = data.get("ok", False)
                color = "green" if ok else "red"
                self.progress.update(self.step_task, description=f"[{color}]Tool Result:[/] {tool}")
            elif e_kind == "task.completed":
                self.progress.update(self.step_task, description="[bold green]Task Completed![/]")
            elif e_kind == "task.failed":
                err = data.get("error", "Unknown error")
                self.progress.update(self.step_task, description=f"[bold red]Task Failed:[/] {err}")
                
        elif kind == "stream_closed":
            state = event.get("state", "")
            if state == "completed":
                self.progress.update(self.step_task, description="[bold green]Done.[/]")
            else:
                self.progress.update(self.step_task, description=f"[bold red]Stopped:[/] {state}")
