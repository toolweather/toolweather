"""Toolweather query core: read-only lookups over mcp_health.db."""
import json, math, re, sqlite3, time, os, secrets, datetime
from collections import Counter

DB = os.environ.get("TW_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_health.db"))
KEYS_DB = os.environ.get("TW_KEYS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.db"))
FREE_DAILY = int(os.environ.get("TW_FREE_DAILY", "1000"))
STALE_S, SLOW_MS, RECENT_BREAK_S = 3600, 3000, 7 * 86400

def con():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False); c.row_factory = sqlite3.Row; return c

def iso(ts): return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat(timespec="seconds") if ts else None

LATEST = """SELECT e.id eid, e.server_name, e.url, e.transport, p.* FROM endpoints e
  JOIN probes p ON p.id = (SELECT id FROM probes WHERE endpoint_id=e.id ORDER BY id DESC LIMIT 1)"""

def find_endpoints(c, server):
    rows = c.execute(LATEST + " WHERE e.server_name=? OR e.url=?", (server, server)).fetchall()
    if not rows:
        rows = c.execute(LATEST + " WHERE e.server_name LIKE ? OR e.url LIKE ? LIMIT 5", (f"%{server}%", f"%{server}%")).fetchall()
    return rows

def endpoint_report(c, r, tool=None):
    now = time.time(); reasons = []
    last = c.execute("SELECT c.ts, c.severity, c.kind, c.tool, c.detail FROM changes c WHERE endpoint_id=? "
                     + ("AND c.tool=? " if tool else "") + "ORDER BY c.ts DESC LIMIT 20",
                     (r["eid"], tool) if tool else (r["eid"],)).fetchall()
    last_change = None
    if last:
        ts = last[0]["ts"]; batch = [x for x in last if x["ts"] >= ts - 1]
        last_change = {"at": iso(ts), "breaking": any(x["severity"] == "breaking" for x in batch),
                       "changes": [{"severity": x["severity"], "kind": x["kind"], "tool": x["tool"], "detail": x["detail"]} for x in batch]}
    tools = [t["name"] for t in json.loads(r["tools_json"])] if r["tools_json"] else None
    st = r["status"]
    if st == "failed": verdict = "unsafe"; reasons.append(f"server down/broken: {r['error']}")
    elif st == "auth_required":
        oa = json.loads(r["oauth"]) if r["oauth"] else {}
        state = oa.get("state")
        if state == "auth_broken": verdict = "unsafe"; reasons.append(f"auth_broken: {oa.get('state_detail')}; new logins will fail")
        elif state in ("auth_ok", "auth_suspect"): verdict = "caution"; reasons.append(f"{state}: {oa.get('state_detail')}; tools unverified without credentials")
        else: verdict = "caution"; reasons.append(f"requires authentication; {oa.get('state_detail') or 'health of tools unverified'}")
    else:
        verdict = "safe"
        if tool and tools is not None and tool not in tools:
            verdict = "unsafe"; reasons.append(f"tool '{tool}' not in current tool list")
        if last_change and last_change["breaking"] and now - last[0]["ts"] < RECENT_BREAK_S and verdict == "safe":
            verdict = "caution"; reasons.append("breaking tool change in last 7 days; re-read schema before calling")
        if (r["latency_ms"] or 0) > SLOW_MS and verdict == "safe":
            verdict = "caution"; reasons.append(f"slow: {r['latency_ms']:.0f} ms")
    if now - r["ts"] > STALE_S and verdict == "safe":
        verdict = "caution"; reasons.append("last check older than 1h")
    oauth = json.loads(r["oauth"]) if r["oauth"] else None
    return {"server": r["server_name"], "url": r["url"], "transport": r["transport"], "tool": tool,
            "verdict": verdict, "reasons": reasons or ["up, handshake + tools/list OK, no recent breaking change"],
            "status": st, "http_status": r["http_status"],
            "latency_ms": round(r["latency_ms"], 1) if r["latency_ms"] else None,
            "protocol_version": r["protocol_version"],
            "spec_readiness": {"readiness": r["spec_readiness"], "supports_2026_07_28": bool(r["supports_2026_07_28"]) if r["supports_2026_07_28"] is not None else None,
                               "legacy_versions": json.loads(r["legacy_versions"]) if r["legacy_versions"] else None},
            "auth": None if st != "auth_required" else {"advertises_oauth": (oauth or {}).get("advertises_oauth"),
                     "resource_metadata_url": (oauth or {}).get("resource_metadata_url") or (oauth or {}).get("prm_url"),
                     "authorization_servers": (oauth or {}).get("authorization_servers"),
                     "state": (oauth or {}).get("state"), "detail": (oauth or {}).get("state_detail"),
                     "dynamic_client_registration": (oauth or {}).get("dcr"),
                     "auth_server": {k: v for k, v in ((oauth or {}).get("as_check") or {}).items()
                                     if k in ("issuer", "metadata_found", "metadata_url", "authorization_endpoint", "token_endpoint",
                                              "registration_endpoint", "pkce_s256", "cimd", "error", "token_endpoint_check",
                                              "authorization_endpoint_check", "registration_endpoint_check")} or None},
            "tool_count": r["tool_count"], "last_tool_change": last_change, "last_checked": iso(r["ts"])}

