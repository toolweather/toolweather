"""Generate site/index.html (static Toolweather status page preview) from mcp_health.db."""
import html, json, sqlite3, statistics, datetime, os
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
rd_order = [("dual", "Dual-era (2026-07-28 + legacy)", "#3f8f8a"), ("modern_only", "2026-07-28 only", "#8fb8d8"),
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
page = f"""<!doctype html><html lang="en"><head><link rel="alternate" type="text/plain" title="llms.txt" href="/llms.txt"><link rel="service-desc" type="application/json" href="https://toolweather.toolweather.workers.dev/openapi.json"><link rel="mcp-server-card" type="application/json" href="/.well-known/mcp"><script>(function(){{var t=localStorage.getItem("tw-theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=t}})();
function twToggle(){{var d=document.documentElement,t=d.dataset.theme==="dark"?"light":"dark";d.dataset.theme=t;localStorage.setItem("tw-theme",t);document.getElementById("tg").textContent=t==="dark"?"Light mode":"Dark mode"}}</script><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Toolweather — is this MCP tool safe to call right now?</title><style>
:root{{--bg:#f7f6f2;--card:#ffffff;--line:#e3e1da;--row:#efede7;--txt:#2b2f36;--h:#1f2329;--mut:#6b7280;--acc:#3f8f8a;--leg:#d6d2c8;--okb:#e4f1ef;--okt:#2f6f6b;--badb:#f6e7e4;--badt:#a2463a;color-scheme:light}}
:root[data-theme=dark]{{--bg:#1c1f24;--card:#252a31;--line:#343a43;--row:#2e333b;--txt:#d9dde3;--h:#f1f3f5;--mut:#9aa3ae;--acc:#5fb3ab;--leg:#5a616b;--okb:#21403d;--okt:#8fd3cb;--badb:#47292a;--badt:#f0a59b;color-scheme:dark}}
.tg{{background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:999px;padding:5px 12px;font:13px inherit;cursor:pointer;margin-left:12px}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
.wrap{{max-width:1120px;margin:0 auto;padding:32px 24px 64px}}header{{display:flex;justify-content:space-between;align-items:center}}
.logo{{display:flex;align-items:center;gap:10px;font-weight:700;font-size:21px;letter-spacing:-.02em}}.logo span{{color:var(--acc)}}.live{{color:var(--mut);font-size:13px}}.live i{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--acc);margin-right:6px}}
h1{{color:var(--h);font-size:38px;letter-spacing:-.03em;margin:36px 0 6px}}.sub{{color:var(--mut);margin:0 0 28px;font-size:17px}}
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px}}.stat{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.03)}}
.stat b{{display:block;font-size:28px;letter-spacing:-.02em}}.stat span{{color:var(--mut);font-size:13px}}
.grid{{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:18px;margin-top:18px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}}
h2{{font-size:16px;margin:0 0 14px}}table{{width:100%;border-collapse:collapse;font-size:13.5px}}th{{text-align:left;color:var(--mut);font-weight:500;padding:6px 4px;border-bottom:1px solid var(--line)}}
td{{padding:8px 4px;border-bottom:1px solid var(--row);vertical-align:top}}.nm{{font-weight:600}}.u{{color:var(--mut);font-size:12px}}
.pill{{font-size:12px;padding:2px 8px;border-radius:999px;white-space:nowrap}}.ok{{background:var(--okb);color:var(--okt)}}.bad{{background:var(--badb);color:var(--badt)}}
.bar{{display:flex;height:14px;border-radius:8px;overflow:hidden;margin:6px 0 14px}}ul{{list-style:none;padding:0;margin:0}}
.legend li{{display:flex;align-items:center;gap:8px;padding:5px 0}}.bar{{background:var(--row)}}.legend b{{margin-left:auto}}.legend em{{color:var(--mut);width:40px;text-align:right;font-style:normal}}.dot{{width:10px;height:10px;border-radius:3px}}
pre{{background:#2b2f36;border:1px solid var(--line);border-radius:12px;padding:14px;overflow:auto;font-size:12.5px;color:#e7e5df;margin:0}}
pre{{white-space:pre-wrap;word-break:break-all}}.u{{word-break:break-all}}.chg li{{overflow-wrap:anywhere;padding:6px 0;border-bottom:1px solid var(--row);font-size:13.5px}}code{{color:#3f8f8a}}
.badge{{display:inline-flex;font:600 12px/1 Verdana,sans-serif;border-radius:5px;overflow:hidden;margin:4px 8px 4px 0}}.badge span{{padding:6px 8px}}
.badge{{color:#fff}}.b1{{background:#4b5563}}.b2{{background:#3f8f8a}}.b3{{background:#b7791f}}.b4{{background:#5b84b1}}footer{{color:var(--mut);font-size:12px;margin-top:28px}}
</style></head><body><div class="wrap">
<header><div class="logo"><svg width="34" height="34" viewBox="0 0 40 40" aria-hidden="true"><circle cx="15" cy="15" r="7" fill="#f2c46d"/><g stroke="#f2c46d" stroke-width="2.2" stroke-linecap="round"><path d="M15 3v2.5M3 15h2.5M6.5 6.5l1.8 1.8M23.5 6.5l-1.8 1.8"/></g><path d="M13 31a6 6 0 0 1 1-11.9A8 8 0 0 1 29.3 21 5 5 0 0 1 29 31z" fill="var(--card)" stroke="#3f8f8a" stroke-width="2.2" stroke-linejoin="round"/></svg><div>Tool<span>weather</span></div></div><div class="live"><i></i>Last check {when}<button class="tg" id="tg" onclick="twToggle()">Dark mode</button></div></header>
<h1>Is this MCP tool safe to call right now?</h1><p class="sub">Live health, breaking-change and spec-readiness checks for remote MCP servers in the official registry. Read-only probes every 15 minutes.</p>
<div class="stats"><div class="stat"><b>{total:,}</b><span>servers monitored</span></div><div class="stat"><b>{pct(by.get('ok',0))}</b><span>healthy (open)</span></div>
<div class="stat"><b>{pct(by.get('auth_required',0))}</b><span>behind auth</span></div><div class="stat"><b>{med:.0f} ms</b><span>median handshake</span></div>
<div class="stat"><b>{tools_total:,}</b><span>tools tracked</span></div></div>
<div class="grid"><div class="card"><h2>Most reliable servers</h2><table><tr><th>Server</th><th>Uptime</th><th>Latency</th><th>Tools</th><th>Checks</th></tr>{rel_html}</table></div>
<div><div class="card"><h2>2026-07-28 spec readiness</h2><div class="bar">{bar}</div><ul class="legend">{legend}</ul></div>
<div class="card" style="margin-top:18px"><h2>Recently broken</h2><table><tr><th>Server</th><th style="width:120px">Error</th></tr>{brk_html}</table></div></div></div>
<div class="card" id="accuracy" style="margin-bottom:18px"><h2>Track record</h2><p class="sub" style="margin:4px 0 10px">How often a verdict still held at the very next scheduled check of the same server (usually ~15 minutes later). This measures how stable our verdicts are between checks, not whether real tool calls succeed. History starts Oct 15, 2026, so samples are still small. <a style="color:var(--acc)" href="https://toolweather.toolweather.workers.dev/v1/accuracy">Raw data</a></p><div id="accout">Loading…</div></div>
<div class="card" id="key" style="margin-bottom:18px"><h2>Get a free API key</h2><p class="sub" style="margin:4px 0 10px">1,000 checks/day and 3 change-alert watches. Instant, no email or signup. Agents can also call <code>POST /v1/keys</code> or the <code>get_api_key</code> MCP tool.</p>
<button id="getkey" style="padding:8px 16px;border-radius:8px;border:0;background:var(--acc);color:#fff;font-weight:600;cursor:pointer">Get a free key</button>
<pre id="keyout" style="display:none;margin-top:10px;white-space:pre-wrap;word-break:break-all"></pre></div>
<div class="grid"><div class="card"><h2>For agents: ask before you call</h2><pre>curl -H "X-API-Key: $TW_KEY" \\
  "https://toolweather.toolweather.workers.dev/v1/is_safe_to_call?server={esc(ex)}"

{{ "verdict": "safe", "status": "ok", "latency_ms": 182,
  "spec_readiness": {{"readiness": "dual", "supports_2026_07_28": true}},
  "last_tool_change": null, "last_checked": "…" }}

# or add the MCP server and call the tool:
is_safe_to_call(server="{esc(ex)}", tool="search")</pre></div>
<div class="card"><h2>Tool changes</h2><ul class="chg">{chg_html}</ul>
<h2 style="margin-top:18px">Embeddable badge</h2>
<div><img alt="Toolweather badge" src="https://toolweather.toolweather.workers.dev/badge/{esc(ex)}.svg"></div>
<pre style="margin-top:10px">[![Toolweather](https://toolweather.toolweather.workers.dev/badge/{esc(ex)}.svg)](https://toolweather.pages.dev/)</pre></div></div>
<footer>Preview generated from local probe data. Probes only run initialize/server-discover and tools/list — tools are never called. Free tier: 1,000 checks/day per key (<a style="color:var(--acc)" href="#key">get one instantly</a>), plus change alerts (signed webhooks) for 3 watched servers — see <a style="color:var(--acc)" href="https://github.com/toolweather/toolweather#change-alerts-webhooks">docs</a>. <a style="color:var(--acc)" href="https://github.com/toolweather/toolweather">GitHub</a> · <a style="color:var(--acc)" href="https://toolweather.toolweather.workers.dev/openapi.json">OpenAPI</a> · <a style="color:var(--acc)" href="/llms.txt">llms.txt</a> · <a style="color:var(--acc)" href="/.well-known/mcp">MCP server card</a> · MCP endpoint: https://toolweather.toolweather.workers.dev/mcp · New: the free daily <a style="color:var(--acc)" href="https://upstream-api.pages.dev/digest/">breaking-changes digest</a> (MCP + AI/dev APIs, <a style="color:var(--acc)" href="https://upstream.toolweather.workers.dev/digest/feed.xml">RSS</a>) · Contact: toolweather@mail.grokbot.com · Cookie-free, anonymous visit counting.</footer>
</div><script>document.getElementById("tg").textContent=document.documentElement.dataset.theme==="dark"?"Light mode":"Dark mode"</script><script>fetch("https://toolweather.toolweather.workers.dev/v1/accuracy").then(function(r){{return r.json()}}).then(function(d){{var L={{safe:"Safe (up)",caution_auth:"Caution (needs login)",unsafe:"Unsafe (down)"}},h="<table><tr><th>Day (UTC)</th>";Object.keys(L).forEach(function(k){{h+="<th>"+L[k]+"</th>"}});h+="</tr>";d.daily.forEach(function(x){{h+="<tr><td>"+x.day+"</td>";Object.keys(L).forEach(function(k){{var v=x[k];h+="<td>"+(v?(100*v.match_rate).toFixed(1)+"% <span class=u>of "+v.pairs.toLocaleString()+"</span>":"–")+"</td>"}});h+="</tr>"}});document.getElementById("accout").innerHTML=d.daily.length?h+"</table>":"No data yet."}}).catch(function(){{document.getElementById("accout").textContent="Track record unavailable right now."}})</script><script>document.getElementById("getkey").onclick=async function(){{var b=this,o=document.getElementById("keyout");b.disabled=true;b.textContent="Creating…";try{{var r=await fetch("https://toolweather.toolweather.workers.dev/v1/keys?source=web",{{method:"POST"}}),d=await r.json();o.style.display="block";o.textContent=r.ok?"Your key (shown once, copy it now):\n"+d.api_key+"\n\nUse it as the X-API-Key header:\n"+d.try:(d.message||"Something went wrong, please try again.");b.textContent=r.ok?"Key created":"Get a free key";if(!r.ok)b.disabled=false}}catch(e){{o.style.display="block";o.textContent="Network error, please try again.";b.disabled=false;b.textContent="Get a free key"}}}}</script><script>try{{fetch("https://toolweather.toolweather.workers.dev/v1/hit?p="+encodeURIComponent(location.pathname)+"&r="+encodeURIComponent(document.referrer),{{mode:"no-cors",keepalive:true}})}}catch(e){{}}</script></body></html>"""
os.makedirs("site", exist_ok=True); open(OUT, "w").write(page); print("wrote", OUT, total, "servers")
