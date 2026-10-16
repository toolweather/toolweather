#!/usr/bin/env python3
"""mcp-health: polite read-only prober for remote MCP servers from the official registry.
Only performs: initialize -> notifications/initialized -> tools/list. Never calls tools."""
import re, datetime, argparse, asyncio, hashlib, json, sqlite3, statistics, time, random, sys
from urllib.parse import urlparse, urljoin
import httpx

REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"
DB = "mcp_health.db"
UA = "mcp-health-probe/0.1 (read-only; initialize+tools/list only)"
PROTO = "2025-06-18"
MODERN = "2026-07-28"
MODERN_ERRS = {-32022, -32020, -32021, -32023}

SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints(id INTEGER PRIMARY KEY, server_name TEXT, version TEXT,
  url TEXT UNIQUE, transport TEXT, first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY, started REAL, finished REAL, n INTEGER);
CREATE TABLE IF NOT EXISTS probes(id INTEGER PRIMARY KEY, cycle_id INT, endpoint_id INT, ts REAL,
  status TEXT, http_status INT, latency_ms REAL, protocol_version TEXT, server_info TEXT,
  tools_hash TEXT, tools_json TEXT, tool_count INT, error TEXT, era TEXT,
  supports_2026_07_28 INT, legacy_versions TEXT, spec_readiness TEXT, oauth TEXT);
CREATE INDEX IF NOT EXISTS ix_probes_ep ON probes(endpoint_id, ts);
CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY, endpoint_id INT, ts REAL, prev_probe INT,
  probe INT, severity TEXT, kind TEXT, tool TEXT, detail TEXT);
