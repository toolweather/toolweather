"""Generate the Toolweather status site from mcp_health.db.

Writes site/index.html, then the readiness leaderboard, per-server pages,
sitemap.xml, and robots.txt (see build_servers.py). probe.yml deploys site/.
"""
import html, sqlite3, statistics, datetime, os
import build_servers
from site_install import INSTALL_CSS, install_section
DB = "mcp_health.db"; OUT = "site/index.html"
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
LATEST = """SELECT e.server_name, e.url, p.* FROM endpoints e JOIN probes p ON p.id=(SELECT max(id) FROM probes WHERE endpoint_id=e.id)"""
rows = c.execute(LATEST).fetchall()
by = {}
for r in rows: by[r["status"]] = by.get(r["status"], 0) + 1
ok = [r for r in rows if r["status"] == "ok"]
med = statistics.median([r["latency_ms"] for r in ok]) if ok else 0
ready = {}
for r in ok: ready[r["spec_readiness"] or "unknown"] = ready.get(r["spec_readiness"] or "unknown", 0) + 1
tools_total = sum(r["tool_count"] or 0 for r in ok)
# reliability: uptime over all probes, needs >=2 checks
rel = c.execute("""SELECT e.server_name, e.url, count(*) n, avg(p.status='ok') up, avg(CASE WHEN p.status='ok' THEN latency_ms END) lat,
  max(p.tool_count) tc FROM probes p JOIN endpoints e ON e.id=p.endpoint_id GROUP BY e.id
  HAVING n>=2 AND up=1 ORDER BY n DESC, lat ASC LIMIT 12""").fetchall()
broken = c.execute("""SELECT e.server_name, e.url, p.error, p.ts, prev.status prev FROM endpoints e
  JOIN probes p ON p.id=(SELECT max(id) FROM probes WHERE endpoint_id=e.id)
  LEFT JOIN probes prev ON prev.id=(SELECT max(id) FROM probes WHERE endpoint_id=e.id AND id<p.id)
  WHERE p.status='failed' ORDER BY (prev.status='ok') DESC, p.ts DESC LIMIT 10""").fetchall()
changes = c.execute("""SELECT e.server_name, c.severity, c.kind, c.tool, c.ts FROM changes c JOIN endpoints e ON e.id=c.endpoint_id
  ORDER BY c.ts DESC LIMIT 8""").fetchall()
last = max(r["ts"] for r in rows); esc = html.escape
when = datetime.datetime.fromtimestamp(last, datetime.timezone.utc).strftime("%b %d, %Y %H:%M UTC")
total = len(rows); pct = lambda n: f"{100*n/max(total,1):.0f}%"
rd_order = [("dual", "Dual-era (2026-07-28 + legacy)", "#7A8F66"), ("modern_only", "2026-07-28 only", "#A67C52"),
            ("legacy_only", "Legacy only — not ready", "var(--leg)")]
rd_tot = sum(ready.values()) or 1
bar = "".join(f'<div style="width:{100*ready.get(k,0)/rd_tot:.1f}%;background:{col}" title="{lab}"></div>' for k, lab, col in rd_order)
legend = "".join(f'<li><span class="dot" style="background:{col}"></span>{lab}<b>{ready.get(k,0)}</b><em>{100*ready.get(k,0)/rd_tot:.0f}%</em></li>' for k, lab, col in rd_order)
def short(u): return esc(u.replace("https://", "")[:48])
rel_html = "".join(f'<tr><td><div class="nm">{esc(r["server_name"])}</div><div class="u">{short(r["url"])}</div></td><td><span class="pill ok">100%</span></td>'
                   f'<td>{r["lat"]:.0f} ms</td><td>{r["tc"] or 0}</td><td>{r["n"]}</td></tr>' for r in rel)
brk_html = "".join(f'<tr><td><div class="nm">{esc(r["server_name"])}</div><div class="u">{short(r["url"])}</div></td>'
                   f'<td><span class="pill bad">{esc((r["error"] or "").split(":")[0][:18])}</span></td></tr>' for r in broken)
