# Toolweather

**Is this MCP tool safe to call right now?** Toolweather watches the remote MCP servers listed in the
[official MCP registry](https://registry.modelcontextprotocol.io) and lets agents ask, before they call a tool,
whether the server is up, how fast it is, whether its tools just changed in a breaking way, whether it is ready
for the 2026-07-28 spec, and whether it needs auth.

- Status page: https://toolweather.pages.dev
- API: https://toolweather.toolweather.workers.dev
- MCP endpoint (streamable HTTP): https://toolweather.toolweather.workers.dev/mcp
- Contact: toolweather@mail.grokbot.com

## API

```bash
curl -H "X-API-Key: $TW_KEY" "https://toolweather.toolweather.workers.dev/v1/is_safe_to_call?server=com.ufcalendar/fight-api&tool=get_events"
curl -H "X-API-Key: $TW_KEY" "https://toolweather.toolweather.workers.dev/v1/suggest_alternatives?q=send%20email"
```

`is_safe_to_call` returns `verdict` (`safe` / `caution` / `unsafe` / `unknown`), reasons, live status
(`ok` / `auth_required` / `failed`), latency, protocol version, spec readiness, auth/OAuth discovery info,
the last tool change (and whether it was breaking), and `last_checked`.

Free tier: 1,000 calls per key per day. Email toolweather@mail.grokbot.com for a key.

### MCP

Add `https://toolweather.toolweather.workers.dev/mcp` as a remote MCP server with header `X-API-Key: <key>`.
Tools: `is_safe_to_call(server, tool?)` and `suggest_alternatives(query, limit?)`. The endpoint answers both
2026-07-28 `server/discover` and legacy `initialize`.

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