ORDER = {"safe": 0, "caution": 1, "unsafe": 2}
def is_safe_to_call(server, tool=None):
    t0 = time.perf_counter(); c = con()
    rows = find_endpoints(c, server)
    if not rows:
        return {"server": server, "tool": tool, "verdict": "unknown", "reasons": ["server not in Toolweather index"], "query_ms": round((time.perf_counter()-t0)*1000, 2)}
    reps = sorted((endpoint_report(c, r, tool) for r in rows), key=lambda x: ORDER[x["verdict"]])
    out = dict(reps[0]); out["other_endpoints"] = [{"url": x["url"], "verdict": x["verdict"], "status": x["status"]} for x in reps[1:]]
    out["query_ms"] = round((time.perf_counter() - t0) * 1000, 2); return out

# ---- alternatives (TF-IDF cosine over tool names + descriptions) ----
TOK = re.compile(r"[a-z0-9]+")
STOP = set("the a an of to and or for in on with by is are be this that from it as at your you get list use can will".split())
def toks(s):
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s or "").replace("_", " ").lower()
    return [w for w in TOK.findall(s) if w not in STOP and len(w) > 1]

_idx = {"mtime": None}
def index():
    m = os.path.getmtime(DB)
    if _idx["mtime"] == m: return _idx
    c = con(); docs = {}
    for r in c.execute(LATEST + " WHERE p.status='ok' AND p.tools_json IS NOT NULL"):
        tl = json.loads(r["tools_json"]); words = []
        for t in tl: words += toks(t["name"]) * 2 + toks(t.get("description"))[:60]
        docs[r["eid"]] = (r, Counter(words), [t["name"] for t in tl])
    df = Counter(w for _, (_, tf, _) in docs.items() for w in tf)
    N = max(len(docs), 1); idf = {w: math.log(N / (1 + d)) + 1 for w, d in df.items()}
    vecs = {}
    for eid, (r, tf, names) in docs.items():
        v = {w: (1 + math.log(n)) * idf[w] for w, n in tf.items()}; nrm = math.sqrt(sum(x*x for x in v.values())) or 1
        vecs[eid] = {w: x / nrm for w, x in v.items()}
    _idx.update(mtime=m, docs=docs, idf=idf, vecs=vecs); return _idx

def suggest_alternatives(query, limit=5):
    t0 = time.perf_counter(); ix = index(); c = con()
    src = find_endpoints(c, query) if query else []
    src = [r for r in src if r["server_name"] == query or r["url"] == query] or (src if "/" in query or "." in query else [])
    exclude = {r["server_name"] for r in src}
    if src and src[0]["eid"] in ix["vecs"]: q = ix["vecs"][src[0]["eid"]]; basis = "tools of " + src[0]["server_name"]
    else:
        tf = Counter(toks(query)); q = {w: (1 + math.log(n)) * ix["idf"].get(w, 1) for w, n in tf.items()}
        nrm = math.sqrt(sum(x*x for x in q.values())) or 1; q = {w: x/nrm for w, x in q.items()}; basis = "capability text"
    scored = []
    for eid, v in ix["vecs"].items():
        r, _, names = ix["docs"][eid]
        if r["server_name"] in exclude: continue
        s = sum(x * v.get(w, 0) for w, x in q.items())
        if s > 0.05: scored.append((s, eid))
    scored.sort(reverse=True); out, seen = [], set()
    for s, eid in scored:
        r, _, names = ix["docs"][eid]
        if r["server_name"] in seen: continue
        seen.add(r["server_name"]); rep = endpoint_report(c, r)
        if rep["verdict"] == "unsafe": continue
        qw = set(q); match = [n for n in names if qw & set(toks(n))][:5]
        out.append({"server": r["server_name"], "url": r["url"], "similarity": round(s, 3), "verdict": rep["verdict"],
                    "latency_ms": rep["latency_ms"], "matching_tools": match or names[:5], "last_checked": rep["last_checked"]})
        if len(out) >= limit: break
    return {"query": query, "basis": basis, "alternatives": out, "query_ms": round((time.perf_counter()-t0)*1000, 2)}

# ---- API keys + free daily quota ----
def kcon():
    c = sqlite3.connect(KEYS_DB, check_same_thread=False)
    c.executescript("CREATE TABLE IF NOT EXISTS keys(key TEXT PRIMARY KEY, label TEXT, daily_limit INT, created REAL);"
                    "CREATE TABLE IF NOT EXISTS usage(key TEXT, day TEXT, n INT, PRIMARY KEY(key,day));"); return c
def create_key(label="", limit=FREE_DAILY):
    k = "tw_" + secrets.token_urlsafe(24); c = kcon()
    c.execute("INSERT INTO keys VALUES(?,?,?,?)", (k, label, limit, time.time())); c.commit(); return k
def check_and_count(key):
    """Returns (ok, used, limit). Counts the call if allowed."""
    c = kcon(); row = c.execute("SELECT daily_limit FROM keys WHERE key=?", (key,)).fetchone()
    if not row: return False, 0, 0
    day = datetime.date.today().isoformat()
    n = (c.execute("SELECT n FROM usage WHERE key=? AND day=?", (key, day)).fetchone() or [0])[0]
    if n >= row[0]: return False, n, row[0]
    c.execute("INSERT INTO usage VALUES(?,?,1) ON CONFLICT(key,day) DO UPDATE SET n=n+1", (key, day)); c.commit()
    return True, n + 1, row[0]