"""

def db(path):
    c = sqlite3.connect(path); c.executescript(SCHEMA)
    have = {r[1] for r in c.execute("PRAGMA table_info(probes)")}
    for col, typ in [("era","TEXT"),("supports_2026_07_28","INT"),("legacy_versions","TEXT"),
                     ("spec_readiness","TEXT"),("oauth","TEXT")]:
        if col not in have: c.execute(f"ALTER TABLE probes ADD COLUMN {col} {typ}")
    have = {r[1] for r in c.execute("PRAGMA table_info(endpoints)")}
    if "priority" not in have: c.execute("ALTER TABLE endpoints ADD COLUMN priority REAL")
    return c

# ---------- registry ----------
REG_CACHE = "registry_cache.json"
async def _registry_pages(client, extra, max_pages):
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"limit": 100, "version": "latest", **extra}
        if cursor: params["cursor"] = cursor
        for attempt in range(6):
            try:
                r = await client.get(REGISTRY, params=params, timeout=30); r.raise_for_status(); break
            except Exception:
                if attempt == 5: raise
                await asyncio.sleep(min(2 ** attempt * 2, 20))
        j = r.json(); out += j.get("servers", [])
        cursor = j.get("metadata", {}).get("nextCursor")
        if not cursor: break
        await asyncio.sleep(0.1)
    return out

def _ekey(e): return (e.get("server", {}).get("name"), e.get("server", {}).get("version"))
def _upd(e): return e.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {}).get("updatedAt") or ""

async def fetch_registry(client, max_pages=3000, cache_s=6 * 3600):
    """Full registry list, cached. With a cache, only entries updated since the newest cached update are fetched
    (updated_since) and merged; if the registry is slow or down, the stale cache is used."""
    import os
    cached = json.load(open(REG_CACHE)) if os.path.exists(REG_CACHE) else None
    if cached and time.time() - os.path.getmtime(REG_CACHE) < cache_s: return cached
    try:
        if cached:
            since = max((_upd(e) for e in cached), default="")
            since = (datetime.datetime.fromisoformat(since.replace("Z", "+00:00")) - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ") if since else None
            new = await _registry_pages(client, {"updated_since": since} if since else {}, max_pages)
            idx = {_ekey(e): i for i, e in enumerate(cached)}
            for e in new:
                if _ekey(e) in idx: cached[idx[_ekey(e)]] = e
                else: idx[_ekey(e)] = len(cached); cached.append(e)
            out = cached
            print(f"registry: incremental since {since}: {len(new)} updated entries", file=sys.stderr)
        else:
            out = await _registry_pages(client, {}, max_pages)
    except Exception as e:
        if cached:
            print(f"registry fetch failed ({type(e).__name__}); using stale cache", file=sys.stderr); return cached
        raise
    json.dump(out, open(REG_CACHE + ".tmp", "w")); os.replace(REG_CACHE + ".tmp", REG_CACHE)
    return out

def select_remotes(entries):
    """No popularity field exists in the registry, so priority is a proxy:
    publish activity (#versions), metadata completeness, recency."""
    nver = {}
    for e in entries:
        n = e.get("server", {}).get("name"); nver[n] = nver.get(n, 0) + 1
    eps = {}
    for e in entries:
        s = e.get("server", {}); m = e.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
        if m.get("isLatest") is False or m.get("status", "active") != "active": continue
        for rm in s.get("remotes") or []:
            t, u = rm.get("type"), rm.get("url", "")
            if t in ("streamable-http", "sse") and u.startswith("http") and "{" not in u:
                score = (3 * bool(s.get("repository", {}).get("url"))
                         + 1 * bool(s.get("websiteUrl")) + 1 * bool(s.get("title")) + 1 * bool(s.get("icons"))
                         + (2 if (m.get("updatedAt") or "") >= "2026-07" else 0))
                eps[u] = (s.get("name"), s.get("version"), u, t, score)
    return list(eps.values())

# Our own registry servers. select_remotes scores metadata only (max 8). A cycle
# keeps --limit of ~28k remotes (2,000 in probe.yml). These four score 7 — repo,
# website, and title, but no icons — and their URLs sort late inside that band,
# past the ~250 score-7 slots left after every score-8 remote. They never enter
# the index, so /badge stays "unknown" and build_servers.py never writes
# /servers/io.github.toolweather--<name>/. PIN_PRIORITY sits above any registry
# score so the priority sort keeps them; limit_keeping_pinned also keeps them
# when --sample ignores priority.
PIN_PRIORITY = 100
PINNED_REMOTES = (
    ("io.github.toolweather/toolweather", "https://toolweather.toolweather.workers.dev/mcp", "streamable-http"),
    ("io.github.toolweather/upstream", "https://upstream.toolweather.workers.dev/mcp", "streamable-http"),
    ("io.github.toolweather/paycheck", "https://paycheck.toolweather.workers.dev/mcp", "streamable-http"),
    ("io.github.toolweather/spendgate", "https://spendgate.toolweather.workers.dev/mcp", "streamable-http"),
)

def ensure_pinned(eps):
    """Boost pinned remotes above the --limit cutoff. Add the known endpoint when
    the registry list does not contain that name or URL yet (version stays empty
    until a registry row supplies one). A registry row for the same name wins on
    URL, so a moved endpoint is probed at the published URL."""
    by_url = {e[2]: e for e in eps}
    by_name = {}
    for e in eps:
        by_name.setdefault(e[0], e)
    replaced, consumed = {}, set()
    extras = []
    for name, url, transport in PINNED_REMOTES:
        hit = by_url.get(url)
        if hit:
            replaced[hit[2]] = (name, hit[1], url, hit[3] or transport, PIN_PRIORITY)
            consumed.add(hit[2]); continue
        named = by_name.get(name)
        if named and named[2] not in consumed:
            replaced[named[2]] = (named[0], named[1], named[2], named[3] or transport, PIN_PRIORITY)
            consumed.add(named[2]); continue
        extras.append((name, "", url, transport, PIN_PRIORITY))
    return [replaced.get(e[2], e) for e in eps] + extras

def limit_keeping_pinned(eps, limit):
    """Keep every pinned endpoint inside `limit`, dropping the lowest-priority tail."""
    by_url, by_name = {}, {}
    for e in eps:
        by_url.setdefault(e[2], e); by_name.setdefault(e[0], e)
    chosen, seen = [], set()
    for name, url, _t in PINNED_REMOTES:
        e = by_url.get(url) or by_name.get(name)
        if e and e[2] not in seen:
            chosen.append(e); seen.add(e[2])
    rest = [e for e in eps if e[2] not in seen]
    if limit is None: return chosen + rest
    return chosen + rest[:max(0, limit - len(chosen))]

def self_check_pins():
    """The production cutoff is score 8 (about 1,750 remotes) then the alphabetically
    earliest score-7 URLs until --limit 2000. These four are score 7 and sort late."""
    fillers = [(f"io.github.example/s{i}", "1", f"https://a{i:05d}.example/mcp", "streamable-http", 8) for i in range(20)]
    early7 = [(f"io.github.early/s{i}", "1", f"https://b{i:05d}.example/mcp", "streamable-http", 7) for i in range(10)]
    late = [(n, "1.2.0", u, t, 7) for n, u, t in PINNED_REMOTES]
    raw = fillers + early7 + late
    ordered = sorted(sorted(raw, key=lambda e: e[2]), key=lambda e: -e[4])
    assert not any(e[0].startswith("io.github.toolweather/") for e in ordered[:20])
    pinned = ensure_pinned(raw)
    ordered = sorted(sorted(pinned, key=lambda e: e[2]), key=lambda e: -e[4])
    kept = limit_keeping_pinned(ordered, 20)
    assert len(kept) == 20
    by_name = {e[0]: e for e in kept}
    for n, u, t in PINNED_REMOTES:
        assert by_name[n][1] == "1.2.0" and by_name[n][2] == u and by_name[n][3] == t and by_name[n][4] == PIN_PRIORITY
    added = ensure_pinned(fillers)
    assert {e[2] for e in added if e[4] == PIN_PRIORITY} == {u for _n, u, _t in PINNED_REMOTES}
    assert all(e[1] == "" for e in added if e[4] == PIN_PRIORITY)
    shuffled = list(added)
    random.Random(0).shuffle(shuffled)
    sampled = limit_keeping_pinned(shuffled, 10)
    assert len(sampled) == 10 and {u for _n, u, _t in PINNED_REMOTES} <= {e[2] for e in sampled}
    moved = ensure_pinned([("io.github.toolweather/upstream", "9", "https://new.example/mcp", "sse", 7)])
    up = next(e for e in moved if e[0] == "io.github.toolweather/upstream")
    assert up[1:] == ("9", "https://new.example/mcp", "sse", PIN_PRIORITY)
    assert "https://upstream.toolweather.workers.dev/mcp" not in {e[2] for e in moved}

# ---------- probing ----------
def rpc(id_, method, params=None):
    m = {"jsonrpc": "2.0", "method": method}
    if id_ is not None: m["id"] = id_
    if params is not None: m["params"] = params
    return m

def parse_body(r, want_id):
    ct = r.headers.get("content-type", "")
    text = r.text
    msgs = []
    if "text/event-stream" in ct:
        for block in text.split("\n\n"):
            data = "\n".join(l[5:].lstrip() for l in block.splitlines() if l.startswith("data:"))
            if data:
                try: msgs.append(json.loads(data))
                except Exception: pass
    else:
        j = json.loads(text); msgs = j if isinstance(j, list) else [j]
    for m in msgs:
        if isinstance(m, dict) and m.get("id") == want_id: return m
    raise ValueError("no JSON-RPC response with id %s" % want_id)

class AuthRequired(Exception):
    def __init__(self, code, www=None): super().__init__(code); self.code, self.www = code, www

def _auth(r):
    if r.status_code in (401, 403): raise AuthRequired(r.status_code, r.headers.get("www-authenticate"))

def modern_meta():
    return {"io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "mcp-health", "version": "0.2"},
            "io.modelcontextprotocol/clientCapabilities": {}}

async def probe_modern(client, url):
    """2026-07-28: stateless; server/discover then tools/list, each with _meta + headers.
    Returns None if the server looks legacy (fall back), else result tuple."""
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
         "MCP-Protocol-Version": MODERN}
    t0 = time.perf_counter()
    r = await client.post(url, json=rpc(1, "server/discover", {"_meta": modern_meta()}),
                          headers={**h, "Mcp-Method": "server/discover"})
    lat = (time.perf_counter() - t0) * 1000
    _auth(r)
    try: m = parse_body(r, 1)
    except Exception: return None
    if "error" in m:
        e = m["error"]
        if e.get("code") == -32022:
            sup = (e.get("data") or {}).get("supported") or []
            return ("unsupported", sup)   # modern-aware server, but not 2026-07-28 => use legacy list
        return None
    res = m.get("result") or {}
    if "supportedVersions" not in res: return None
    sup = res["supportedVersions"]
    if MODERN not in sup: return ("unsupported", sup)
    tools, cursor, i = [], None, 2
    while True:
        params = {"_meta": modern_meta()}
        if cursor: params["cursor"] = cursor
        r2 = await client.post(url, json=rpc(i, "tools/list", params), headers={**h, "Mcp-Method": "tools/list"})
        _auth(r2); r2.raise_for_status()
        m2 = parse_body(r2, i)
        if "error" in m2: raise ValueError("tools/list error: %s" % m2["error"])
        tools += m2["result"].get("tools", []); cursor = m2["result"].get("nextCursor"); i += 1
        if not cursor or i > 20: break
    si = (res.get("_meta") or {}).get("io.modelcontextprotocol/serverInfo")
    return ("ok", r.status_code, lat, MODERN, si, tools, sup)

