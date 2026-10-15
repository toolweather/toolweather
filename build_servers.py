"""Publish the MCP readiness leaderboard and one indexable page per server.

Reads the same mcp_health.db shape as build_site.py (latest probe per endpoint,
plus tool-list changes) and writes:

  site/servers/index.html
  site/servers/<slug>/index.html
  site/servers/style.css
  site/sitemap.xml
  site/robots.txt

Called from build_site.py so the probe.yml Pages deploy picks the files up
with the homepage. Slugs are filesystem-safe forms of registry names
(`io.github.foo/bar` -> `io.github.foo--bar`). There is no separate server
allowlist: every endpoint in the probe DB gets a page, including the four
`io.github.toolweather/*` remotes mcphealth.py pins into the probe set.

Cloudflare Pages allows about 20,000 files per deployment. PAGE_CAP stays
under that, after the rest of site/. Every server is still listed on the
leaderboard if the cap binds; the page says so. At the current probe size
(~2,000 servers) the cap does not drop anyone.
"""
import datetime, hashlib, html, os, re, shutil, sys
from urllib.parse import quote
import tw_core as tw
from site_install import INSTALL_CSS, install_section

ORIGIN = "https://toolweather.pages.dev"
BADGE_ORIGIN = "https://toolweather.toolweather.workers.dev"
# Leave room for index, legal pages, icons, this stylesheet, and sitemap.
PAGE_CAP = 18000
RESERVED_SLUGS = {"style.css"}
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_STATUS_ORDER = {"ok": 0, "auth_required": 1, "failed": 2}

ROBOTS_TXT = """# Public status pages. Per-server HTML lives under /servers/.
User-agent: *
Allow: /servers/
Allow: /

Sitemap: https://toolweather.pages.dev/sitemap.xml
"""

STYLE = """
:root{
  --paper:#F0EBE3;--plate:#D9CDB8;--ink:#6A5B4A;--clay:#A67C52;--mute:#7A7064;--wash:#E2D9CB;
  --bg:var(--paper);--card:#F7F3EC;--line:var(--plate);--row:var(--wash);
  --txt:var(--ink);--h:var(--ink);--mut:var(--mute);--acc:#7A5432;
  --okb:#E4EBD8;--okt:#3E5230;--badb:#F3E3DC;--badt:#8C4538;
  --authb:#F3EBD4;--autht:#6E5420;
  --display:"Cormorant Garamond","EB Garamond",Georgia,serif;
  --sans:"IBM Plex Sans","Avenir Next",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,monospace;
  color-scheme:light
}
:root[data-theme=dark]{
  --paper:#1C1712;--plate:#4A3E32;--ink:#E8DFD2;--clay:#C4A07A;--mute:#B5A794;--wash:#3A3128;
  --bg:#1C1712;--card:#2A231B;--line:#4A3E32;--row:#342C23;
  --txt:#E8DFD2;--h:#F3EBDF;--mut:#B5A794;--acc:var(--clay);
  --okb:#2A3324;--okt:#C5D4AE;--badb:#3A2822;--badt:#E7BDB2;
  --authb:#3A3220;--autht:#E4D0A0;
  color-scheme:dark
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.55 var(--sans)}
a{color:var(--acc);text-underline-offset:3px}
:focus-visible{outline:2px solid var(--clay);outline-offset:2px}
.isobars{position:absolute;left:0;right:0;top:0;width:100%;height:180px;pointer-events:none;color:#8C7B68;opacity:.34;-webkit-mask-image:linear-gradient(#000 40%,transparent);mask-image:linear-gradient(#000 40%,transparent)}
:root[data-theme=dark] .isobars{color:#E2D9CB;opacity:.08}
main{position:relative;max-width:1120px;margin:0 auto;padding:32px 24px 64px}
main.narrow{max-width:800px}
.back{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:0 0 8px}
.brand{display:inline-flex;align-items:center;gap:8px;font-family:var(--display);font-size:26px;font-weight:500;color:var(--ink);text-decoration:none}
.brand span{color:var(--clay)}
.brand img{width:40px;height:40px;display:block}
.metar{font-family:var(--mono);color:var(--mut);font-size:12px;letter-spacing:.04em;margin:0 0 8px}
.chip{display:inline-block;margin-right:8px;border:1.5px solid var(--ink);border-radius:999px;padding:0 8px;font-family:var(--sans);font-size:11px;letter-spacing:.08em;color:var(--ink);vertical-align:1px}
.tg{margin-left:auto;background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:999px;padding:5px 12px;font:500 13px var(--sans);cursor:pointer}
h1{font-family:var(--display);font-weight:500;color:var(--h);font-size:40px;letter-spacing:.015em;line-height:1.15;margin:22px 0 8px;overflow-wrap:anywhere}
h1::before{content:"";display:block;width:36px;height:2px;background:var(--clay);margin:0 0 12px}
h2{font-family:var(--sans);font-weight:500;font-size:16px;margin:28px 0 10px;color:var(--ink)}
.sub,.note{color:var(--mut);margin:0 0 18px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
.filters a{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 12px;text-decoration:none}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:0 0 8px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.stat b{display:block;font-weight:500;font-size:26px;font-variant-numeric:tabular-nums;color:var(--ink)}
.stat span{color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin:0 0 14px}
.tablewrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--mut);font-weight:500;padding:6px 8px;border-bottom:1px solid var(--line)}
td{padding:8px;border-bottom:1px solid var(--row);vertical-align:top}
.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.u{color:var(--mut);font-size:12px;word-break:break-all}
.pill{font-size:12px;padding:2px 8px;border-radius:999px;white-space:nowrap}
.ok{background:var(--okb);color:var(--okt)}
.auth{background:var(--authb);color:var(--autht)}
.bad{background:var(--badb);color:var(--badt)}
code{font-family:var(--mono);font-size:.92em;background:var(--wash);border-radius:4px;padding:1px 5px}
dl{display:grid;grid-template-columns:11rem 1fr;gap:6px 12px;margin:0}
dt{color:var(--mut)}
dd{margin:0;overflow-wrap:anywhere}
.badgeimg{display:block;margin:8px 0;height:20px}
pre{background:#2A241C;border:1px solid #4A3F34;border-radius:12px;padding:14px;overflow:auto;margin:8px 0 0;color:#E7DDD0;font:12.5px/1.55 var(--mono);white-space:pre-wrap;word-break:break-all}
:root[data-theme=dark] pre{background:#14110E;border-color:#3E342A}
.copy{margin-top:8px;background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:4px 10px;font:500 13px var(--sans);cursor:pointer}
.banner{background:var(--authb);color:var(--autht);border-radius:12px;padding:12px 14px;margin:0 0 16px}
footer{color:var(--mut);font-size:12px;margin-top:28px}
@media(max-width:800px){.stats{grid-template-columns:1fr 1fr}h1{font-size:32px}dl{grid-template-columns:1fr}main{padding:24px 16px 48px}}
""" + INSTALL_CSS

