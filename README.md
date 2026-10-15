# Toolweather

**One connection gives you four tools.** Add `https://toolweather.toolweather.workers.dev/mcp` once.

| | What you get | Tools |
| --- | --- | --- |
| **MCP health** | Is this MCP server or tool safe to call right now? About 2,000 servers from the [official MCP registry](https://registry.modelcontextprotocol.io), probed every 15 minutes. | `check_mcp_server_health` · `find_alternative_mcp_servers` · `watch_server` · `list_watches` · `unwatch_server` · `get_api_key` |
| **Upstream** | API status and model retirements, across about 40 providers. | `check_api_status` · `check_model_deprecation` · `list_recent_api_changes` · `list_providers` · `get_digest` |
| **SpendGate** | `approve` / `ask_human` / `block` before an agent pays. | `score_spend` · `get_quota` · `spendgate_get_api_key` |
| **PayCheck** | `trusted` / `unknown` / `suspicious` / `block` before an agent pays a merchant, host, or wallet. | `check_merchant` · `paycheck_get_quota` · `paycheck_get_api_key` |

- Status page: https://toolweather.pages.dev
- API / MCP: https://toolweather.toolweather.workers.dev/mcp
- Upstream REST + x402 (still independent): https://upstream.toolweather.workers.dev · https://upstream-api.pages.dev
- SpendGate REST + x402 (still independent): https://spendgate.toolweather.workers.dev
- PayCheck REST + x402 (still independent): https://paycheck.toolweather.workers.dev
- Contact: toolweather@mail.grokbot.com

## Install

Free to try. No signup. Connect with no key (50 calls/day). Call `get_api_key` (it works with no key) to raise the quota to 1,000 calls/day and 3 watches. The key is shown once. Send it as `X-API-Key` after that. A few new keys per network per day, plus a global daily cap.

You can also mint a key first with `curl -X POST https://toolweather.toolweather.workers.dev/v1/keys`, or click **Get a free key** on https://toolweather.pages.dev.

### Add to Cursor

[![Add to Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](cursor://anysphere.cursor-deeplink/mcp/install?name=toolweather&config=eyJ1cmwiOiJodHRwczovL3Rvb2x3ZWF0aGVyLnRvb2x3ZWF0aGVyLndvcmtlcnMuZGV2L21jcCJ9)

```text
cursor://anysphere.cursor-deeplink/mcp/install?name=toolweather&config=eyJ1cmwiOiJodHRwczovL3Rvb2x3ZWF0aGVyLnRvb2x3ZWF0aGVyLndvcmtlcnMuZGV2L21jcCJ9
```

Checked against the [Cursor MCP install link](https://cursor.com/docs/mcp/install-links) format: `cursor://anysphere.cursor-deeplink/mcp/install?name=$NAME&config=$BASE64`. `name` is the server name. `config` is the base64 of `JSON.stringify` of the single server object. Cursor's own example encodes `{"command":"npx","args":["-y","@modelcontextprotocol/server-postgres","postgresql://localhost/mydb"]}`. This link encodes `{"url":"https://toolweather.toolweather.workers.dev/mcp"}`. Cursor asks before it installs.

### Cursor

`~/.cursor/mcp.json` or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "toolweather": {
      "url": "https://toolweather.toolweather.workers.dev/mcp"
    }
  }
}
```

### Claude Code

`.mcp.json`. Set `"type": "http"`. Claude Code treats an entry that has a `url` and no `type` as stdio and skips it.

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

```sh
claude mcp add --transport http toolweather https://toolweather.toolweather.workers.dev/mcp
```

### Claude Desktop

`claude_desktop_config.json` (remote HTTP through the `mcp-remote` bridge):

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

### Raise the quota

After `get_api_key`, add the key.

Cursor and Claude Code, inside the `toolweather` object:

```json
"headers": { "X-API-Key": "<key from get_api_key>" }
```

Claude Code CLI:

```sh
claude mcp add --transport http toolweather https://toolweather.toolweather.workers.dev/mcp --header "X-API-Key: <key>"
```

Claude Desktop — header arg plus env:

```json
{
  "mcpServers": {
    "toolweather": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://toolweather.toolweather.workers.dev/mcp",
               "--header", "X-API-Key:${TW_KEY}"],
      "env": { "TW_KEY": "<key from get_api_key>" }
    }
  }
}
```

### VS Code

`.vscode/mcp.json`:

```json
{
  "servers": {
    "toolweather": {
      "type": "http",
      "url": "https://toolweather.toolweather.workers.dev/mcp"
    }
  }
}
```

To send a key, prompt for it:

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

**Auth-gated servers:** without credentials, Toolweather follows the OAuth discovery chain (RFC 9728 protected-resource metadata, RFC 8414 / OIDC auth-server metadata) and checks that the authorization, token and registration endpoints answer over TLS. `auth.state` is `auth_ok` (login flow reachable; `dynamic_client_registration` tells you if registration is open), `auth_suspect` (endpoints reachable but one returned a 5xx to our parameter-less check; verdict `caution`), `auth_broken` (OAuth advertised but metadata is missing or an endpoint is unreachable/TLS fails; verdict `unsafe`: new logins will fail), or `auth_opaque` (needs credentials, no OAuth discovery, e.g. API keys). No client is ever registered and no credentials are sent. To check a server with your own token, add `X-Upstream-Authorization: Bearer <token>` (REST) or `bearer_token` (MCP tool argument): Toolweather runs one authenticated initialize + tools/list and returns `authenticated_check`. The token is used for that request only, never stored or logged, and the result is not cached.

**Track record:** `GET /v1/accuracy` (no key) and the status page show, per day, how often a verdict matched the very next scheduled check of the same server. It measures how stable verdicts are between checks (~15 min), not whether tool calls succeed.

**Fresh results:** if the stored check is older than 5 minutes, or you pass `fresh=true`, Toolweather runs a quick read-only `initialize` handshake against the server right then (4 s timeout) and returns that result (`source: live_probe`, `live: {...}`). Live results are cached briefly (a server is re-probed at most once a minute) and each key gets 200 live checks a day; after that you get the newest cached result.

Free tier: 1,000 calls per key per day, or 50/day anonymous with no key. Over quota: pay-per-call via x402 ($0.001/check, $0.003/live, $0.01/batch, $0.05/30d watch).

> **Getting `HTTP 403` with body `error code: 1010`?** That is Cloudflare's edge rejecting Python's default `Python-urllib/x.y` User-Agent before the request reaches Toolweather. It applies to all free `*.workers.dev` / `*.pages.dev` addresses and can't be disabled there. It is not a key problem. Send any other User-Agent:
>
> ```python
> import json, urllib.request
> req = urllib.request.Request("https://toolweather.toolweather.workers.dev/v1/keys", method="POST", headers={"User-Agent": "my-agent/1.0"})
> key = json.load(urllib.request.urlopen(req))["api_key"]
> ```
>
> `requests`, `httpx`, `aiohttp`, curl, Node and browsers work as-is.
Get a key with `curl -X POST https://toolweather.toolweather.workers.dev/v1/keys`.

