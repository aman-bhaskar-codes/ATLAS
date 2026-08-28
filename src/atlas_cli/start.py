"""atlas start command - unified entry point for the ATLAS runtime.

This command provides the canonical way to start the ATLAS system as a living
runtime that stays alive and accepts tasks continuously.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(help="Start and manage the ATLAS runtime")
console = Console()


def _handle_signal(signum: int, frame: Any) -> None:
    """Handle shutdown signals gracefully."""
    console.print("\n[yellow]Shutdown signal received, stopping...[/]")
    # The main loop will handle graceful shutdown


@app.command("start")
def start_runtime(
    foreground: bool = typer.Option(True, "--foreground/--daemon", help="Run in foreground (default) or daemon mode"),
    port: int = typer.Option(8730, "--port", "-p", help="API server port"),
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="API server host"),
) -> None:
    """Start the ATLAS runtime and keep it alive.

    This command starts the complete ATLAS system with all components
    and keeps it running to accept tasks continuously. It performs
    staged startup with health checks and graceful shutdown.

    Examples:
      atlas runtime start                    # Start in foreground mode
      atlas runtime start --daemon           # Start as daemon
      atlas runtime start --port 9000        # Start on custom port
    """

    async def run() -> None:
        # Set up signal handlers
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        console.print("[bold cyan]Starting ATLAS Runtime...[/]")

        try:
            # Build the Atlas instance
            from atlas.app import build

            atlas = await build()

            # Start the runtime with supervisor
            console.print("[dim]Initializing runtime supervisor...[/]")
            health_report = await atlas.start()

            # Display startup results
            _display_startup_results(health_report)

            # Check if system is in usable state
            from atlas.bootstrap.runtime import SystemState

            if health_report.overall_status == SystemState.FAILED:
                console.print("[bold red]✗ Runtime startup failed[/]")
                sys.exit(1)

            # Start API server in foreground mode
            if foreground:
                console.print(f"[green]✓ Runtime ready[/] - API server at http://{host}:{port}")
                console.print("[dim]Press Ctrl+C to stop[/]")

                # Start uvicorn server
                import uvicorn

                from atlas.interfaces.api.app import create_app

                config = uvicorn.Config(
                    create_app(),
                    host=host,
                    port=port,
                    log_level="info",
                )
                server = uvicorn.Server(config)
                await server.serve()

            else:
                console.print("[yellow]Daemon mode not yet implemented[/]")
                console.print("[dim]Use --foreground for now[/]")
                # TODO: Implement daemon mode
                sys.exit(1)

        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down gracefully...[/]")
            if "atlas" in locals():
                await atlas.close()
            console.print("[green]✓ Shutdown complete[/]")

        except Exception as exc:
            console.print(f"[bold red]✗ Runtime error:[/] {exc}")
            import traceback

            console.print(traceback.format_exc())
            sys.exit(1)

    asyncio.run(run())


def _display_startup_results(health_report: Any) -> None:
    """Display the results of the startup process.

    Args:
        health_report: The health report from runtime supervisor
    """
    from atlas.bootstrap.runtime import SystemState

    # Overall status
    status_color = {
        SystemState.READY: "green",
        SystemState.DEGRADED: "yellow",
        SystemState.FAILED: "red",
    }.get(health_report.overall_status, "red")

    console.print(
        Panel(
            f"[bold {status_color}]{health_report.overall_status.value.upper()}[/]",
            title="[bold]Runtime Status[/]",
            border_style=status_color,
        )
    )

    # Component health table
    if health_report.components:
        table = Table(title="Component Health")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Detail", style="dim")
        table.add_column("Latency", style="dim")

        for name, health in health_report.components.items():
            status_color = {
                "healthy": "green",
                "degraded": "yellow",
                "unavailable": "yellow",
                "failed": "red",
            }.get(health.status.value, "red")

            table.add_row(
                name,
                f"[{status_color}]{health.status.value}[/]",
                health.detail,
                f"{health.latency_ms:.1f}ms" if health.latency_ms > 0 else "N/A",
            )

        console.print(table)

    # Degraded components warning
    if health_report.degraded_components:
        console.print(f"[yellow]⚠ Degraded components:[/] {', '.join(health_report.degraded_components)}")

    # Unavailable capabilities warning
    if health_report.unavailable_capabilities:
        console.print(f"[yellow]⚠ Unavailable capabilities:[/] {', '.join(health_report.unavailable_capabilities)}")

    # Startup timing
    console.print(f"[dim]Startup completed in {health_report.uptime_seconds:.1f}s[/]")


@app.command("stop")
def stop_runtime() -> None:
    """Stop the running ATLAS runtime gracefully.

    This command sends a shutdown signal to the running ATLAS instance.
    """
    console.print("[yellow]Stopping ATLAS runtime...[/]")
    # TODO: Implement runtime stop via signal or API call
    console.print("[dim]Runtime stop not yet implemented[/]")
    console.print("[dim]Use Ctrl+C in the runtime terminal for now[/]")


@app.command("status")
def runtime_status() -> None:
    """Check the status of the ATLAS runtime.

    This command checks if the runtime is running and reports its current state.
    """

    async def check() -> None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Check liveness
                live_resp = await client.get("http://127.0.0.1:8730/api/v1/live")
                live_data = live_resp.json()

                # Check readiness
                ready_resp = await client.get("http://127.0.0.1:8730/api/v1/ready")
                ready_data = ready_resp.json()

                # Check health
                health_resp = await client.get("http://127.0.0.1:8730/api/v1/health")
                health_data = health_resp.json()

                # Display status
                console.print("[bold cyan]ATLAS Runtime Status[/]")
                console.print("Alive: [green]✓[/]" if live_data["alive"] else "[red]✗[/]")
                console.print("Ready: [green]✓[/]" if ready_data["ready"] else "[red]✗[/]")
                console.print(f"State: {ready_data['state']}")
                console.print(f"Uptime: {live_data['uptime_seconds']:.1f}s")
                console.print(f"Overall: {health_data['overall']}")

                if health_data["components"]:
                    table = Table(title="Component Health")
                    table.add_column("Component", style="cyan")
                    table.add_column("Status")

                    for comp in health_data["components"]:
                        status_color = {
                            "healthy": "green",
                            "degraded": "yellow",
                            "unavailable": "yellow",
                            "failed": "red",
                        }.get(comp["status"], "red")

                        table.add_row(
                            comp["name"],
                            f"[{status_color}]{comp['status']}[/]",
                        )

                    console.print(table)

        except httpx.ConnectError:
            console.print("[red]✗ Runtime is not running[/]")
            console.print("[dim]Start it with: atlas start[/]")
        except Exception as exc:
            console.print(f"[red]✗ Error checking status:[/] {exc}")

    asyncio.run(check())


@app.command("restart")
def restart_runtime() -> None:
    """Restart the ATLAS runtime.

    This command stops and restarts the runtime with minimal downtime.
    """
    console.print("[yellow]Restarting ATLAS runtime...[/]")
    # TODO: Implement graceful restart
    console.print("[dim]Runtime restart not yet implemented[/]")
    console.print("[dim]Use: atlas stop && atlas start[/]")
