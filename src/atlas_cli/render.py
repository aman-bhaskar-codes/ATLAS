"""Rich console rendering for the CLI."""

from datetime import datetime
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


class TaskRenderer:
    """Renders task events in real-time using Rich."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=False,  # Keep history visible
        )
        self.live = Live(self.progress, console=console, refresh_per_second=4)
        self.step_task = self.progress.add_task("[dim]Connecting...[/]", total=None)
        self.event_count = 0

    def __enter__(self) -> "TaskRenderer":
        self.live.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.live.stop()

    def _format_timestamp(self, ts_str: str) -> str:
        """Format ISO timestamp to HH:MM:SS"""
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.strftime("%H:%M:%S")
        except Exception:
            return ts_str[:8] if len(ts_str) >= 8 else ts_str

    def _get_event_symbol(self, kind: str) -> tuple[str, str]:
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

    def process_event(self, event: dict[str, Any]) -> None:
        """Handle an incoming event from the WebSocket (Phase 1 format)."""
        self.event_count += 1

        # Extract event details
        kind = event.get("kind", "unknown")
        timestamp = event.get("_timestamp", "")
        metadata = event.get("metadata", {})
        is_historical = event.get("historical", False)

        # Update progress bar description
        symbol, color = self._get_event_symbol(kind)

        prefix = "[dim]↻[/] " if is_historical else ""
        desc = f"{prefix}[{color}]{symbol}[/] {kind}"

        # Add key metadata to description
        if "summary" in metadata:
            desc += f" - {metadata['summary']}"
        elif "thought" in metadata:
            desc += f" - {metadata['thought'][:50]}..."
        elif "tool" in metadata:
            desc += f" - {metadata['tool']}"

        self.progress.update(self.step_task, description=desc)

        # For important events, also print to console above progress
        if kind in ["task.started", "task.completed", "task.failed", "tool.completed", "tool.failed"]:
            self.live.console.print(f"[dim]{self._format_timestamp(timestamp)}[/] [{color}]{symbol} {kind}[/]")
