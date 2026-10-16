# Installing Toolweather (remote MCP suite, nothing to run locally)

One connection gives you four tools: MCP health, Upstream (API status and model retirements), SpendGate (`approve` / `ask_human` / `block`), and PayCheck (`trusted` / `unknown` / `suspicious` / `block`).

Free to try. No signup. Connect with no key (50 calls/day). Call `get_api_key` to raise the quota to 1,000 calls/day and 3 watches. The key is shown once. Send it as `X-API-Key` after that.

```bash
curl -X POST https://toolweather.toolweather.workers.dev/v1/keys
```

Add to Cursor (one click; Cursor asks before it installs). The link is `cursor://anysphere.cursor-deeplink/mcp/install?name=toolweather&config=<base64>`, and the config decodes to `{"url":"https://toolweather.toolweather.workers.dev/mcp?src=site"}`:

```text
cursor://anysphere.cursor-deeplink/mcp/install?name=toolweather&config=eyJ1cmwiOiJodHRwczovL3Rvb2x3ZWF0aGVyLnRvb2x3ZWF0aGVyLndvcmtlcnMuZGV2L21jcD9zcmM9c2l0ZSJ9
```

Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "toolweather": {
      "url": "https://toolweather.toolweather.workers.dev/mcp?src=site"
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
      "url": "https://toolweather.toolweather.workers.dev/mcp?src=site"
    }
  }
}
```

`claude mcp add --transport http toolweather https://toolweather.toolweather.workers.dev/mcp?src=site`

Claude Desktop (`claude_desktop_config.json`, via `mcp-remote`):

```json
{
  "mcpServers": {
    "toolweather": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://toolweather.toolweather.workers.dev/mcp?src=site"]
    }
  }
}
```

Smithery / remote HTTP. Public streamable HTTP, no API key, no `Authorization` header. Paste the URL into Smithery or any remote HTTP client (Cline `cline_mcp_settings.json` uses this same URL):

```text
https://toolweather.toolweather.workers.dev/mcp?src=site
```

Smithery HTTP config (`type` and `url` only, per https://smithery.ai/docs/use/deep-linking):

```json
{
  "type": "http",
  "url": "https://toolweather.toolweather.workers.dev/mcp?src=site"
}
```

The same anonymous blocks are on https://toolweather.pages.dev/#install. After `get_api_key`, add `"headers": { "X-API-Key": "<key>" }` on URL-based configs.

Verify: call `check_mcp_server_health` with `{"server":"com.ufcalendar/fight-api"}`, `check_api_status` with `{"provider":"openai"}`, `score_spend` with `{"amount":1,"category":"status_check","merchant":"https://example.com"}`, and `check_merchant` with `{"target":"api.openai.com"}`. Health and status results start with a one-line `summary`. `score_spend` returns `approve`, `ask_human`, or `block`. `check_merchant` returns `trusted`, `unknown`, `suspicious`, or `block`.

MCP health tools: `check_mcp_server_health`, `find_alternative_mcp_servers`, `get_api_key`, `watch_server`, `list_watches`, `unwatch_server`.

Upstream (API status) tools on the same endpoint: `check_api_status`, `check_model_deprecation`, `list_recent_api_changes`, `list_providers`, `get_digest`.

SpendGate tools on the same endpoint: `score_spend`, `get_quota`, `spendgate_get_api_key` (SpendGate's `get_api_key`, renamed because `get_api_key` mints a Toolweather key). `get_quota` keeps its name. For a stable SpendGate daily cap through this bundle, call `get_api_key` first (Toolweather key) or pass a SpendGate `sg_live_` key; anonymous callers share IP/session-based caps that can reset on rotating egress.

PayCheck tools on the same endpoint: `check_merchant`, `paycheck_get_quota`, `paycheck_get_api_key`. PayCheck's `get_api_key` is renamed to `paycheck_get_api_key` because `get_api_key` mints a Toolweather key. PayCheck's `get_quota` is renamed to `paycheck_get_quota` because `get_quota` stays SpendGate's free-score cap. `check_merchant` keeps its name. Anonymous bundle clients (`anon:`) stay at PayCheck's 100/day cap. A hex Toolweather client gets PayCheck's keyed free tier (1,000/day). A PayCheck `pc_live_` key is `paycheck_get_api_key`, not `get_api_key`.