_AS_CACHE = {}   # per-cycle cache: authorization-server URL -> asyncio.Task with its check result

def _err(e):
    n = type(e).__name__
    if "SSL" in n or "ssl" in str(e).lower() or "certificate" in str(e).lower(): return "tls_error"
    if "Timeout" in n: return "timeout"
    if "Connect" in n: return "connect_error"
    return n

async def _reach(client, method, url, **kw):
    """Is an OAuth endpoint reachable over TLS? Any HTTP answer below 500 counts (400/401/405 are expected
    for unauthenticated requests). Never sends credentials and never registers a client."""
    try:
        r = await client.request(method, url, timeout=6, follow_redirects=False, **kw)
        return {"ok": r.status_code < 500, "http_status": r.status_code}
    except Exception as e:
        return {"ok": False, "error": _err(e)}

async def _check_as(client, issuer):
    """RFC 8414 (+ OIDC fallback) metadata for one authorization server, then reachability of its endpoints."""
    out = {"issuer": issuer, "metadata_found": False}
    pu = urlparse(issuer); base = f"{pu.scheme}://{pu.netloc}"; path = pu.path.rstrip("/")
    cands = [base + "/.well-known/oauth-authorization-server" + path, base + "/.well-known/openid-configuration" + path]
    if path: cands += [issuer.rstrip("/") + "/.well-known/openid-configuration", base + "/.well-known/oauth-authorization-server"]
    last = None
    for c in cands:
        try:
            r = await client.get(c, timeout=6)
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, dict) and (j.get("token_endpoint") or j.get("authorization_endpoint")):
                    out.update(metadata_found=True, metadata_url=c, token_endpoint=j.get("token_endpoint"),
                               authorization_endpoint=j.get("authorization_endpoint"),
                               registration_endpoint=j.get("registration_endpoint"),
                               pkce_s256="S256" in (j.get("code_challenge_methods_supported") or []),
                               cimd=bool(j.get("client_id_metadata_document_supported")))
                    break
            last = f"HTTP {r.status_code}"
        except Exception as e: last = _err(e)
    if not out["metadata_found"]:
        out["error"] = last; return out
    if out["authorization_endpoint"]: out["authorization_endpoint_check"] = await _reach(client, "GET", out["authorization_endpoint"])
    if out["token_endpoint"]: out["token_endpoint_check"] = await _reach(client, "POST", out["token_endpoint"], data={"grant_type": "authorization_code"})
    if out["registration_endpoint"]: out["registration_endpoint_check"] = await _reach(client, "GET", out["registration_endpoint"])
    return out