THEME = """<script>(function(){var t=localStorage.getItem("tw-theme")||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=t})();
function twToggle(){var d=document.documentElement,t=d.dataset.theme==="dark"?"light":"dark";d.dataset.theme=t;localStorage.setItem("tw-theme",t);var b=document.getElementById("tg");if(b)b.textContent=t==="dark"?"Light mode":"Dark mode"}</script>"""

ISOBARS = """<svg class="isobars" viewBox="0 0 1200 200" preserveAspectRatio="none" aria-hidden="true"><path d="M-40 70 C 160 24, 300 140, 480 68 S 800 12, 1020 86 S 1240 150, 1320 60" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M-40 120 C 180 74, 340 180, 540 112 S 860 48, 1100 124 S 1280 170, 1340 110" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round"/></svg>"""

FONTS = """<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@500;600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">"""

LATEST = """SELECT e.id eid, e.server_name, e.url, e.transport, p.* FROM endpoints e
  JOIN probes p ON p.id=(SELECT max(id) FROM probes WHERE endpoint_id=e.id)"""


def server_slug(name):
    """Filesystem-safe slug. Slashes in registry names become `--`."""
    raw = (name or "").strip()
    slug = _UNSAFE.sub("-", raw.replace("/", "--")).strip(".-")
    if slug in {"", ".", ".."}:
        slug = "server"
    if len(slug.encode("utf-8")) > 120:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        trimmed = slug.encode("utf-8")[:100].decode("utf-8", "ignore").rstrip(".-")
        slug = (trimmed + "-" + digest).strip(".-") or ("server-" + digest)
    return slug


def assign_slugs(names):
    """One unique slug per registry name. The alphabetically first name keeps the plain slug."""
    used = {}
    out = {}
    for name in sorted(names, key=lambda n: (n or "").lower()):
        base = server_slug(name)
        slug = base
        if slug in used or slug in RESERVED_SLUGS:
            slug = base + "-" + hashlib.sha256((name or "").encode("utf-8")).hexdigest()[:8]
        n = 2
        orig = slug
        while slug in used or slug in RESERVED_SLUGS:
            slug = f"{orig}-{n}"
            n += 1
        used[slug] = name
        out[name] = slug
    return out


def badge_url(name):
    """Same /badge/<registry-name>.svg route the Worker already serves. Slashes stay."""
    return f"{BADGE_ORIGIN}/badge/{quote(name or '', safe='/')}.svg"


def page_url(slug):
    return f"{ORIGIN}/servers/{slug}/"


def badge_markdown(name, slug):
    return f"[![Toolweather]({badge_url(name)})]({page_url(slug)})"


def _health_key(status, latency, tie):
    lat = latency if isinstance(latency, (int, float)) else 1e18
    return (_STATUS_ORDER.get(status, 9), lat, tie or "")


def _section(status, readiness):
    if status != "ok":
        return "down"
    if readiness in ("dual", "modern_only"):
        return "ready"
    return "legacy"


def _fmt_checked(ts):
    if not ts:
        return "—", ""
    dt = datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC"), dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _metar_stamp(ts):
    if not ts:
        return "------Z"
    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%d%H%MZ")


