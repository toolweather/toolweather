"""Toolweather MCP server (same functions as the API).
stdio:  .venv/bin/python mcp_server.py
http:   .venv/bin/python mcp_server.py --http  (streamable HTTP on 127.0.0.1:8788/mcp, key via TW_API_KEY env or tool arg)"""
import os, sys
from mcp.server.mcpserver import MCPServer
import tw_core as tw

mcp = MCPServer("toolweather", version="0.1")

def _gate(api_key):
    k = api_key or os.environ.get("TW_API_KEY")
    ok, used, lim = tw.check_and_count(k or "")
    if not ok: raise ValueError("invalid API key" if lim == 0 else f"free daily limit of {lim} reached")

@mcp.tool()
def is_safe_to_call(server: str, tool: str | None = None, api_key: str | None = None) -> dict:
    """Check whether an MCP server (registry name or URL), and optionally one of its tools, is safe to call right now.
    Returns verdict safe/caution/unsafe, live status, latency, last tool change (breaking?), spec readiness, auth info, last_checked."""
    _gate(api_key); return tw.is_safe_to_call(server, tool)

@mcp.tool()
def suggest_alternatives(query: str, limit: int = 5, api_key: str | None = None) -> dict:
    """Find working MCP servers with similar tools. query = a server name/URL, or capability text like 'send email'."""
    _gate(api_key); return tw.suggest_alternatives(query, min(limit, 20))

if __name__ == "__main__":
    mcp.run("streamable-http", host="127.0.0.1", port=8788) if "--http" in sys.argv else mcp.run("stdio")