def auth_state(info):
    """auth_ok: login flow reachable | auth_broken: OAuth advertised but a step fails | auth_opaque: no OAuth discovery (API key etc.)"""
    if not info.get("prm_found"):
        if info.get("resource_metadata_url"): return "auth_broken", "resource metadata URL in WWW-Authenticate does not resolve"
        return "auth_opaque", "requires credentials; no OAuth discovery advertised (likely API key or custom auth)"
    a = info.get("as_check")
    if not info.get("authorization_servers"): return "auth_broken", "protected-resource metadata lists no authorization server"
    if not a or not a.get("metadata_found"): return "auth_broken", f"authorization server metadata does not resolve ({(a or {}).get('error')})"
    for k, lbl in (("token_endpoint", "token endpoint"), ("authorization_endpoint", "authorization endpoint")):
        if not a.get(k): return "auth_broken", f"metadata has no {lbl}"
        c = a.get(k + "_check") or {}
        if not c.get("ok"):
            if c.get("http_status"):   # reachable over TLS but 5xx to our parameter-less request: real requests may still work
                return "auth_suspect", f"{lbl} answered HTTP {c['http_status']} to a parameter-less check; login may still work"
            return "auth_broken", f"{lbl} unreachable ({c.get('error')})"
    reg = "open (dynamic client registration)" if a.get("registration_endpoint") and (a.get("registration_endpoint_check") or {}).get("ok") \
        else ("client ID metadata documents" if a.get("cimd") else "pre-registered clients only")
    return "auth_ok", f"OAuth login flow reachable (metadata, authorization + token endpoints OK); registration: {reg}"

async def oauth_info(client, url, www):
    """Without credentials: RFC 9728 protected-resource metadata -> RFC 8414 auth-server metadata ->
    authorization/token/registration endpoint reachability (TLS). Result has state auth_ok/auth_broken/auth_opaque."""
    info = {"www_authenticate": (www or "")[:300] or None, "resource_metadata_url": None,
            "prm_found": False, "authorization_servers": None}
    if www:
        mm = re.search(r'resource_metadata="?([^",\s]+)', www)
        if mm: info["resource_metadata_url"] = mm.group(1)
    cands = [info["resource_metadata_url"]] if info["resource_metadata_url"] else []
    pu = urlparse(url); base = f"{pu.scheme}://{pu.netloc}"
    cands += [base + "/.well-known/oauth-protected-resource" + pu.path.rstrip("/"),
              base + "/.well-known/oauth-protected-resource"]
    for c in cands:
        try:
            r = await client.get(c, timeout=6)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                j = r.json(); info["prm_found"] = True; info["prm_url"] = c
                info["authorization_servers"] = j.get("authorization_servers"); break
        except Exception: pass
    info["advertises_oauth"] = bool(info["prm_found"] or info["resource_metadata_url"]
                                    or (www and "bearer" in www.lower()))
    if info["prm_found"] and info["authorization_servers"]:
        iss = str(info["authorization_servers"][0])
        if iss not in _AS_CACHE: _AS_CACHE[iss] = asyncio.ensure_future(_check_as(client, iss))
        try: info["as_check"] = await asyncio.shield(_AS_CACHE[iss])
        except Exception as e: info["as_check"] = {"issuer": iss, "metadata_found": False, "error": _err(e)}
    info["state"], info["state_detail"] = auth_state(info)
    if info["state"] == "auth_ok": info["dcr"] = "dynamic client registration" in info["state_detail"]
    return info

