# Toolweather

**Is this MCP tool safe to call right now?** Toolweather watches the remote MCP servers listed in the
[official MCP registry](https://registry.modelcontextprotocol.io) and lets agents ask, before they call a tool,
whether the server is up, how fast it is, whether its tools just changed in a breaking way, whether it is ready
for the 2026-07-28 spec, and whether it needs auth.

- Status page: https://toolweather.pages.dev
- API: https://toolweather.toolweather.workers.dev
- MCP endpoint (streamable HTTP): https://toolweather.toolweather.workers.dev/mcp
- Contact: toolweather@mail.grokbot.com

## Install

Get a free key instantly (1,000 calls/day, 3 watches), no email or signup:

```sh
curl -X POST https://toolweather.toolweather.workers.dev/v1/keys
```

or click **Get a free key** on https://toolweather.pages.dev, or call the `get_api_key` MCP tool (works without a key). The key is shown once. Limits: a few new keys per network per day, plus a global daily cap. Then add the remote MCP server.

**Claude Desktop** (`claude_desktop_config.json`; uses the `mcp-remote` bridge):

```json
{
  "mcpServers": {
    "toolweather": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://toolweather.toolweather.workers.dev/mcp",
               "--header", "X-API-Key:${TW_KEY}"],
      "env": { "TW_KEY": "<your-key>" }
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json` or `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "toolweather": {
      "url": "https://toolweather.toolweather.workers.dev/mcp",
      "headers": { "X-API-Key": "<your-key>" }
    }
  }
}
```

**VS Code** (`.vscode/mcp.json`):

```json
{
  "inputs": [{ "type": "promptString", "id": "tw-key", "description": "Toolweather API key", "password": true }],
  "servers": {
    "toolweather": {
      "type": "http",
      "url": "https://toolweather.toolweather.workers.dev/mcp",
      "headers": { "X-API-Key": "${input:tw-key}" }
    }
  }
}
```

**Claude Code:** `claude mcp add --transport http toolweather https://toolweather.toolweather.workers.dev/mcp --header "X-API-Key: <your-key>"`

Discovery files: [OpenAPI 3.1](https://toolweather.toolweather.workers.dev/openapi.json) ·
[MCP server card](https://toolweather.toolweather.workers.dev/.well-known/mcp) ·
[llms.txt](https://toolweather.pages.dev/llms.txt) · registry name `io.github.toolweather/toolweather`.

## API

```bash
curl -H "X-API-Key: $TW_KEY" "https://toolweather.toolweather.workers.dev/v1/is_safe_to_call?server=com.ufcalendar/fight-api&tool=get_events"
curl -H "X-API-Key: $TW_KEY" "https://toolweather.toolweather.workers.dev/v1/suggest_alternatives?q=send%20email"
```

`is_safe_to_call` returns `verdict` (`safe` / `caution` / `unsafe` / `unknown`), reasons, live status
(`ok` / `auth_required` / `failed`), latency, protocol version, spec readiness, auth/OAuth discovery info,
the last tool change (and whether it was breaking), `last_checked`, plus `checked_at` and `age_seconds`.

**Fresh results:** if the stored check is older than 5 minutes, or you pass `fresh=true`, Toolweather runs a quick read-only `initialize` handshake against the server right then (4 s timeout) and returns that result (`source: live_probe`, `live: {...}`). Live results are cached briefly (a server is re-probed at most once a minute) and each key gets 200 live checks a day; after that you get the newest cached result.

Free tier: 1,000 calls per key per day.

> **Python users:** Cloudflare's `*.workers.dev` edge rejects Python's default `Python-urllib/x.y` User-Agent (error 1010). Send any descriptive User-Agent, e.g. `urllib.request.Request(url, headers={"User-Agent": "my-agent/1.0", "X-API-Key": key})`. `requests`, `httpx`, curl and Node work as-is.
Get a key with `curl -X POST https://toolweather.toolweather.workers.dev/v1/keys`.

### MCP

Add `https://toolweather.toolweather.workers.dev/mcp` as a remote MCP server with header `X-API-Key: <key>`.
Tools: `get_api_key()` (no key needed), `is_safe_to_call(server, tool?)`, `suggest_alternatives(query, limit?)`, and change alerts:
`watch_server(server, webhook_url)`, `list_watches()`, `unwatch_server(id)`. The endpoint answers both
2026-07-28 `server/discover` and legacy `initialize`.

### Change alerts (webhooks)

Watch a server and get an HTTPS POST when its verdict changes (`safe` / `caution` / `unsafe`), it starts
requiring auth, or its tool list has a breaking change. Free tier: 3 watches per key.

```bash
curl -X POST -H "X-API-Key: $TW_KEY" -H "content-type: application/json" \
  -d '{"server":"com.ufcalendar/fight-api","webhook_url":"https://example.com/hooks/toolweather"}' \
  https://toolweather.toolweather.workers.dev/v1/watches      # returns id + secret (shown once)
curl -H "X-API-Key: $TW_KEY" https://toolweather.toolweather.workers.dev/v1/watches
curl -X DELETE -H "X-API-Key: $TW_KEY" https://toolweather.toolweather.workers.dev/v1/watches/<id>
```

Event types: `verdict_changed`, `auth_required`, `breaking_tool_change`. Each POST carries
`X-Toolweather-Event`, `X-Toolweather-Delivery` and `X-Toolweather-Signature: t=<unix>,v1=<hex>` where
`v1 = HMAC-SHA256(secret, "<t>.<raw body>")`. Verify it (and reject old `t`) before trusting the payload:

```python
import hmac, hashlib, time
def verify(secret, header, raw_body):
    parts = dict(p.split("=", 1) for p in header.split(","))
    mac = hmac.new(secret.encode(), f"{parts['t']}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, parts["v1"]) and abs(time.time() - int(parts["t"])) < 600
```

Webhooks must be public `https` URLs; private, loopback and link-local addresses are rejected.
Reply 2xx within 10 s. Failed deliveries retry after 1, 5, 30, 120 and 360 minutes; after 3 failed
deliveries in a row the watch is disabled (see `GET /v1/watches`). Changes are detected every 5 minutes
by a Cloudflare cron trigger, against probes that run every 15 minutes.

### Badge

```markdown
[![Toolweather](https://toolweather.toolweather.workers.dev/badge/<registry-name>.svg)](https://toolweather.pages.dev)
```

## Probing policy (read-only)

If you run an MCP server, here is exactly what we do:

- We only send `server/discover` (2026-07-28) and/or `initialize` + `notifications/initialized`, then `tools/list`,
  and close the session. **We never call tools**, read resources or run prompts.
- For servers that answer 401/403 we read `WWW-Authenticate` and fetch `/.well-known/oauth-protected-resource` (GET only).
- At most one in-flight request per host, at least 2 s between requests to the same host, 12 s timeout, one check
  per server every 15 minutes.
- User-Agent: `mcp-health-probe/0.1 (read-only; initialize+tools/list only)`.
- To opt out or report a problem, email toolweather@mail.grokbot.com.

## How it works

- `mcphealth.py`: registry fetch, polite async prober, SQLite storage, tool-list diffing
  (breaking = tool removed, required param added, param removed or type changed).
- `.github/workflows/probe.yml`: runs every 15 min on GitHub Actions, pushes precomputed verdicts to Cloudflare D1
  (`scripts/ingest_d1.py`) and redeploys the status page (`build_site.py`) to Cloudflare Pages.
- `worker/`: Cloudflare Worker serving the API, MCP endpoint and badges from D1.
- `tw_core.py`, `api.py`, `mcp_server.py`: local (self-hosted) versions of the same API.

## License

MIT