### MCP

Add `https://toolweather.toolweather.workers.dev/mcp` as a remote MCP server. No signup: connect with no key (50 calls/day). `get_api_key` raises that to 1,000 calls/day. Then send header `X-API-Key: <key>`.

**MCP health:** `get_api_key()` (no key needed), `check_mcp_server_health(server, tool?)` (alias `is_safe_to_call`),
`find_alternative_mcp_servers(query, limit?)` (alias `suggest_alternatives`), and change alerts
`watch_server(server, webhook_url)`, `list_watches()`, `unwatch_server(id)`.

**Upstream (proxied on the same MCP):** `check_api_status(provider)` (alias `get_status`),
`check_model_deprecation(model)` (alias `get_model`), `list_recent_api_changes` (alias `get_changes`),
`list_providers()`, `get_digest(date?)`. Over Upstream's free quota these return an x402 payment requirement
(USDC on Base); Upstream's own REST `/x402/*` endpoints stay live for clients that pay directly.

**SpendGate (proxied on the same MCP):** `score_spend(amount, category?, merchant?, daily_budget_remaining?, ...)`,
`get_quota(api_key?)`, `spendgate_get_api_key(label?)`. Call `score_spend` before an agent pays.
`approve` means pay, `ask_human` means stop and ask a person, `block` means do not pay.

SpendGate's own tool `get_api_key` is exposed here as `spendgate_get_api_key`. `get_api_key` already mints a
Toolweather key (`tw_…`, 1,000 checks/day). `get_quota` does not match any Toolweather tool, so it keeps
SpendGate's name and means SpendGate's free scores, not Toolweather's daily check quota. `score_spend` is unchanged.
An `api_key` argument on these tools is a SpendGate key (`sg_live_…`), not a Toolweather key. Send the Toolweather
key as `X-API-Key` the same way as for the other suite tools.

SpendGate tools do not consume the Toolweather call quota. They keep SpendGate's own limits: 100 scores/day
anonymous, 1,000/day with a SpendGate key, then $0.001 per score via x402 (USDC on Base). `get_quota` and
`spendgate_get_api_key` are not billed. SpendGate's own REST stays at https://spendgate.toolweather.workers.dev.
For a stable SpendGate daily cap through this bundle, call `get_api_key` first (a Toolweather key) or pass a
SpendGate `sg_live_` key. Anonymous callers share IP/session-based caps that can reset on rotating egress.