async def probe_streamable(client, url):
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = rpc(1, "initialize", {"protocolVersion": PROTO, "capabilities": {},
                                 "clientInfo": {"name": "mcp-health", "version": "0.1"}})
    t0 = time.perf_counter()
    r = await client.post(url, json=init, headers=h)
    lat = (time.perf_counter() - t0) * 1000
    _auth(r)
    r.raise_for_status()
    res = parse_body(r, 1)
    if "error" in res: raise ValueError("initialize error: %s" % res["error"])
    sid = r.headers.get("mcp-session-id")
    if sid: h["Mcp-Session-Id"] = sid
    pv = res["result"].get("protocolVersion"); h["MCP-Protocol-Version"] = pv or PROTO
    await client.post(url, json=rpc(None, "notifications/initialized"), headers=h)
    tools, cursor, i = [], None, 2
    while True:
        r2 = await client.post(url, json=rpc(i, "tools/list", {"cursor": cursor} if cursor else {}), headers=h)
        _auth(r2)
        r2.raise_for_status()
        m = parse_body(r2, i)
        if "error" in m: raise ValueError("tools/list error: %s" % m["error"])
        tools += m["result"].get("tools", []); cursor = m["result"].get("nextCursor")
        i += 1
        if not cursor or i > 20: break
    if sid:
        try: await client.delete(url, headers=h)
        except Exception: pass
    return r.status_code, lat, pv, res["result"].get("serverInfo"), tools

async def probe_legacy_init(client, url):
    """initialize only (no tools/list); returns negotiated legacy version or None."""
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    r = await client.post(url, json=rpc(1, "initialize", {"protocolVersion": PROTO, "capabilities": {},
                          "clientInfo": {"name": "mcp-health", "version": "0.2"}}), headers=h)
    if r.status_code >= 400: return None
    m = parse_body(r, 1); pv = (m.get("result") or {}).get("protocolVersion")
    sid = r.headers.get("mcp-session-id")
    if sid:
        try: await client.delete(url, headers={**h, "Mcp-Session-Id": sid})
        except Exception: pass
    return pv if pv and pv < MODERN else None

async def probe_sse(client, url):
    """Legacy HTTP+SSE: GET stream, read 'endpoint' event, POST messages, read replies on stream."""
    t0 = time.perf_counter()
    async with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as s:
        _auth(s)
        s.raise_for_status()
        lines = s.aiter_lines()
        async def next_event():
            ev, data = None, []
            async for l in lines:
                if l == "":
                    if data or ev: return ev, "\n".join(data)
                    continue
                if l.startswith("event:"): ev = l[6:].strip()
                elif l.startswith("data:"): data.append(l[5:].lstrip())
            raise ValueError("stream closed")
        post_url = None
        while post_url is None:
            ev, d = await next_event()
            if ev == "endpoint": post_url = urljoin(url, d.strip())
        async def call(id_, method, params):
            pr = await client.post(post_url, json=rpc(id_, method, params))
            _auth(pr)
            pr.raise_for_status()
            if id_ is None: return None
            while True:
                ev, d = await next_event()
                try: m = json.loads(d)
                except Exception: continue
                if m.get("id") == id_: return m
        res = await call(1, "initialize", {"protocolVersion": PROTO, "capabilities": {},
                                           "clientInfo": {"name": "mcp-health", "version": "0.1"}})
        lat = (time.perf_counter() - t0) * 1000
        if "error" in res: raise ValueError("initialize error: %s" % res["error"])
        await call(None, "notifications/initialized", None)
        m = await call(2, "tools/list", {})
        if "error" in m: raise ValueError("tools/list error: %s" % m["error"])
        return s.status_code, lat, res["result"].get("protocolVersion"), res["result"].get("serverInfo"), m["result"].get("tools", [])

DEADLINE = None
START = time.monotonic()   # time budget counts from process start, so a slow registry fetch can't push the run past its timeout
class HostLimiter:
    """At most 1 concurrent probe per host, min gap between requests to the same host."""
    def __init__(self, gap=2.0): self.locks, self.last, self.gap = {}, {}, gap
    async def acquire(self, host):
        lk = self.locks.setdefault(host, asyncio.Lock()); await lk.acquire()
        wait = self.last.get(host, 0) + self.gap - time.monotonic()
        if wait > 0: await asyncio.sleep(wait)
        return lk
    def release(self, host, lk): self.last[host] = time.monotonic(); lk.release()

def norm_tools(tools):
    keep = [{"name": t.get("name"), "description": t.get("description"),
             "inputSchema": t.get("inputSchema"), "outputSchema": t.get("outputSchema")} for t in (tools or []) if isinstance(t, dict)]
    keep.sort(key=lambda t: str(t["name"]))
    js = json.dumps(keep, sort_keys=True, separators=(",", ":"))
    return js, hashlib.sha256(js.encode()).hexdigest()

