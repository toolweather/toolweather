#!/usr/bin/env python3
"""mcp-health: polite read-only prober for remote MCP servers from the official registry.
Only performs: initialize -> notifications/initialized -> tools/list. Never calls tools."""
import argparse, asyncio, hashlib, json, sqlite3, statistics, time, random, sys
from urllib.parse import urlparse, urljoin
import httpx

REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"
DB = "mcp_health.db"
UA = "mcp-health-probe/0.1 (read-only; initialize+tools/list only)"
PROTO = "2025-06-18"

SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints(id INTEGER PRIMARY KEY, server_name TEXT, version TEXT,
  url TEXT UNIQUE, transport TEXT, first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY, started REAL, finished REAL, n INTEGER);
CREATE TABLE IF NOT EXISTS probes(id INTEGER PRIMARY KEY, cycle_id INT, endpoint_id INT, ts REAL,
  status TEXT, http_status INT, latency_ms REAL, protocol_version TEXT, server_info TEXT,
  tools_hash TEXT, tools_json TEXT, tool_count INT, error TEXT);
CREATE INDEX IF NOT EXISTS ix_probes_ep ON probes(endpoint_id, ts);
CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY, endpoint_id INT, ts REAL, prev_probe INT,
  probe INT, severity TEXT, kind TEXT, tool TEXT, detail TEXT);