**PayCheck (proxied on the same MCP):** `check_merchant(target, api_key?)`, `paycheck_get_quota(api_key?)`,
`paycheck_get_api_key()`. Call `check_merchant` before an agent pays a domain, https URL, or `0x` wallet.
`trusted` means an allowlisted https API, `unknown` or `suspicious` means verify out of band, `block` means do not pay.
PayCheck does not send money.

PayCheck's own tool `get_api_key` is exposed here as `paycheck_get_api_key`. `get_api_key` already mints a
Toolweather key. PayCheck's `get_quota` is exposed as `paycheck_get_quota` because `get_quota` stays SpendGate's
free-score cap. `check_merchant` does not collide, so it keeps PayCheck's name. An `api_key` argument on these
tools is a PayCheck key (`pc_live_…`), not a Toolweather key and not a SpendGate key. Send the Toolweather key
as `X-API-Key` the same way as for the other suite tools.

PayCheck tools do not consume the Toolweather call quota. They keep PayCheck's own limits: 100 checks/day
anonymous or for an `anon:` bundle client, 1,000/day with a PayCheck key or a hex `x-tw-bundle-client`
(a Toolweather key hash, or a stable MCP session hash), then $0.001 per check via x402 (USDC on Base).
`paycheck_get_quota` and `paycheck_get_api_key` are not billed. PayCheck's own REST stays at
https://paycheck.toolweather.workers.dev (`/v1/check`, `/v1/quota`, `/v1/keys`, `/mcp`).

The endpoint answers both 2026-07-28 `server/discover` and legacy `initialize`.

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

## Toolweather suite architecture

The public MCP at `toolweather…/mcp` lists MCP-health, Upstream, SpendGate, and PayCheck tools. Upstream, SpendGate, and PayCheck
tool calls are proxied to those Workers (same Cloudflare account; service binding `UPSTREAM` / `SPENDGATE` / `PAYCHECK`,
HTTPS fallback to each worker's `/mcp`). When `BUNDLE_PROXY_SECRET` is set, the proxy sends `x-tw-bundle-secret`
and `x-tw-bundle-client` (a hash of the caller, never the raw key, session id, or IP). Each product's own Worker, REST API,
and x402 payment endpoints remain unchanged for existing clients.

`x-tw-bundle-client` is chosen as follows:

- Toolweather free key (`plan === "free_key"`, hex without `anon:`): that key's existing sha256, unchanged.
- Anonymous with an `Mcp-Session-Id` of 8–128 safe characters: `sha256("tw-mcp-session|" + session)` as 64 hex characters (no `anon:` prefix), so the cap stays on one client when cloud egress IPs rotate.
- Anonymous with no session header: `anon:` plus the existing daily IP hash, so SpendGate and PayCheck can apply the anonymous limit (100/day).

Upstream's free-tier check only accepts `/^[a-f0-9]{16,128}$/i`. When the client id still has an `anon:` prefix, the proxy strips that prefix and forwards the same hash. SpendGate and PayCheck still receive the `anon:` form. PayCheck treats `anon:` as 100/day and a hex client as its keyed free tier (1,000/day).

SpendGate's code is not in this repo. A call with an unrecognized secret is still anonymous on SpendGate, so per-client quota only works after SpendGate accepts the same secret Upstream does and keys its cap by `x-tw-bundle-client`. For a stable SpendGate daily cap through this bundle, call `get_api_key` first (Toolweather key) or pass a SpendGate `sg_live_…` key. Anonymous callers share IP/session-based caps that can reset on rotating egress.

PayCheck's code is not in this repo. It receives the same `x-tw-bundle-secret` and `x-tw-bundle-client` headers as SpendGate. An `anon:` client stays at 100 checks/day. A hex client (a Toolweather key hash, or a stable MCP session hash) gets PayCheck's keyed free tier of 1,000/day. A PayCheck `pc_live_…` key passed as `api_key` selects that key's cap.

## How it works

- `mcphealth.py`: registry fetch, polite async prober, SQLite storage, tool-list diffing
  (breaking = tool removed, required param added, param removed or type changed).
- `.github/workflows/probe.yml`: runs every 15 min on GitHub Actions, pushes precomputed verdicts to Cloudflare D1
  (`scripts/ingest_d1.py`) and redeploys the status page (`build_site.py`) to Cloudflare Pages.
- `worker/`: Cloudflare Worker serving the API, MCP endpoint and badges from D1.
- `tw_core.py`, `api.py`, `mcp_server.py`: local (self-hosted) versions of the same API.

## License

MIT
