# Embed a free status badge

Free. No signup. Paste one line into your MCP server's README.

The image is the live badge from Toolweather probes (the same checks as the status page, about every 15 minutes). The link opens that server's public status page: current health, latency, and 2026-07-28 spec readiness.

```markdown
[![Toolweather](https://toolweather.toolweather.workers.dev/badge/<registry-name>.svg)](https://toolweather.pages.dev/servers/<slug>/)
```

`<registry-name>` is the official MCP registry name, for example `com.ufcalendar/fight-api`.

`<slug>` is that name made safe for a URL path: replace each `/` with `--`. Other characters that are not letters, digits, `.`, `_`, or `-` become `-`. If two names would share a slug, the alphabetically later one gets a short hash suffix. Each server page shows the finished snippet with the real name filled in.

Example:

```markdown
[![Toolweather](https://toolweather.toolweather.workers.dev/badge/com.ufcalendar/fight-api.svg)](https://toolweather.pages.dev/servers/com.ufcalendar--fight-api/)
```

That image URL is the existing badge route (`/badge/<registry-name>.svg` on the Toolweather Worker). It already returns an SVG (`healthy`, `auth required`, `down`, or `unknown`). You do not need a second badge service, an account, or an API key.

Find a server on the [readiness leaderboard](https://toolweather.pages.dev/servers/).