"""

def db(path):
    c = sqlite3.connect(path); c.executescript(SCHEMA); return c

# ---------- registry ----------
async def fetch_registry(client, max_pages=500):
    out, cursor = [], None
    for _ in range(max_pages):
        params = {"limit": 100}
        if cursor: params["cursor"] = cursor
        r = await client.get(REGISTRY, params=params); r.raise_for_status()
        j = r.json(); out += j.get("servers", [])
        cursor = j.get("metadata", {}).get("nextCursor")
        if not cursor: break
        await asyncio.sleep(0.2)
    return out

def select_remotes(entries):
    eps = {}
    for e in entries:
        s = e.get("server", {}); m = e.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
        if m.get("isLatest") is False or m.get("status", "active") != "active": continue
        for rm in s.get("remotes") or []:
            t, u = rm.get("type"), rm.get("url", "")
            if t in ("streamable-http", "sse") and u.startswith("http") and "{" not in u:
                eps[u] = (s.get("name"), s.get("version"), u, t)
    return list(eps.values())

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

class AuthRequired(Exception): pass

async def probe_streamable(client, url):
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = rpc(1, "initialize", {"protocolVersion": PROTO, "capabilities": {},
                                 "clientInfo": {"name": "mcp-health", "version": "0.1"}})
    t0 = time.perf_counter()
    r = await client.post(url, json=init, headers=h)
    lat = (time.perf_counter() - t0) * 1000
    if r.status_code in (401, 403): raise AuthRequired(r.status_code)
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
        if r2.status_code in (401, 403): raise AuthRequired(r2.status_code)
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

async def probe_sse(client, url):
    """Legacy HTTP+SSE: GET stream, read 'endpoint' event, POST messages, read replies on stream."""
    t0 = time.perf_counter()
    async with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as s:
        if s.status_code in (401, 403): raise AuthRequired(s.status_code)
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
            if pr.status_code in (401, 403): raise AuthRequired(pr.status_code)
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
             "inputSchema": t.get("inputSchema"), "outputSchema": t.get("outputSchema")} for t in tools]
    keep.sort(key=lambda t: str(t["name"]))
    js = json.dumps(keep, sort_keys=True, separators=(",", ":"))
    return js, hashlib.sha256(js.encode()).hexdigest()

async def probe_one(client, sem, hl, ep, timeout):
    name, ver, url, transport = ep
    host = urlparse(url).netloc
    async with sem:
        lk = await hl.acquire(host)
        try:
            fn = probe_sse if transport == "sse" else probe_streamable
            code, lat, pv, si, tools = await asyncio.wait_for(fn(client, url), timeout)
            js, hsh = norm_tools(tools)
            return dict(status="ok", http_status=code, latency_ms=lat, protocol_version=pv,
                        server_info=json.dumps(si), tools_json=js, tools_hash=hsh, tool_count=len(tools), error=None)
        except AuthRequired as e:
            return dict(status="auth_required", http_status=int(str(e)), error="HTTP %s" % e)
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
    s = schema or {}
    return s.get("properties") or {}, set(s.get("required") or [])

def diff_tools(old_js, new_js):
    old = {t["name"]: t for t in json.loads(old_js)}; new = {t["name"]: t for t in json.loads(new_js)}
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
            ot, nt = op[p].get("type"), np_[p].get("type")
            if ot != nt: ch.append(("breaking", "param_type_changed", n, "%s: %s -> %s" % (p, ot, nt)))
            elif op[p] != np_[p]: ch.append(("harmless", "param_schema_changed", n, p))
        if old[n].get("description") != new[n].get("description"):
            ch.append(("harmless", "description_changed", n, ""))
        if old[n].get("outputSchema") != new[n].get("outputSchema"):
            ch.append(("harmless", "output_schema_changed", n, ""))
    return ch

# ---------- cycle ----------
async def run_cycle(dbpath, limit, concurrency, timeout, sample, host_gap):
    con = db(dbpath); now = time.time()
    limits = httpx.Limits(max_connections=concurrency * 2)
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=httpx.Timeout(timeout, connect=8),
                                 follow_redirects=True, limits=limits) as client:
        entries = await fetch_registry(client)
        eps = select_remotes(entries)
        print(f"registry entries={len(entries)} remote endpoints={len(eps)}", file=sys.stderr)
        eps.sort(key=lambda e: e[2])
        if sample: random.Random(42).shuffle(eps)
        eps = eps[:limit]
        for n, v, u, t in eps:
            con.execute("INSERT INTO endpoints(server_name,version,url,transport,first_seen,last_seen) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(url) DO UPDATE SET server_name=excluded.server_name, version=excluded.version, "
                        "transport=excluded.transport, last_seen=excluded.last_seen", (n, v, u, t, now, now))
        cid = con.execute("INSERT INTO cycles(started,n) VALUES(?,?)", (now, len(eps))).lastrowid; con.commit()
        sem, hl = asyncio.Semaphore(concurrency), HostLimiter(host_gap)
        results = await asyncio.gather(*(probe_one(client, sem, hl, e, timeout) for e in eps))
    nchg = nbreak = 0
    for ep, r in zip(eps, results):
        eid = con.execute("SELECT id FROM endpoints WHERE url=?", (ep[2],)).fetchone()[0]
        prev = con.execute("SELECT id,tools_hash,tools_json FROM probes WHERE endpoint_id=? AND status='ok' "
                           "ORDER BY ts DESC LIMIT 1", (eid,)).fetchone()
        pid = con.execute("INSERT INTO probes(cycle_id,endpoint_id,ts,status,http_status,latency_ms,protocol_version,"
                          "server_info,tools_hash,tools_json,tool_count,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cid, eid, time.time(), r["status"], r.get("http_status"), r.get("latency_ms"),
                           r.get("protocol_version"), r.get("server_info"), r.get("tools_hash"),
                           r.get("tools_json"), r.get("tool_count"), r.get("error"))).lastrowid
        if r["status"] == "ok" and prev and prev[1] != r["tools_hash"]:
            for sev, kind, tool, det in diff_tools(prev[2], r["tools_json"]):
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
    st, fn, n = con.execute("SELECT started,finished,n FROM cycles WHERE id=?", (cid,)).fetchone()
    out = {"cycle": cid, "endpoints": n, "duration_s": round((fn or time.time()) - st, 1),
           "by_status": dict(rows), "median_latency_ms": round(statistics.median(lats), 1) if lats else None,
           "p90_latency_ms": round(statistics.quantiles(lats, n=10)[-1], 1) if len(lats) > 2 else None,
           "protocol_versions": dict(pvs), "top_errors": errs, "changes": nchg, "breaking": nbreak}
    print(json.dumps(out, indent=2, default=str)); return out

def main():
    ap = argparse.ArgumentParser(description="MCP registry health prober")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("probe", help="run one probe cycle")
    p.add_argument("--db", default=DB); p.add_argument("--limit", type=int, default=2000)
    p.add_argument("--concurrency", type=int, default=20); p.add_argument("--timeout", type=float, default=15)
    p.add_argument("--host-gap", type=float, default=2.0, help="min seconds between requests to same host")
    p.add_argument("--sample", action="store_true", help="random (seeded) sample instead of first N by URL")
    r = sp.add_parser("report", help="summarize latest cycle"); r.add_argument("--db", default=DB)
    a = ap.parse_args()
    if a.cmd == "probe": asyncio.run(run_cycle(a.db, a.limit, a.concurrency, a.timeout, a.sample, a.host_gap))
    else:
        con = db(a.db); cid = con.execute("SELECT max(id) FROM cycles").fetchone()[0]; report(con, cid)

if __name__ == "__main__": main()