def fleet_metar(servers):
    """Quiet METAR strip: TW DDHHMMZ OPEN## AUTH## HS### QNH1013."""
    if not servers:
        return "TW ------Z OPEN0 AUTH0 HS0 QNH1013"
    newest = max((s["ts"] or 0) for s in servers)
    n = len(servers) or 1
    op = round(100 * sum(1 for s in servers if s["status"] == "ok") / n)
    au = round(100 * sum(1 for s in servers if s["status"] == "auth_required") / n)
    lats = sorted(s["latency_ms"] for s in servers if s["status"] == "ok" and isinstance(s["latency_ms"], (int, float)))
    hs = round(lats[len(lats) // 2]) if lats else 0
    return f"TW {_metar_stamp(newest)} OPEN{op} AUTH{au} HS{hs} QNH1013"


def server_metar(s):
    hs = round(s["latency_ms"]) if isinstance(s["latency_ms"], (int, float)) else 0
    flag = {"ok": "OPEN", "auth_required": "AUTH", "failed": "DOWN"}.get(s["status"], "UNK")
    rd = (s["readiness"] or "unknown").upper()
    return f"TW {_metar_stamp(s['ts'])} {flag} HS{hs} {rd} QNH1013"


def _fmt_lat(ms):
    if not isinstance(ms, (int, float)):
        return "—"
    return f"{ms:.0f} ms"


def _short_url(url):
    return (url or "").replace("https://", "").replace("http://", "")[:64]


def _change_line(rep):
    lc = (rep or {}).get("last_tool_change")
    if not lc:
        return "None recorded"
    bits = []
    for ch in (lc.get("changes") or [])[:4]:
        kind = (ch.get("kind") or "").replace("_", " ")
        tool = ch.get("tool") or ""
        sev = ch.get("severity") or ""
        bits.append(" ".join(x for x in (sev, kind, tool) if x))
    extra = len(lc.get("changes") or []) - len(bits)
    summary = "; ".join(bits) if bits else ("breaking" if lc.get("breaking") else "non-breaking")
    if extra > 0:
        summary += f"; +{extra} more"
    flag = "breaking" if lc.get("breaking") else "non-breaking"
    when = lc.get("at") or ""
    return f"{when} ({flag}) — {summary}".strip(" —")


def _verdict_line(rep):
    if not rep:
        return "Verdict unavailable for this probe."
    reasons = rep.get("reasons") or []
    why = reasons[0] if reasons else ""
    return f"Verdict: {rep.get('verdict') or 'unknown'}. {why}".strip()


def _status_word(status):
    return {"ok": "up", "auth_required": "needs auth", "failed": "down"}.get(status, status or "unknown")


def _meta_description(name, status, readiness):
    word = _status_word(status)
    rd = readiness or "unknown"
    tail = f" MCP status: {word}. 2026-07-28 readiness: {rd}. Probed by Toolweather."
    text = f"Is {name} down?" + tail
    if len(text) <= 160:
        return text
    room = 160 - len("Is  down?") - len(tail)
    if room < 8:
        return f"Is {name} down? MCP status: {word}."[:160]
    short = name[: room - 1].rstrip() + "…"
    return f"Is {short} down?" + tail


def _pill(status):
    if status == "ok":
        return "ok"
    if status == "auth_required":
        return "auth"
    return "bad"


def load_servers(conn):
    groups = {}
    for r in conn.execute(LATEST):
        name = (r["server_name"] or "").strip() or (r["url"] or f"endpoint-{r['eid']}")
        groups.setdefault(name, []).append(r)
    servers = []
    for name, eps in groups.items():
        eps.sort(key=lambda r: _health_key(r["status"], r["latency_ms"], r["url"]))
        primary = eps[0]
        rep = tw.endpoint_report(conn, primary)
        servers.append({
            "name": name,
            "url": primary["url"] or "",
            "status": primary["status"] or "unknown",
            "latency_ms": primary["latency_ms"],
            "readiness": primary["spec_readiness"] or None,
            "tool_count": primary["tool_count"],
            "ts": primary["ts"],
            "endpoints": [(e["url"] or "", e["status"] or "") for e in eps],
            "rep": rep,
        })
    servers.sort(key=lambda s: _health_key(s["status"], s["latency_ms"], s["name"].lower()))
    for i, s in enumerate(servers, 1):
        s["rank"] = i
        s["section"] = _section(s["status"], s["readiness"])
    return servers


def _esc(s):
    return html.escape(s if s is not None else "", quote=True)


def _shell(title, description, canonical, css_href, body, narrow=False):
    esc_title = _esc(title)
    desc = _esc(description)
    canon = _esc(canonical)
    cls = "narrow" if narrow else ""
    og_image = f"{ORIGIN}/logo-400.png"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
{THEME}<title>{esc_title}</title>
<meta name="description" content="{desc}">
<meta name="robots" content="index,follow">
<link rel="canonical" href="{canon}">
<meta property="og:title" content="{esc_title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{canon}">
<meta property="og:type" content="website">
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{esc_title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{og_image}">
{FONTS}<link rel="stylesheet" href="{_esc(css_href)}">
</head><body>{ISOBARS}<main class="{cls}">
{body}
<script>var b=document.getElementById("tg");if(b)b.textContent=document.documentElement.dataset.theme==="dark"?"Light mode":"Dark mode"</script>
<script>try{{fetch("{BADGE_ORIGIN}/v1/hit?p="+encodeURIComponent(location.pathname)+"&r="+encodeURIComponent(document.referrer),{{mode:"no-cors",keepalive:true}})}}catch(e){{}}</script>
</main></body></html>"""


def _footer():
    return ('<footer><a href="/">Toolweather</a> · <a href="/servers/">Leaderboard</a> · '
            '<a href="https://github.com/toolweather/toolweather/blob/main/badge_readme.md">Badge docs</a> · '
            '<a href="/terms">Terms</a> · <a href="/privacy">Privacy</a></footer>')


def _row(s, slug, linked):
    checked, iso = _fmt_checked(s["ts"])
    name = s["name"]
    if linked:
        label = f'<a href="/servers/{_esc(slug)}/">{_esc(name)}</a>'
    else:
        label = f'<span>{_esc(name)}</span>'
    tools = "—" if s["tool_count"] is None else str(int(s["tool_count"]))
    rd = s["readiness"] or "unknown"
    time = f'<time datetime="{iso}">{_esc(checked)}</time>' if iso else _esc(checked)
    return (f'<tr><td class="num">{s["rank"]}</td><td>{label}<div class="u">{_esc(_short_url(s["url"]))}</div></td>'
            f'<td><span class="pill {_pill(s["status"])}">{_esc(s["status"])}</span></td>'
            f'<td class="num">{_esc(_fmt_lat(s["latency_ms"]))}</td><td><code>{_esc(rd)}</code></td>'
            f'<td class="num">{_esc(tools)}</td><td class="num">{time}</td></tr>')


def _leaderboard(servers, slugs, paged, capped_note, metar):
    groups = {"ready": [], "legacy": [], "down": []}
    for s in servers:
        groups[s["section"]].append(s)
    sections = [
        ("ready", "Ready (2026-07-28)",
         "Open servers whose latest probe reported spec readiness dual or modern_only."),
        ("legacy", "Legacy-only",
         "Open servers that are not on the 2026-07-28 spec (legacy_only, or readiness not recorded)."),
        ("down", "Down / auth",
         "Latest probe failed, or the server asked for credentials."),
    ]
    blocks = []
    for key, title, blurb in sections:
        rows = groups[key]
        body = "".join(_row(s, slugs[s["name"]], slugs[s["name"]] in paged) for s in rows) or "<tr><td colspan='7'>None in this probe.</td></tr>"
        blocks.append(
            f'<section id="{key}"><h2>{_esc(title)} <span class="u">{len(rows)}</span></h2>'
            f'<p class="note">{_esc(blurb)}</p><div class="tablewrap"><table><thead><tr>'
            f'<th>Rank</th><th>Name</th><th>Status</th><th>Latency</th><th>Spec readiness</th><th>Tools</th><th>Last checked</th>'
            f'</tr></thead><tbody>{body}</tbody></table></div></section>')
    n_ready, n_legacy, n_down = len(groups["ready"]), len(groups["legacy"]), len(groups["down"])
    banner = f'<p class="banner">{_esc(capped_note)}</p>' if capped_note else ""
    nav = (f'<nav class="filters" aria-label="Sections">'
           f'<a href="#ready">Ready · {n_ready}</a>'
           f'<a href="#legacy">Legacy-only · {n_legacy}</a>'
           f'<a href="#down">Down / auth · {n_down}</a></nav>')
    body = f"""<p class="back"><a class="brand" href="/"><img src="../mark.svg" width="40" height="40" alt="">Tool<span>weather</span></a><button class="tg" id="tg" type="button" onclick="twToggle()">Dark mode</button></p>
<p class="metar"><span class="chip">HEALTH</span>{_esc(metar)}</p>
<h1>MCP readiness leaderboard</h1>
<p class="sub">Ranked by health (ok first), then latency. Spec readiness is dual, modern_only, or legacy_only from the latest probe. <a href="/">Status page</a></p>
{install_section()}
{banner}
<div class="stats"><div class="stat"><b>{len(servers)}</b><span>servers</span></div><div class="stat"><b>{n_ready}</b><span>ready (2026-07-28)</span></div>
<div class="stat"><b>{n_legacy}</b><span>legacy-only</span></div><div class="stat"><b>{n_down}</b><span>down / auth</span></div></div>
{nav}
{''.join(blocks)}
{_footer()}"""
    desc = "Ranked MCP servers by health and latency, with 2026-07-28 spec readiness (dual, modern_only, legacy_only), tool counts, and last probe time."
    return _shell("MCP readiness leaderboard · Toolweather", desc, f"{ORIGIN}/servers/", "style.css", body)


def _server_page(s, slug):
    name = s["name"]
    title = f"{name} MCP status & 2026-07-28 readiness"
    checked, iso = _fmt_checked(s["ts"])
    rd = s["readiness"] or "unknown"
    tools = "—" if s["tool_count"] is None else str(int(s["tool_count"]))
    md = badge_markdown(name, slug)
    burl = badge_url(name)
    purl = page_url(slug)
    time = f'<time datetime="{iso}">{_esc(checked)}</time>' if iso else _esc(checked)
    others = s["endpoints"][1:8]
    extra = ""
    if others:
        items = "".join(f'<li><code>{_esc(u)}</code> · {_esc(st or "unknown")}</li>' for u, st in others)
        more = len(s["endpoints"]) - 1 - len(others)
        if more:
            items += f"<li>+{more} more</li>"
        extra = f"<h2>Other endpoints</h2><p class=\"note\">Figures above are the healthiest, then the fastest. This name has {len(s['endpoints'])} endpoints.</p><ul>{items}</ul>"
    word = _status_word(s["status"])
    body = f"""<p class="back"><a class="brand" href="/"><img src="../../mark.svg" width="40" height="40" alt="">Tool<span>weather</span></a><a href="/servers/">Leaderboard</a><button class="tg" id="tg" type="button" onclick="twToggle()">Dark mode</button></p>
<p class="metar"><span class="chip">HEALTH</span>{_esc(server_metar(s))}</p>
<h1>{_esc(title)}</h1>
<p class="lede">Is {_esc(name)} down? Latest probe: <b>{_esc(word)}</b>.</p>
<p class="card verdict">{_esc(_verdict_line(s["rep"]))}</p>
<dl>
<dt>Status</dt><dd><span class="pill {_pill(s["status"])}">{_esc(s["status"])}</span></dd>
<dt>Latency</dt><dd>{_esc(_fmt_lat(s["latency_ms"]))}</dd>
<dt>Spec readiness</dt><dd><code>{_esc(rd)}</code></dd>
<dt>Tools</dt><dd>{_esc(tools)}</dd>
<dt>Last tool change</dt><dd>{_esc(_change_line(s["rep"]))}</dd>
<dt>Last checked</dt><dd>{time}</dd>
<dt>Endpoint</dt><dd><code>{_esc(s["url"])}</code></dd>
</dl>
{extra}
<h2>Status badge</h2>
<p class="note">Free, no signup. The image updates from Toolweather probes. The link opens this page.</p>
<img class="badgeimg" alt="{_esc('Toolweather status for ' + name)}" src="{_esc(burl)}" height="20">
<p><a href="{_esc(burl)}">Live badge SVG</a></p>
<h2>Copy-paste README snippet</h2>
<pre id="snip">{_esc(md)}</pre>
<button class="copy" type="button" onclick="twCopy()">Copy</button>
<p class="note">Registry name <code>{_esc(name)}</code> · page <a href="{_esc(purl)}">{_esc(purl)}</a></p>
<p class="note"><a href="/#install">Copy this MCP config</a> for the Toolweather suite (free, no key), then call <code>check_mcp_server_health</code> with this server's name.</p>
{_footer()}
<script>function twCopy(){{var b=document.querySelector(".copy"),el=document.getElementById("snip"),text=el.textContent.replace(/^\\n+|\\n+$/g,"");var ta=document.createElement("textarea");ta.value=text;ta.setAttribute("readonly","");ta.style.position="fixed";ta.style.left="-9999px";document.body.appendChild(ta);ta.select();var ok=false;try{{ok=document.execCommand("copy")}}catch(e){{}}ta.remove();if(navigator.clipboard&&window.isSecureContext)navigator.clipboard.writeText(text).catch(function(){{}});b.textContent=ok?"Copied":"Copy failed";setTimeout(function(){{b.textContent="Copy"}},2500)}}</script>"""
    return _shell(title, _meta_description(name, s["status"], s["readiness"]), purl, "../style.css", body, narrow=True)


def _sitemap(urls):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in urls:
        extra = f"<lastmod>{_esc(lastmod)}</lastmod>" if lastmod else ""
        lines.append(f"<url><loc>{_esc(loc)}</loc>{extra}</url>")
    lines.append("</urlset>\n")
    return "".join(lines)


def _mkdir_slug(parent, slug):
    if not slug or slug in {".", ".."} or any(c in slug for c in "/\\\x00"):
        raise ValueError(f"unsafe slug {slug!r}")
    dest = os.path.normpath(os.path.join(parent, slug))
    if os.path.dirname(dest) != os.path.normpath(parent):
        raise ValueError(f"unsafe slug {slug!r}")
    os.makedirs(dest, exist_ok=True)
    return dest


def generate(conn, site_dir="site", page_cap=PAGE_CAP):
    site_dir = os.path.abspath(site_dir)
    os.makedirs(site_dir, exist_ok=True)
    servers = load_servers(conn)
    slugs = assign_slugs([s["name"] for s in servers])
    paged_names = [s["name"] for s in servers[:page_cap]]
    paged = {slugs[n] for n in paged_names}
    dropped = max(0, len(servers) - len(paged_names))
    capped_note = ""
    if dropped:
        capped_note = (f"Generated status pages for {len(paged_names)} of {len(servers)} servers. "
                       f"Cloudflare Pages allows about 20,000 files per deployment, so this build stops at {page_cap} pages "
                       f"and keeps the healthiest, lowest-latency servers. Every server is still listed below.")
        print(f"WARNING: omitted {dropped} per-server HTML files at page cap {page_cap} "
              f"(Cloudflare Pages ~20,000 file limit). Leaderboard still lists every server.", file=sys.stderr)
    tmp = os.path.join(site_dir, ".servers-tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    with open(os.path.join(tmp, "style.css"), "w", encoding="utf-8") as f:
        f.write(STYLE)
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write(_leaderboard(servers, slugs, paged, capped_note, fleet_metar(servers)))
    for name in paged_names:
        s = next(x for x in servers if x["name"] == name)
        dest = _mkdir_slug(tmp, slugs[name])
        with open(os.path.join(dest, "index.html"), "w", encoding="utf-8") as f:
            f.write(_server_page(s, slugs[name]))
    final = os.path.join(site_dir, "servers")
    if os.path.isdir(final):
        shutil.rmtree(final)
    os.rename(tmp, final)
    lastmods = {}
    for s in servers:
        _label, iso = _fmt_checked(s["ts"])
        if iso:
            lastmods[s["name"]] = iso[:10]
    newest = max(lastmods.values(), default="")
    urls = [(f"{ORIGIN}/", newest), (f"{ORIGIN}/servers/", newest)]
    for rel in ("terms/index.html", "privacy/index.html"):
        if os.path.isfile(os.path.join(site_dir, rel)):
            urls.append((f"{ORIGIN}/{rel.split('/')[0]}/", ""))
    for name in paged_names:
        urls.append((page_url(slugs[name]), lastmods.get(name, "")))
    with open(os.path.join(site_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(_sitemap(urls))
    with open(os.path.join(site_dir, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(ROBOTS_TXT)
    print(f"wrote {os.path.join(site_dir, 'servers')} ({len(paged_names)} pages, {len(servers)} servers)")
    return {"servers": len(servers), "pages": len(paged_names), "dropped": dropped, "slugs": slugs}


def self_check():
    """Fixture DB in the probe schema. Does not write the repo site/ directory."""
    import sqlite3, tempfile
    from html.parser import HTMLParser

    class Titles(HTMLParser):
        def __init__(self):
            super().__init__()
            self._in = False
            self.title = []
            self.desc = ""
            self.canon = ""
            self.robots = ""
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "title":
                self._in = True
            elif tag == "meta" and a.get("name") == "description":
                self.desc = a.get("content") or ""
            elif tag == "meta" and a.get("name") == "robots":
                self.robots = a.get("content") or ""
            elif tag == "link" and a.get("rel") == "canonical":
                self.canon = a.get("href") or ""
        def handle_endtag(self, tag):
            if tag == "title":
                self._in = False
        def handle_data(self, data):
            if self._in:
                self.title.append(data)

    schema = """
    CREATE TABLE endpoints(id INTEGER PRIMARY KEY, server_name TEXT, version TEXT, url TEXT UNIQUE, transport TEXT, first_seen REAL, last_seen REAL, priority REAL);
    CREATE TABLE probes(id INTEGER PRIMARY KEY, cycle_id INT, endpoint_id INT, ts REAL, status TEXT, http_status INT, latency_ms REAL, protocol_version TEXT, server_info TEXT, tools_hash TEXT, tools_json TEXT, tool_count INT, error TEXT, era TEXT, supports_2026_07_28 INT, legacy_versions TEXT, spec_readiness TEXT, oauth TEXT);
    CREATE TABLE changes(id INTEGER PRIMARY KEY, endpoint_id INT, ts REAL, prev_probe INT, probe INT, severity TEXT, kind TEXT, tool TEXT, detail TEXT);
    """
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)
    specs = [
        ("com.ufcalendar/fight-api", "https://api.ufcalendar.com/mcp", "ok", 100, "dual", 1, '["2025-06-18"]', 3, None, None),
        ("io.github.foo/bar", "https://example.com/foo", "ok", 400, "legacy_only", 0, '["2025-06-18"]', 1, None, None),
        ("a--b", "https://example.com/a--b", "ok", 800, "dual", 1, "[]", 2, None, None),
        ("a/b", "https://example.com/a/b", "ok", 900, "modern_only", 1, "[]", 4, None, None),
        ("a<b>&c/d", "https://example.com/odd", "ok", 1000, "legacy_only", 0, "[]", 0, None, None),
        ("auth.example/api", "https://example.com/auth", "auth_required", 50, None, None, None, None, None, '{"state":"auth_ok","state_detail":"login flow reachable"}'),
        ("down.example/api", "https://example.com/down", "failed", None, None, None, None, None, "timeout", None),
    ]
    for i, (name, url, status, lat, rd, modern, legacy, tools, err, oauth) in enumerate(specs, 1):
        conn.execute("INSERT INTO endpoints(id, server_name, url, transport, first_seen, last_seen) VALUES(?,?,?,?,?,?)",
                     (i, name, url, "http", now, now))
        conn.execute("""INSERT INTO probes(id, cycle_id, endpoint_id, ts, status, http_status, latency_ms, protocol_version,
            server_info, tools_hash, tools_json, tool_count, error, era, supports_2026_07_28, legacy_versions, spec_readiness, oauth)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (i, 1, i, now, status, 200 if status == "ok" else 500, lat, "2025-06-18", "{}", "h",
                      '[{"name":"search","description":"find"}]', tools, err, "modern" if modern else "legacy",
                      modern, legacy, rd, oauth))
    conn.execute("INSERT INTO changes(endpoint_id, ts, prev_probe, probe, severity, kind, tool, detail) VALUES(?,?,?,?,?,?,?,?)",
                 (1, now - 5, 1, 1, "harmless", "description_changed", "search", "copy"))
    conn.commit()
    root = os.path.dirname(os.path.abspath(__file__))
    for rel, needle in (("site/index.html", 'href="/servers/"'),
                        ("build_site.py", 'href="/servers/"'),
                        ("build_site.py", "build_servers.generate"),
                        ("README.md", "badge_readme.md"),
                        ("README.md", "Embed a free status badge"),
                        (".github/workflows/probe.yml", "python build_site.py"),
                        (".github/workflows/probe.yml", "python build_servers.py --check"),
                        ("badge_readme.md", "https://toolweather.toolweather.workers.dev/badge/<registry-name>.svg")):
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        if needle not in text:
            raise SystemExit(f"self-check: {rel} missing {needle!r}")
    assert badge_url("com.ufcalendar/fight-api") == "https://toolweather.toolweather.workers.dev/badge/com.ufcalendar/fight-api.svg"
    assert server_slug("io.github.foo/bar") == "io.github.foo--bar"
    assert "/" not in server_slug("io.github.foo/bar")
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "terms"))
        os.makedirs(os.path.join(tmp, "privacy"))
        open(os.path.join(tmp, "terms/index.html"), "w").close()
        open(os.path.join(tmp, "privacy/index.html"), "w").close()
        info = generate(conn, site_dir=tmp, page_cap=PAGE_CAP)
        assert info["dropped"] == 0 and info["pages"] == 7
        board = open(os.path.join(tmp, "servers/index.html"), encoding="utf-8").read()
        css = open(os.path.join(tmp, "servers/style.css"), encoding="utf-8").read()
        assert "#F0EBE3" in css and "#A67C52" in css and "Cormorant Garamond" in css and "IBM Plex Sans" in css
        assert "Cormorant+Garamond" in board and 'href="style.css"' in board
        assert 'src="../mark.svg"' in board and "QNH1013" in board and ">HEALTH<" in board
        mark = open(os.path.join(root, "site/mark.svg"), encoding="utf-8").read()
        assert "feGaussianBlur" in mark and "glassa" in mark and "recessa" in mark and "#A67C52" in mark
        assert "mascot_loop" not in mark
        home = open(os.path.join(root, "site/index.html"), encoding="utf-8").read()
        assert "QNH1013" in home and 'id="srvcheck"' in home and "is_safe_to_call" in home
        assert "One connection gives you three tools." in home
        import site_install
        assert "Copy this MCP config" in home
        assert 'href="#install"' in home
        assert site_install.CURSOR_DEEPLINK.endswith(
            "eyJ1cmwiOiJodHRwczovL3Rvb2x3ZWF0aGVyLnRvb2x3ZWF0aGVyLndvcmtlcnMuZGV2L21jcCJ9")
        for pid, text in (
            ("cfg-cursor", site_install.CURSOR_JSON),
            ("cfg-claude-code", site_install.CLAUDE_CODE_JSON),
            ("cfg-desktop", site_install.CLAUDE_DESKTOP_JSON),
            ("cfg-smithery", site_install.SMITHERY_JSON),
            ("cfg-remote-url", site_install.MCP_URL),
            ("cfg-claude-cli", site_install.CLAUDE_CLI),
        ):
            m = re.search(rf'id="{pid}">(.*?)</pre>', home, re.S)
            assert m, pid
            assert html.unescape(m.group(1)) == text, (pid, html.unescape(m.group(1)), text)
        remote = home.split('id="snip-remote"', 1)[1].split("</article>", 1)[0]
        assert "X-API-Key" not in remote and '"headers"' not in remote
        assert "no Authorization header" in remote
        assert '"type": "http"' in site_install.CLAUDE_CODE_JSON
        assert '"type"' not in site_install.CURSOR_JSON
        assert 'id="ready"' in board and 'id="legacy"' in board and 'id="down"' in board
        assert "Copy this MCP config" in board and 'id="snip-remote"' in board
        assert site_install.MCP_URL in board
        assert board.find("com.ufcalendar/fight-api") < board.find("io.github.foo/bar")
        assert ">1</td>" in board
        page = os.path.join(tmp, "servers/com.ufcalendar--fight-api/index.html")
        text = open(page, encoding="utf-8").read()
        parser = Titles()
        parser.feed(text)
        got = html.unescape("".join(parser.title))
        assert got == "com.ufcalendar/fight-api MCP status & 2026-07-28 readiness", got
        assert "down" in parser.desc.lower() and "com.ufcalendar/fight-api" in parser.desc
        assert parser.canon == "https://toolweather.pages.dev/servers/com.ufcalendar--fight-api/"
        assert "index,follow" in parser.robots
        assert 'href="/#install"' in text and "Copy this MCP config" in text
        assert "https://toolweather.toolweather.workers.dev/badge/com.ufcalendar/fight-api.svg" in text
        assert "[![Toolweather](https://toolweather.toolweather.workers.dev/badge/com.ufcalendar/fight-api.svg)](https://toolweather.pages.dev/servers/com.ufcalendar--fight-api/)" in text
        assert "description changed" in text and "search" in text
        odd = open(os.path.join(tmp, "servers", info["slugs"]["a<b>&c/d"], "index.html"), encoding="utf-8").read()
        op = Titles()
        op.feed(odd)
        assert html.unescape("".join(op.title)) == "a<b>&c/d MCP status & 2026-07-28 readiness"
        assert "<script" not in html.unescape("".join(op.title))
        sm = open(os.path.join(tmp, "sitemap.xml"), encoding="utf-8").read()
        assert sm.count("<loc>") == 2 + 2 + 7  # home, leaderboard, terms, privacy, servers
        assert "https://toolweather.pages.dev/servers/" in sm
        assert "https://toolweather.pages.dev/servers/com.ufcalendar--fight-api/" in sm
        robots = open(os.path.join(tmp, "robots.txt"), encoding="utf-8").read()
        assert "Allow: /servers/" in robots
        assert "Sitemap: https://toolweather.pages.dev/sitemap.xml" in robots
        # Cap keeps the leaderboard complete and documents the drop.
        generate(conn, site_dir=tmp, page_cap=1)
        board = open(os.path.join(tmp, "servers/index.html"), encoding="utf-8").read()
        assert "Cloudflare Pages" in board and "7 servers" in board
        assert "down.example/api" in board and "auth.example/api" in board
        pages = [d for d in os.listdir(os.path.join(tmp, "servers")) if os.path.isdir(os.path.join(tmp, "servers", d))]
        assert pages == ["com.ufcalendar--fight-api"], pages
        sm = open(os.path.join(tmp, "sitemap.xml"), encoding="utf-8").read()
        assert sm.count("https://toolweather.pages.dev/servers/") == 2  # leaderboard + the one server (prefix)
    # Our registry names use the same slash-to-double-dash slug the badge links expect.
    for leaf in ("toolweather", "upstream", "paycheck", "spendgate"):
        name = f"io.github.toolweather/{leaf}"
        assert server_slug(name) == f"io.github.toolweather--{leaf}"
        assert "/" not in server_slug(name)
    import mcphealth
    mcphealth.self_check_pins()
    with tempfile.TemporaryDirectory() as tmp2:
        pinned = sqlite3.connect(":memory:")
        pinned.row_factory = sqlite3.Row
        pinned.executescript(schema)
        urls = {
            "toolweather": "https://toolweather.toolweather.workers.dev/mcp",
            "upstream": "https://upstream.toolweather.workers.dev/mcp",
            "paycheck": "https://paycheck.toolweather.workers.dev/mcp",
            "spendgate": "https://spendgate.toolweather.workers.dev/mcp",
        }
        for i, leaf in enumerate(("toolweather", "upstream", "paycheck", "spendgate"), 1):
            name = f"io.github.toolweather/{leaf}"
            pinned.execute("INSERT INTO endpoints(id, server_name, url, transport, first_seen, last_seen) VALUES(?,?,?,?,?,?)",
                           (i, name, urls[leaf], "streamable-http", now, now))
            pinned.execute("""INSERT INTO probes(id, cycle_id, endpoint_id, ts, status, http_status, latency_ms, protocol_version,
                server_info, tools_hash, tools_json, tool_count, error, era, supports_2026_07_28, legacy_versions, spec_readiness, oauth)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (i, 1, i, now, "ok", 200, 80, "2025-06-18", "{}", "h", "[]", 1, None, "legacy", 0, "[]", "legacy_only", None))
        pinned.commit()
        info_p = generate(pinned, site_dir=tmp2)
        for leaf in urls:
            slug = f"io.github.toolweather--{leaf}"
            assert info_p["slugs"][f"io.github.toolweather/{leaf}"] == slug
            text = open(os.path.join(tmp2, "servers", slug, "index.html"), encoding="utf-8").read()
            assert f"io.github.toolweather/{leaf}" in text
            assert f"/badge/io.github.toolweather/{leaf}.svg" in text
            assert f"https://toolweather.pages.dev/servers/{slug}/" in text
    # Collision: alphabetical name keeps the plain slug.
    slugs = assign_slugs(["a/b", "a--b"])
    assert slugs["a--b"] == "a--b"
    assert slugs["a/b"] != "a--b" and slugs["a/b"].startswith("a--b-")
    print("self-check ok", info["pages"], "fixture pages")


if __name__ == "__main__":
    if "--check" in sys.argv:
        self_check()
    else:
        import sqlite3
        db = os.environ.get("TW_DB", "mcp_health.db")
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        generate(c)
