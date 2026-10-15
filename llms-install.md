# Installing Toolweather (remote MCP server, nothing to run locally)

Toolweather is a hosted MCP server. It needs a free API key, which is instant (no email or signup):

```bash
curl -X POST https://toolweather.toolweather.workers.dev/v1/keys
```

Copy `api_key` from the response (shown once), then add to your MCP settings (e.g. Cline `cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "toolweather": {
      "type": "streamableHttp",
      "url": "https://toolweather.toolweather.workers.dev/mcp",
      "headers": { "X-API-Key": "<your key>" }
    }
  }
}
```

Without a key you can still call the `get_api_key` tool to obtain one.

Verify: call `check_mcp_server_health` with `{"server":"com.ufcalendar/fight-api"}`. The result starts with a one-line `summary`.

Tools: `check_mcp_server_health` (is this MCP server healthy?), `find_alternative_mcp_servers`, `get_api_key`, `watch_server`, `list_watches`, `unwatch_server`.