async def probe_one(client, sem, hl, ep, timeout):
    name, ver, url, transport = ep[:4]
    host = urlparse(url).netloc
    async with sem:
        if DEADLINE and time.monotonic() > DEADLINE: return None   # out of time budget: skip, keep cycle
        lk = await hl.acquire(host)
        try:
            async def go():
                sup = None
                if transport != "sse":
                    mr = await probe_modern(client, url)
                    if mr and mr[0] == "ok":
                        _, code, lat, pv, si, tools, sup = mr
                        # also try legacy initialize so dual-era servers aren't labelled modern_only
                        try:
                            lr = await probe_legacy_init(client, url)
                            if lr: sup = sorted(set(sup) | {lr})
                        except Exception: pass
                        return code, lat, pv, si, tools, "modern", sup
                    if mr and mr[0] == "unsupported": sup = mr[1]
                fn = probe_sse if transport == "sse" else probe_streamable
                code, lat, pv, si, tools = await fn(client, url)
                return code, lat, pv, si, tools, "legacy", sup
            code, lat, pv, si, tools, era, sup = await asyncio.wait_for(go(), timeout)
            js, hsh = norm_tools(tools)
            modern = era == "modern" or (sup and MODERN in sup)
            legacy = sorted(set([v for v in (sup or []) if v < MODERN] + ([pv] if era == "legacy" and pv else [])))
            readiness = "dual" if modern and legacy else ("modern_only" if modern else "legacy_only")
            return dict(status="ok", http_status=code, latency_ms=lat, protocol_version=pv, era=era,
                        supports_2026_07_28=int(bool(modern)), legacy_versions=json.dumps(legacy), spec_readiness=readiness,
                        server_info=json.dumps(si), tools_json=js, tools_hash=hsh, tool_count=len(tools), error=None)
        except AuthRequired as e:
            try: oi = await asyncio.wait_for(oauth_info(client, url, e.www), 25)
            except Exception: oi = {"advertises_oauth": bool(e.www), "www_authenticate": e.www, "state": "auth_unknown", "state_detail": "OAuth checks timed out"}
            return dict(status="auth_required", http_status=e.code, error="HTTP %s" % e.code, oauth=json.dumps(oi))
        except httpx.HTTPStatusError as e:
            return dict(status="failed", http_status=e.response.status_code, error="HTTP %d" % e.response.status_code)
        except asyncio.TimeoutError:
            return dict(status="failed", error="timeout")
        except Exception as e:
            return dict(status="failed", error=(type(e).__name__ + ": " + str(e))[:300])
        finally:
            hl.release(host, lk)

# ---------- diffing ----------
def _props(schema):
    # JSON Schema allows boolean schemas (true/false) anywhere; treat non-dicts as having no properties
    s = schema if isinstance(schema, dict) else {}
    props = s.get("properties"); props = props if isinstance(props, dict) else {}
    req = s.get("required"); req = req if isinstance(req, list) else []
    return props, set(r for r in req if isinstance(r, str))

def _ptype(p):
    # Boolean, null, and array schemas are valid JSON Schema and have no "type".
    # Differing whole values are param_schema_changed; do not invent a type name.
    return p.get("type") if isinstance(p, dict) else None

def _tools_by_name(js):
    if not js: return {}
    raw = json.loads(js)
    if not isinstance(raw, list):
        raise ValueError("tools JSON is %s, expected a list" % type(raw).__name__)
    out = {}
    for t in raw:
        if not isinstance(t, dict): continue
        name = t.get("name")
        if not isinstance(name, str): continue   # missing, null, or non-string name
        out[name] = t
    return out

def diff_tools(old_js, new_js):
    old = _tools_by_name(old_js); new = _tools_by_name(new_js)
    ch = []
    for n in old.keys() - new.keys(): ch.append(("breaking", "tool_removed", n, ""))
    for n in new.keys() - old.keys(): ch.append(("harmless", "tool_added", n, ""))
    for n in old.keys() & new.keys():
        op, oreq = _props(old[n].get("inputSchema")); np_, nreq = _props(new[n].get("inputSchema"))
        for p in nreq - oreq: ch.append(("breaking", "required_param_added", n, p))
        for p in oreq - nreq: ch.append(("harmless", "param_made_optional", n, p))
        for p in op.keys() - np_.keys(): ch.append(("breaking", "param_removed", n, p))
        for p in np_.keys() - op.keys():
            if p not in nreq: ch.append(("harmless", "optional_param_added", n, p))
        for p in op.keys() & np_.keys():
            ot, nt = _ptype(op[p]), _ptype(np_[p])
            if ot != nt: ch.append(("breaking", "param_type_changed", n, "%s: %s -> %s" % (p, ot, nt)))
            elif op[p] != np_[p]: ch.append(("harmless", "param_schema_changed", n, p))
        if old[n].get("description") != new[n].get("description"):
            ch.append(("harmless", "description_changed", n, ""))
        if old[n].get("outputSchema") != new[n].get("outputSchema"):
            ch.append(("harmless", "output_schema_changed", n, ""))
    return ch

