# Installing Toolweather (remote MCP suite, nothing to run locally)

One connection gives you three tools: MCP health, Upstream (API status and model retirements), and SpendGate (`approve` / `ask_human` / `block`).

Free to try. No signup. Connect with no key (50 calls/day). Call `get_api_key` to raise the quota to 1,000 calls/day and 3 watches. The key is shown once. Send it as `X-API-Key` after that.

```bash
curl -X POST https://toolweather.toolweather.workers.dev/v1/keys
```

Add to Cursor (one click; Cursor asks before it installs). The link is `cursor://anysphere.cursor-deeplink/mcp/install?name=toolweather&config=<base64>`, and the config decodes to `{"url":"https://toolweather.toolweather.workers.dev/mcp"}`:

```text
cursor://anysphere.cursor-deeplink/mcp/install?name=toolweather&config=eyJ1cmwiOiJodHRwczovL3Rvb2x3ZWF0aGVyLnRvb2x3ZWF0aGVyLndvcmtlcnMuZGV2L21jcCJ9
```

Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "toolweather": {
      "url": "https://toolweather.toolweather.workers.dev/mcp"
    }
  }
}
```

Claude Code (`.mcp.json`, `"type": "http"` required):

```json
{
  "mcpServers": {
    "toolweather": {
      "type": "http",
      "url": "https://toolweather.toolweather.workers.dev/mcp"
    }
  }
}
```

`claude mcp add --transport http toolweather https://toolweather.toolweather.workers.dev/mcp`

Claude Desktop (`claude_desktop_config.json`, via `mcp-remote`):

```json
{
  "mcpServers": {
    "toolweather": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://toolweather.toolweather.workers.dev/mcp"]
    }
  }
}
```

Other clients (for example Cline `cline_mcp_settings.json`) can use the same URL. After `get_api_key`, add `"headers": { "X-API-Key": "<key>" }` on URL-based configs.

Verify: call `check_mcp_server_health` with `{"server":"com.ufcalendar/fight-api"}`, `check_api_status` with `{"provider":"openai"}`, and `score_spend` with `{"amount":1,"category":"status_check","merchant":"https://example.com"}`. Health and status results start with a one-line `summary`. `score_spend` returns `approve`, `ask_human`, or `block`.

MCP health tools: `check_mcp_server_health`, `find_alternative_mcp_servers`, `get_api_key`, `watch_server`, `list_watches`, `unwatch_server`.

Upstream (API status) tools on the same endpoint: `check_api_status`, `check_model_deprecation`, `list_recent_api_changes`, `list_providers`, `get_digest`.

SpendGate tools on the same endpoint: `score_spend`, `get_quota`, `spendgate_get_api_key` (SpendGate's `get_api_key`, renamed because `get_api_key` mints a Toolweather key). `get_quota` keeps its name. For a stable SpendGate daily cap through this bundle, call `get_api_key` first (Toolweather key) or pass a SpendGate `sg_live_` key; anonymous callers share IP/session-based caps that can reset on rotating egress.
