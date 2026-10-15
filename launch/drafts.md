## X post
I built Toolweather: a free API that tells AI agents if an MCP server/tool is safe to call right now. It checks 2,000 public servers every 15 min. Right now: 47% open and healthy, 51% need auth, 164 ms median handshake.
https://toolweather.pages.dev

## Thread
1/ Why: agents call MCP tools blindly. Servers go down, need logins, or rename tools. Toolweather answers "safe / caution / unsafe" with a reason before the call.
2/ What's tracked: handshake, auth, latency, and tool lists for 14,725 tools. Changes to a tool's schema get flagged as breaking.
3/ It also tracks readiness for the 2026-07-28 spec, and asked "web search" or "send email" it suggests servers that work right now.
4/ Free: 1,000 calls/day per key, REST or /mcp. Code: https://github.com/toolweather/toolweather. Key: email toolweather@mail.grokbot.com. Feedback welcome.

## Show HN
Title: Show HN: Toolweather – Is this MCP server safe to call right now?
Text: Toolweather checks the top 2,000 public MCP servers every 15 minutes (handshake, auth, latency, tool list) and exposes one question to agents: is server X / tool Y safe to call now? Answers are safe, caution, or unsafe with a reason, plus working alternatives. Latest round: 47% open and healthy, 51% behind auth, 164 ms median handshake, 14,725 tools tracked. It also flags breaking tool changes and 2026-07-28 spec readiness. Runs on free GitHub Actions + Cloudflare Workers. Free key, 1,000 calls/day. Status page: https://toolweather.pages.dev Code: https://github.com/toolweather/toolweather. Known limits: scheduled checks can start a few minutes late, and auth-gated servers are only checked up to the login.

## r/mcp
Title: I made a free health check API for MCP servers (2,000 servers, every 15 min)
Body: Toolweather probes the top 2,000 public MCP servers every 15 minutes and tells your agent whether a server/tool is safe to call right now, with alternatives if not. Latest: 47% healthy, 51% need auth, 164 ms median, 14,725 tools tracked. Also tracks breaking tool changes and 2026-07-28 spec readiness. Free 1,000 calls/day, available as REST or an MCP endpoint at /mcp. Status page: https://toolweather.pages.dev · Code: https://github.com/toolweather/toolweather. Feedback on false positives very welcome.