def diff_endpoint(old_js, new_js, label):
    # One bad payload must not abort the cycle before it commits.
    try: return diff_tools(old_js, new_js)
    except Exception as e:
        print("diff failed for %s: %s: %s" % (label, type(e).__name__, str(e)[:300]), file=sys.stderr)
        return []

# ---------- cycle ----------
async def run_cycle(dbpath, limit, concurrency, timeout, sample, host_gap, from_cycle=None, budget=None):
    con = db(dbpath); now = time.time()
    limits = httpx.Limits(max_connections=concurrency * 2)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=httpx.Timeout(timeout, connect=8),
                                 follow_redirects=True, limits=limits) as client:
        try:
            entries = await fetch_registry(client)
            eps = select_remotes(entries)
        except Exception as e:   # registry down and no cached list: keep probing what we already track
            entries = []
            eps = [tuple(r) for r in con.execute("SELECT server_name,version,url,transport,coalesce(priority,0) FROM endpoints "
                                                 "WHERE last_seen >= ?", (now - 3 * 86400,))]
            if not eps: raise
            print(f"registry unavailable ({type(e).__name__}); probing {len(eps)} known endpoints from history", file=sys.stderr)
        print(f"registry entries={len(entries)} remote endpoints={len(eps)}", file=sys.stderr)
        eps = ensure_pinned(eps)
        eps.sort(key=lambda e: e[2])
        if sample: random.Random(42).shuffle(eps)   # sample mode keeps v1 ordering (seed 42) for comparability
        else: eps.sort(key=lambda e: -e[4])         # default: priority proxy, highest first
        if from_cycle:
            want = {r[0] for r in con.execute("SELECT e.url FROM probes p JOIN endpoints e ON e.id=p.endpoint_id WHERE p.cycle_id=?", (from_cycle,))}
            known = {e[2] for e in eps}
            eps = [e for e in eps if e[2] in want] + [tuple(r) + (0,) for r in con.execute(
                "SELECT server_name,version,url,transport FROM endpoints") if r[2] in want and r[2] not in known]
            eps = eps[:limit]   # replay stays the historical set; do not inject pins that cycle never saw
        else:
            eps = limit_keeping_pinned(eps, limit)
        print(f"probe set={len(eps)} pinned={[e[0] for e in eps if e[0] in {n for n,_,_ in PINNED_REMOTES}]}", file=sys.stderr)
        for n, v, u, t, pr in eps:
            con.execute("INSERT INTO endpoints(server_name,version,url,transport,first_seen,last_seen,priority) VALUES(?,?,?,?,?,?,?) "
                        "ON CONFLICT(url) DO UPDATE SET server_name=excluded.server_name, version=excluded.version, "
                        "transport=excluded.transport, last_seen=excluded.last_seen, priority=excluded.priority", (n, v, u, t, now, now, pr))
        cid = con.execute("INSERT INTO cycles(started,n) VALUES(?,?)", (now, len(eps))).lastrowid; con.commit()
        sem, hl = asyncio.Semaphore(concurrency), HostLimiter(host_gap)
        global DEADLINE
        DEADLINE = START + budget if budget else None
        results = await asyncio.gather(*(probe_one(client, sem, hl, e, timeout) for e in eps))
    nchg = nbreak = 0
    skipped = sum(r is None for r in results)
    if skipped: print(f"skipped {skipped} endpoints (time budget)", file=sys.stderr)
    for ep, r in zip(eps, results):
        if r is None: continue
        eid = con.execute("SELECT id FROM endpoints WHERE url=?", (ep[2],)).fetchone()[0]
        prev = con.execute("SELECT id,tools_hash,tools_json FROM probes WHERE endpoint_id=? AND status='ok' "
                           "ORDER BY ts DESC LIMIT 1", (eid,)).fetchone()
        pid = con.execute("INSERT INTO probes(cycle_id,endpoint_id,ts,status,http_status,latency_ms,protocol_version,"
                          "server_info,tools_hash,tools_json,tool_count,error,era,supports_2026_07_28,legacy_versions,spec_readiness,oauth) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, eid, time.time(), r["status"], r.get("http_status"), r.get("latency_ms"),
                           r.get("protocol_version"), r.get("server_info"), r.get("tools_hash"),
                           r.get("tools_json"), r.get("tool_count"), r.get("error"), r.get("era"), r.get("supports_2026_07_28"),
                           r.get("legacy_versions"), r.get("spec_readiness"), r.get("oauth"))).lastrowid
        if r["status"] == "ok" and prev and prev[1] != r["tools_hash"]:
            for sev, kind, tool, det in diff_endpoint(prev[2], r["tools_json"], "%s (%s)" % (ep[0], ep[2])):
                con.execute("INSERT INTO changes(endpoint_id,ts,prev_probe,probe,severity,kind,tool,detail) VALUES(?,?,?,?,?,?,?,?)",
                            (eid, time.time(), prev[0], pid, sev, kind, tool, det))
                nchg += 1; nbreak += sev == "breaking"
    con.execute("UPDATE cycles SET finished=? WHERE id=?", (time.time(), cid)); con.commit()
    return report(con, cid, nchg, nbreak)

