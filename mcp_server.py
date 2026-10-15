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
    """Read whether one remote MCP server, and optionally one tool on it, is safe to call right now.
    Use before adding or calling that server, or after its calls fail. Do not use it to search by capability (suggest_alternatives).
    server is a registry name or MCP endpoint URL, not a homepage. tool, if set, forces verdict unsafe when that name is missing.
    api_key is a Toolweather tw_ key; omit only for the smaller anonymous daily cap. Each call counts on that quota.
    Returns verdict (safe|caution|unsafe), status, latency, last tool change, spec readiness, auth, and last_checked. No output schema."""
    _gate(api_key); return tw.is_safe_to_call(server, tool)

@mcp.tool()
def suggest_alternatives(query: str, limit: int = 5, api_key: str | None = None) -> dict:
    """Search the index for healthy MCP servers by capability, or for a replacement for one broken server.
    Use when is_safe_to_call is unsafe or unknown, or when you lack a capability. Do not use it to judge one server you already chose (is_safe_to_call).
    query is a capability phrase such as 'send email', or a server name/URL to replace. limit is 1-20 ranked rows (default 5); there is no cursor.
    Does not call the remote servers. Unsafe rows are dropped. Counts on the same Toolweather quota as is_safe_to_call.
    Returns query and alternatives[] with server, url, verdict, latency, and matching tools. No output schema."""
    _gate(api_key); return tw.suggest_alternatives(query, min(limit, 20))

if __name__ == "__main__":
    mcp.run("streamable-http", host="127.0.0.1", port=8788) if "--http" in sys.argv else mcp.run("stdio")
