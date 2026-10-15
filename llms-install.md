# Installing Toolweather (remote MCP suite, nothing to run locally)

Toolweather is a hosted MCP suite: MCP server health + AI/dev API status + model deprecations + SpendGate spend scoring. It needs a free API key, which is instant (no email or signup):

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

Verify: call `check_mcp_server_health` with `{"server":"com.ufcalendar/fight-api"}`, `check_api_status` with `{"provider":"openai"}`, and `score_spend` with `{"amount":1,"category":"status_check","merchant":"https://example.com"}`. Health and status results start with a one-line `summary`. `score_spend` returns `approve`, `ask_human`, or `block`.

MCP health tools: `check_mcp_server_health`, `find_alternative_mcp_servers`, `get_api_key`, `watch_server`, `list_watches`, `unwatch_server`.

Upstream (API status) tools on the same endpoint: `check_api_status`, `check_model_deprecation`, `list_recent_api_changes`, `list_providers`, `get_digest`.

SpendGate tools on the same endpoint: `score_spend`, `get_quota`, `spendgate_get_api_key` (SpendGate's `get_api_key`, renamed because `get_api_key` mints a Toolweather key). `get_quota` keeps its name. For a stable SpendGate daily cap through this bundle, call `get_api_key` first (Toolweather key) or pass a SpendGate `sg_live_` key; anonymous callers share IP/session-based caps that can reset on rotating egress.
