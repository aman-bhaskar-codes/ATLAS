"""A REAL MCP server exposing 25 tools to force tools/list pagination
(§107). Pagination page size is protocol/server controlled; 25 tools with
descriptions exercises multi-page behavior when the client caps page size."""

from mcp.server.mcpserver import MCPServer

server = MCPServer(name="atlas-paginated", version="1.0.0")


def _make(idx: int):
    async def tool(idx: int = idx) -> str:
        """Deterministic stub tool."""
        return f"result-{idx}"

    return tool


for i in range(25):
    server.add_tool(_make(i), name=f"tool_{i:02d}", description=f"Stub tool {i}")

if __name__ == "__main__":
    server.run()
