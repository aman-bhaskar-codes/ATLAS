"""A tiny REAL MCP server (official SDK) used as a stdio test fixture.

Exposes: echo tool, add tool (structured content), failing tool.
Run: python echo_server.py
"""

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="atlas-echo", version="1.0.0", instructions="Echo fixture server.")


@server.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo:{text}"


@server.tool()
def add(a: int, b: int) -> dict:
    """Add two integers (structured output)."""
    return {"sum": a + b}


@server.tool()
def always_fails() -> str:
    """Always reports is_error."""
    raise RuntimeError("intentional failure")


if __name__ == "__main__":
    server.run()