def report(con, cid, nchg=None, nbreak=None):
    rows = con.execute("SELECT status,count(*) FROM probes WHERE cycle_id=? GROUP BY status", (cid,)).fetchall()
    lats = [r[0] for r in con.execute("SELECT latency_ms FROM probes WHERE cycle_id=? AND status='ok'", (cid,))]
    pvs = con.execute("SELECT protocol_version,count(*) FROM probes WHERE cycle_id=? AND status='ok' GROUP BY 1 ORDER BY 2 DESC", (cid,)).fetchall()
    errs = con.execute("SELECT error,count(*) FROM probes WHERE cycle_id=? AND status='failed' GROUP BY substr(error,1,40) ORDER BY 2 DESC LIMIT 8", (cid,)).fetchall()
    rd = dict(con.execute("SELECT coalesce(spec_readiness,'?'),count(*) FROM probes WHERE cycle_id=? AND status='ok' GROUP BY 1", (cid,)).fetchall())
    oa = [json.loads(x[0]) if x[0] else {} for x in con.execute("SELECT oauth FROM probes WHERE cycle_id=? AND status='auth_required'", (cid,))]
    oauth = {"auth_required": len(oa), "advertises_oauth": sum(bool(o.get("advertises_oauth")) for o in oa),
             "protected_resource_metadata_found": sum(bool(o.get("prm_found")) for o in oa),
             "www_authenticate_resource_metadata": sum(bool(o.get("resource_metadata_url")) for o in oa)}
    chg = dict(con.execute("SELECT severity||':'||kind,count(*) FROM changes c JOIN probes p ON p.id=c.probe WHERE p.cycle_id=? GROUP BY 1", (cid,)).fetchall())
    eps_chg = con.execute("SELECT count(DISTINCT c.endpoint_id), count(DISTINCT CASE WHEN severity='breaking' THEN c.endpoint_id END) FROM changes c JOIN probes p ON p.id=c.probe WHERE p.cycle_id=?", (cid,)).fetchone()
    st, fn, n = con.execute("SELECT started,finished,n FROM cycles WHERE id=?", (cid,)).fetchone()
    out = {"cycle": cid, "endpoints": n, "duration_s": round((fn or time.time()) - st, 1),
           "by_status": dict(rows), "median_latency_ms": round(statistics.median(lats), 1) if lats else None,
           "p90_latency_ms": round(statistics.quantiles(lats, n=10)[-1], 1) if len(lats) > 2 else None,
           "protocol_versions": dict(pvs), "top_errors": errs, "spec_readiness": rd, "oauth": oauth, "change_kinds": chg,
           "endpoints_changed": eps_chg[0], "endpoints_breaking": eps_chg[1], "changes": nchg, "breaking": nbreak}
    print(json.dumps(out, indent=2, default=str)); return out

def main():
    ap = argparse.ArgumentParser(description="MCP registry health prober")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("probe", help="run one probe cycle")
    p.add_argument("--db", default=DB); p.add_argument("--limit", type=int, default=2000)
    p.add_argument("--concurrency", type=int, default=64); p.add_argument("--timeout", type=float, default=12)
    p.add_argument("--host-gap", type=float, default=2.0, help="min seconds between requests to same host")
    p.add_argument("--sample", action="store_true", help="random (seeded) sample instead of priority order")
    p.add_argument("--budget", type=float, help="seconds; stop starting new probes after this")
    p.add_argument("--from-cycle", type=int, help="re-probe exactly the endpoints of an earlier cycle")
    r = sp.add_parser("report", help="summarize latest cycle"); r.add_argument("--db", default=DB)
    pr = sp.add_parser("prune", help="drop old tool JSON / probes to keep the DB small"); pr.add_argument("--db", default=DB)
    pr.add_argument("--days", type=float, default=7)
    a = ap.parse_args()
    if a.cmd == "probe": asyncio.run(run_cycle(a.db, a.limit, a.concurrency, a.timeout, a.sample, a.host_gap, a.from_cycle, a.budget))
    elif a.cmd == "prune":
        con = db(a.db)
        keep = "SELECT max(id) FROM probes WHERE status='ok' GROUP BY endpoint_id"
        con.execute(f"UPDATE probes SET tools_json=NULL WHERE tools_json IS NOT NULL AND id NOT IN ({keep})")
        con.execute(f"DELETE FROM probes WHERE ts < ? AND id NOT IN ({keep}) AND id NOT IN (SELECT max(id) FROM probes GROUP BY endpoint_id)",
                    (time.time() - a.days * 86400,))
        con.commit(); con.execute("VACUUM"); con.close()
        import os; print("db bytes", os.path.getsize(a.db))
    else:
        con = db(a.db); cid = con.execute("SELECT max(id) FROM cycles").fetchone()[0]; report(con, cid)

if __name__ == "__main__": main()