chg_html = "".join(f'<li><span class="pill {"bad" if r["severity"]=="breaking" else "ok"}">{r["severity"]}</span> <b>{esc(r["server_name"])}</b> {esc(r["kind"].replace("_"," "))} <code>{esc(r["tool"] or "")}</code></li>' for r in changes) or "<li>No tool changes yet.</li>"
ex = rel[0]["server_name"] if rel else "io.github.example/server"
ex_slug = build_servers.server_slug(ex)
ex_badge = html.escape(build_servers.badge_url(ex), quote=True)
_stamp = datetime.datetime.fromtimestamp(last, datetime.timezone.utc).strftime("%d%H%MZ")
metar = f"TW {_stamp} OPEN{round(100*by.get('ok',0)/max(total,1))} AUTH{round(100*by.get('auth_required',0)/max(total,1))} HS{med:.0f} QNH1013"
# ISOBAR v14. Light surfaces and --clay are the locked parchment hexes.
# --acc is clay darkened to #7A5432 so small links clear contrast on paper;
# display clay (wordmark, rules, mascot bead) stays #A67C52.
# Dark is the same family (warm brown, not cool gray); clay is lifted so it
# stays readable on night paper.
suite_css = """
:root{
  --paper:#F0EBE3;--plate:#D9CDB8;--ink:#6A5B4A;--clay:#A67C52;--mute:#7A7064;--wash:#E2D9CB;
  --ok:#7A8F66;--warn:#C4A35A;--danger:#B86B5A;--spend:#A89070;
  --bg:var(--paper);--card:#F7F3EC;--line:var(--plate);--row:var(--wash);
  --txt:var(--ink);--h:var(--ink);--mut:var(--mute);--acc:#7A5432;--leg:#8C7B68;
  --okb:#E4EBD8;--okt:#3E5230;--badb:#F3E3DC;--badt:#8C4538;
  --display:"Cormorant Garamond","EB Garamond",Georgia,serif;
  --sans:"IBM Plex Sans","Avenir Next",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,monospace;
  color-scheme:light
}
:root[data-theme=dark]{
  --paper:#1C1712;--plate:#4A3E32;--ink:#E8DFD2;--clay:#C4A07A;--mute:#B5A794;--wash:#3A3128;
  --ok:#A3B68A;--warn:#D4B56A;--danger:#D08B7A;--spend:#C4B09A;
  --bg:#1C1712;--card:#2A231B;--line:#4A3E32;--row:#342C23;
  --txt:#E8DFD2;--h:#F3EBDF;--mut:#B5A794;--acc:var(--clay);--leg:#6B5E50;
  --okb:#2A3324;--okt:#C5D4AE;--badb:#3A2822;--badt:#E7BDB2;
  color-scheme:dark
}
.tg{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:999px;padding:5px 12px;font:500 13px var(--sans);cursor:pointer;margin-left:12px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.55 var(--sans)}
a{color:var(--acc);text-underline-offset:3px}
:focus-visible{outline:2px solid var(--clay);outline-offset:2px}
.wrap{position:relative;z-index:0;max-width:1120px;margin:0 auto;padding:32px 24px 64px}
.isobars{position:absolute;top:0;left:0;width:100%;height:210px;pointer-events:none;z-index:0;color:#8C7B68;opacity:.34;-webkit-mask-image:linear-gradient(#000 42%,transparent);mask-image:linear-gradient(#000 42%,transparent)}
:root[data-theme=dark] .isobars{color:#E2D9CB;opacity:.08}
.wrap>*:not(.isobars){position:relative;z-index:1}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.logo{display:flex;align-items:center;gap:10px;font-family:var(--display);font-weight:500;font-size:30px;letter-spacing:.02em;line-height:1;color:var(--ink)}
.mark{width:56px;height:56px;display:block;flex:none}
.logo span{color:var(--clay)}
.live{color:var(--mut);font-family:var(--mono);font-size:12px;letter-spacing:.02em}
.live i{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--clay);margin-right:6px}
.live a{margin-left:12px}
.metar{font-family:var(--mono);color:var(--mute);font-size:12px;letter-spacing:.04em;margin:16px 0 0}
.chip{display:inline-block;margin-right:8px;border:1.5px solid var(--ink);border-radius:999px;padding:0 8px;font-family:var(--sans);font-size:11px;letter-spacing:.08em;color:var(--ink);vertical-align:1px}
.check{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0 4px}
.check label{font-size:14px}
.check input{flex:1;min-width:220px;background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:8px 12px;font:15px var(--sans)}
h1{font-family:var(--display);font-weight:500;color:var(--h);font-size:42px;letter-spacing:.015em;line-height:1.15;margin:32px 0 6px}
h1::before{content:"";display:block;width:36px;height:2px;background:var(--clay);margin:0 0 14px}
.sub{color:var(--mut);margin:0 0 28px;font-size:17px}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:14px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}
.stat b{display:block;font-family:var(--sans);font-weight:500;font-size:28px;letter-spacing:0;font-variant-numeric:tabular-nums;color:var(--ink)}
.stat span{color:var(--mut);font-size:13px}
.grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:18px;margin-top:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}
h2{font-family:var(--sans);font-weight:500;font-size:16px;letter-spacing:.01em;margin:0 0 14px;color:var(--ink)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--mut);font-weight:500;padding:6px 4px;border-bottom:1px solid var(--line)}
td{padding:8px 4px;border-bottom:1px solid var(--row);vertical-align:top}
.nm{font-weight:500}.u{color:var(--mut);font-size:12px}
.pill{font-family:var(--sans);font-size:12px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.ok{background:var(--okb);color:var(--okt)}.bad{background:var(--badb);color:var(--badt)}
.bar{display:flex;height:14px;border-radius:8px;overflow:hidden;margin:6px 0 14px;background:var(--row)}
ul{list-style:none;padding:0;margin:0}
.legend li{display:flex;align-items:center;gap:8px;padding:5px 0}
.legend b{margin-left:auto}.legend em{color:var(--mut);width:40px;text-align:right;font-style:normal}
.dot{width:10px;height:10px;border-radius:3px}
pre{background:#2A241C;border:1px solid #4A3F34;border-radius:12px;padding:14px;overflow:auto;margin:0;color:#E7DDD0;font:12.5px/1.55 var(--mono);white-space:pre-wrap;word-break:break-all}
:root[data-theme=dark] pre{background:#14110E;border-color:#3E342A}
.u{word-break:break-all}
.chg li{overflow-wrap:anywhere;padding:6px 0;border-bottom:1px solid var(--row);font-size:13.5px}
code{font-family:var(--mono);font-size:.92em;color:var(--ink);background:var(--wash);border-radius:4px;padding:1px 5px}
pre code{background:none;color:inherit;padding:0}
.badge{display:inline-flex;font:500 12px/1 var(--sans);border-radius:5px;overflow:hidden;margin:4px 8px 4px 0;color:#F0EBE3}
.badge span{padding:6px 8px}
.b1{background:#5C5348}.b2{background:#7A8F66}.b3{background:#C4A35A}.b4{background:#A67C52}
footer{color:var(--mut);font-size:12px;margin-top:28px}
.btn{background:#6E4C32;color:#F7F3EC;border:0;border-radius:10px;padding:8px 16px;font:500 14px var(--sans);cursor:pointer}
.btn:hover{background:#5E402A}
.btn:disabled{opacity:.65;cursor:progress}
:root[data-theme=dark] .btn{background:#A67C52;color:#1C1712}
:root[data-theme=dark] .btn:hover{background:#B88B60}
.hero{font-family:var(--display);color:var(--ink);font-size:28px;font-weight:500;letter-spacing:.015em;line-height:1.2;margin:8px 0 16px}
.products{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:0 0 18px}
.products .card{padding:16px 16px 18px;border-top:2px solid var(--clay)}
.products .card:nth-child(3){border-top-color:var(--spend)}
.products h2{font-family:var(--display);font-size:26px;font-weight:500;letter-spacing:.01em;margin:0 0 6px;color:var(--ink)}
.products p{margin:0 0 10px;color:var(--mut);font-size:13.5px}
.tools{display:flex;flex-wrap:wrap;gap:6px;list-style:none;padding:0;margin:0}
.tools code{color:var(--ink);background:var(--wash);border-radius:6px;padding:2px 6px;font-size:12px}
@media(max-width:980px){.stats{grid-template-columns:repeat(3,1fr)}}
@media(max-width:800px){.products{grid-template-columns:1fr}.hero{font-size:24px}h1{font-size:34px}.grid{grid-template-columns:1fr}}
@media(max-width:560px){.stats{grid-template-columns:1fr 1fr}.wrap{padding:24px 16px 48px}}
""" + INSTALL_CSS
suite_html = f"""<p class="hero">One connection gives you three tools.</p>
<div class="products">
<div class="card"><h2>MCP health</h2><p>Is this MCP server or tool safe to call right now? About 2,000 servers, probed every 15 minutes.</p><ul class="tools"><li><code>check_mcp_server_health</code></li><li><code>find_alternative_mcp_servers</code></li><li><code>watch_server</code></li><li><code>list_watches</code></li><li><code>unwatch_server</code></li><li><code>get_api_key</code></li></ul></div>
<div class="card"><h2>Upstream</h2><p>API status and model retirements, across about 40 providers.</p><ul class="tools"><li><code>check_api_status</code></li><li><code>check_model_deprecation</code></li><li><code>list_recent_api_changes</code></li><li><code>list_providers</code></li><li><code>get_digest</code></li></ul></div>
<div class="card"><h2>SpendGate</h2><p>approve, ask_human, or block before an agent pays.</p><ul class="tools"><li><code>score_spend</code></li><li><code>get_quota</code></li><li><code>spendgate_get_api_key</code></li></ul></div>
</div>
{install_section()}"""
page = f"""<!doctype html><html lang="en"><head><link rel="alternate" type="text/plain" title="llms.txt" href="/llms.txt"><link rel="service-desc" type="application/json" href="https://toolweather.toolweather.workers.dev/openapi.json"><link rel="mcp-server-card" type="application/json" href="/.well-known/mcp"><script>(function(){{var t=localStorage.getItem("tw-theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=t}})();
function twToggle(){{var d=document.documentElement,t=d.dataset.theme==="dark"?"light":"dark";d.dataset.theme=t;localStorage.setItem("tw-theme",t);document.getElementById("tg").textContent=t==="dark"?"Light mode":"Dark mode"}}</script><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Toolweather — MCP health, API status, spend checks, and merchant checks for agents</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
{suite_css}
</style></head><body><div class="wrap">
<svg class="isobars" viewBox="0 0 1200 280" preserveAspectRatio="none" aria-hidden="true"><path d="M-40 78 C 150 28, 280 148, 450 74 S 740 8, 940 92 S 1160 168, 1280 64" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><path d="M-40 142 C 170 92, 310 202, 490 132 S 800 52, 1000 148 S 1200 214, 1300 132" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/><path d="M-20 206 C 190 156, 350 266, 550 196 S 880 116, 1100 206 S 1280 266, 1340 196" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round"/></svg>
<header><div class="logo"><img class="mark" src="mark.svg" width="56" height="56" alt=""><div>Tool<span>weather</span></div></div><div class="live"><i></i>Last check {when}<a href="#install">Install</a><a href="/servers/">Leaderboard</a><button class="tg" id="tg" onclick="twToggle()">Dark mode</button></div></header>
<p class="metar"><span class="chip">BUNDLE</span>{metar}</p>
<h1>MCP health, API status, spend checks, and merchant checks for agents</h1>
<form class="check" id="srvcheck" method="get" action="https://toolweather.toolweather.workers.dev/v1/is_safe_to_call"><label for="srvq">Check a server</label><input id="srvq" name="server" placeholder="Registry name or MCP URL" autocomplete="off"><button class="btn" type="submit">Check</button></form>
<p class="metar" id="srvout" hidden></p>
{suite_html}
<div class="stats"><div class="stat"><b>{total:,}</b><span>servers monitored</span></div><div class="stat"><b>{pct(by.get('ok',0))}</b><span>healthy (open)</span></div>
<div class="stat"><b>{pct(by.get('auth_required',0))}</b><span>behind auth</span></div><div class="stat"><b>{med:.0f} ms</b><span>median handshake</span></div>
<div class="stat"><b>{tools_total:,}</b><span>tools tracked</span></div></div>
<div class="grid"><div class="card"><h2>Most reliable servers</h2><table><tr><th>Server</th><th>Uptime</th><th>Latency</th><th>Tools</th><th>Checks</th></tr>{rel_html}</table></div>
<div><div class="card"><h2>2026-07-28 spec readiness</h2><div class="bar">{bar}</div><ul class="legend">{legend}</ul><p style="margin:12px 0 0"><a href="/servers/">Full readiness leaderboard</a></p></div>
<div class="card" style="margin-top:18px"><h2>Recently broken</h2><table><tr><th>Server</th><th style="width:120px">Error</th></tr>{brk_html}</table></div></div></div>
<div class="card" id="accuracy" style="margin-bottom:18px"><h2>Track record</h2><p class="sub" style="margin:4px 0 10px">How often a verdict still held at the very next scheduled check of the same server (usually ~15 minutes later). This measures how stable our verdicts are between checks, not whether real tool calls succeed. History starts Oct 15, 2026, so samples are still small. <a style="color:var(--acc)" href="https://toolweather.toolweather.workers.dev/v1/accuracy">Raw data</a></p><div id="accout">Loading…</div></div>
<div class="card" id="key" style="margin-bottom:18px"><h2>Get a free API key</h2><p class="sub" style="margin:4px 0 10px">No signup. With no key you get 50 checks/day. <code>get_api_key</code> (or this button, or <code>POST /v1/keys</code>) raises that to 1,000 checks/day and 3 change-alert watches. The key is shown once.</p>
<button id="getkey" class="btn" type="button">Get a free key</button>
<pre id="keyout" style="display:none;margin-top:10px;white-space:pre-wrap;word-break:break-all"></pre></div>
<div class="card" id="pricing" style="margin-bottom:18px"><h2>Pricing</h2><p class="sub" style="margin:4px 0 10px">Zero signup. Agents start free (no key needed), then pay per call in USDC on Base via <a style="color:var(--acc)" href="https://www.x402.org">x402</a> — no account, no subscription. You're never charged for errors.</p><table><tr><th>What</th><th>Free</th><th>Beyond free</th></tr><tr><td>Health check (MCP tool or <code>/v1/is_safe_to_call</code>)</td><td>1,000/day per key, or 50/day anonymous (no key)</td><td>$0.001 per call</td></tr><tr><td>Live recheck (<code>fresh=true</code> or your own token)</td><td>200/day per key</td><td>$0.003 per call</td></tr><tr><td>Find alternatives</td><td>Within the free daily quota</td><td>$0.001 per call</td></tr><tr><td>Batch check, up to 25 servers</td><td>Each server counts as 1 call</td><td>$0.01 per batch</td></tr><tr><td>Change-alert watches</td><td>3 per key</td><td>$0.05 per extra watch per 30 days</td></tr><tr><td>Upstream tools (API status, model deprecations)</td><td colspan="2">Upstream's own free quota and prices</td></tr><tr><td>SpendGate tools (<code>score_spend</code>, <code>get_quota</code>, <code>spendgate_get_api_key</code>)</td><td colspan="2">SpendGate's own free quota and prices</td></tr></table><p class="sub" style="margin-top:8px">Every metered response carries <code>X-Free-Remaining</code> and <code>X-Price-After-Free</code> (and a <code>quota</code> body field) so agents can plan. Machine-readable: <a style="color:var(--acc)" href="https://toolweather.toolweather.workers.dev/v1/pricing">/v1/pricing</a> · <a style="color:var(--acc)" href="https://toolweather.toolweather.workers.dev/.well-known/x402">/.well-known/x402</a></p></div>
<div class="grid"><div class="card"><h2>For agents: ask before you call</h2><pre>curl -H "X-API-Key: $TW_KEY" \\
  "https://toolweather.toolweather.workers.dev/v1/is_safe_to_call?server={esc(ex)}"

{{ "verdict": "safe", "status": "ok", "latency_ms": 182,
  "spec_readiness": {{"readiness": "dual", "supports_2026_07_28": true}},
  "last_tool_change": null, "last_checked": "…" }}

# or add the MCP server and call the tools:
check_mcp_server_health(server="{esc(ex)}", tool="search")
check_api_status(provider="openai")</pre></div>
<div class="card"><h2>Tool changes</h2><ul class="chg">{chg_html}</ul>
<h2 style="margin-top:18px">Embeddable badge</h2>
<div><img alt="Toolweather badge" src="{ex_badge}"></div>
<pre style="margin-top:10px">[![Toolweather]({ex_badge})](https://toolweather.pages.dev/servers/{esc(ex_slug)}/)</pre>
<p class="u" style="margin:8px 0 0"><a href="/servers/{esc(ex_slug)}/">This server's status page</a> · <a href="/servers/">All servers</a></p></div></div>
<footer>Preview generated from local probe data. Probes only run initialize/server-discover and tools/list — tools are never called. Free tier: 1,000 checks/day per key (<a style="color:var(--acc)" href="#key">get one instantly</a>), plus change alerts (signed webhooks) for 3 watched servers; heavier use is pay-per-call (<a style="color:var(--acc)" href="#pricing">pricing</a>) — see <a style="color:var(--acc)" href="https://github.com/toolweather/toolweather#change-alerts-webhooks">docs</a>. <a style="color:var(--acc)" href="/servers/">Readiness leaderboard</a> · <a style="color:var(--acc)" href="https://github.com/toolweather/toolweather">GitHub</a> · <a style="color:var(--acc)" href="https://toolweather.toolweather.workers.dev/openapi.json">OpenAPI</a> · <a style="color:var(--acc)" href="/llms.txt">llms.txt</a> · <a style="color:var(--acc)" href="/.well-known/mcp">MCP server card</a> · MCP suite endpoint: https://toolweather.toolweather.workers.dev/mcp (MCP health + Upstream + SpendGate + PayCheck tools) · SpendGate: <a style="color:var(--acc)" href="https://spendgate.toolweather.workers.dev">spendgate.toolweather.workers.dev</a> · PayCheck: <a style="color:var(--acc)" href="https://paycheck.toolweather.workers.dev">paycheck.toolweather.workers.dev</a> · Upstream REST: <a style="color:var(--acc)" href="https://upstream.toolweather.workers.dev">upstream.toolweather.workers.dev</a> · New: the free daily <a style="color:var(--acc)" href="https://upstream-api.pages.dev/digest/">breaking-changes digest</a> (MCP + AI/dev APIs, <a style="color:var(--acc)" href="https://upstream.toolweather.workers.dev/digest/feed.xml">RSS</a>) · <a style="color:var(--acc)" href="/terms">Terms</a> · <a style="color:var(--acc)" href="/privacy">Privacy</a> · Contact: toolweather@mail.grokbot.com · Cookie-free, anonymous visit counting.</footer>
</div><script>document.getElementById("tg").textContent=document.documentElement.dataset.theme==="dark"?"Light mode":"Dark mode"</script><script>fetch("https://toolweather.toolweather.workers.dev/v1/accuracy").then(function(r){{return r.json()}}).then(function(d){{var L={{safe:"Safe (up)",caution_auth:"Caution (needs login)",unsafe:"Unsafe (down)"}},h="<table><tr><th>Day (UTC)</th>";Object.keys(L).forEach(function(k){{h+="<th>"+L[k]+"</th>"}});h+="</tr>";d.daily.forEach(function(x){{h+="<tr><td>"+x.day+"</td>";Object.keys(L).forEach(function(k){{var v=x[k];h+="<td>"+(v?(100*v.match_rate).toFixed(1)+"% <span class=u>of "+v.pairs.toLocaleString()+"</span>":"–")+"</td>"}});h+="</tr>"}});document.getElementById("accout").innerHTML=d.daily.length?h+"</table>":"No data yet."}}).catch(function(){{document.getElementById("accout").textContent="Track record unavailable right now."}})</script><script>document.getElementById("getkey").onclick=async function(){{var b=this,o=document.getElementById("keyout");b.disabled=true;b.textContent="Creating…";try{{var r=await fetch("https://toolweather.toolweather.workers.dev/v1/keys?source=web",{{method:"POST"}}),d=await r.json();o.style.display="block";o.textContent=r.ok?"Your key (shown once, copy it now):\\n"+d.api_key+"\\n\\nUse it as the X-API-Key header:\\n"+d.try:(d.message||"Something went wrong, please try again.");b.textContent=r.ok?"Key created":"Get a free key";if(!r.ok)b.disabled=false}}catch(e){{o.style.display="block";o.textContent="Network error, please try again.";b.disabled=false;b.textContent="Get a free key"}}}}</script><script>try{{fetch("https://toolweather.toolweather.workers.dev/v1/hit?p="+encodeURIComponent(location.pathname)+"&r="+encodeURIComponent(document.referrer),{{mode:"no-cors",keepalive:true}})}}catch(e){{}}</script><script>document.getElementById("srvcheck").addEventListener("submit",async function(ev){{ev.preventDefault();var q=document.getElementById("srvq").value.trim(),o=document.getElementById("srvout");if(!q)return;o.hidden=false;o.textContent="Checking…";try{{var r=await fetch("https://toolweather.toolweather.workers.dev/v1/is_safe_to_call?server="+encodeURIComponent(q)),d=await r.json();if(!r.ok){{o.textContent=d.message||d.error||"Check unavailable.";return}}var lat=d.latency_ms==null?"—":Math.round(d.latency_ms)+" ms";o.textContent=(d.verdict||"unknown")+" · "+(d.status||"")+" · "+lat}}catch(e){{o.textContent="Check unavailable."}}}});</script></body></html>"""
os.makedirs("site", exist_ok=True); open(OUT, "w").write(page); print("wrote", OUT, total, "servers")
build_servers.generate(c, site_dir="site")
