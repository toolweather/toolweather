"""Push latest probe results (with precomputed verdicts + alternatives) from local SQLite into Cloudflare D1."""
import json, os, sys, time, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tw_core as tw, d1, accuracy
from d1 import lit

COLS = ["url", "name", "transport", "verdict", "reasons", "status", "http_status", "latency_ms", "protocol_version",
        "readiness", "supports_2026_07_28", "legacy_versions", "auth", "tool_count", "tool_names", "last_change",
        "last_checked", "alts"]
# D1 free plan allows 100k rows written/day. Rewriting all ~2.3k servers + the whole FTS index every 15 min
# cost ~12k rows per ingest (~1.2M/day), so we only write rows whose meaning changed, plus a freshness
# refresh of last_checked once it is >= REFRESH_S old (the Worker downgrades "safe" after 90 min).
# 75 min keeps ~15 min of slack before that downgrade while cutting refresh writes vs 50 min (~39k/day vs ~58k/day at ~2k servers).
REFRESH_S = 75 * 60

def _sig(row):
    """Comparison key that ignores probe-to-probe noise (timestamps, latency jitter)."""
    d = dict(zip(COLS, row))
    try: alts = [(a.get("server"), a.get("url"), a.get("verdict")) for a in json.loads(d["alts"] or "[]")]
    except Exception: alts = d["alts"]
    return tuple(v for k, v in d.items() if k not in ("last_checked", "latency_ms", "alts")) + (json.dumps(alts),)

def _ts(s):
    try: return datetime.datetime.fromisoformat(s).timestamp()
    except Exception: return 0

def main():
    c = tw.con()
    rows = c.execute(tw.LATEST).fetchall()
    cur = {r["url"]: tuple(r[k] for k in COLS) for r in d1.query("SELECT * FROM servers")[0]["results"]}
    cur_fts = {r["url"]: (r["name"], r["body"]) for r in d1.query("SELECT url, name, body FROM tools_fts")[0]["results"]}
    stmts, new_fts, now = [], {}, time.time()
    n_changed = n_fresh = n_new = 0
    tw.index()
    for r in rows:
        rep = tw.endpoint_report(c, r)
        alts = []
        if r["status"] == "ok":
            try: alts = [{k: a[k] for k in ("server", "url", "similarity", "verdict", "latency_ms", "matching_tools")}
                         for a in tw.suggest_alternatives(r["server_name"], 5)["alternatives"]]
            except Exception: alts = []
        tl = json.loads(r["tools_json"]) if r["tools_json"] else []
        names = [t["name"] for t in tl]
        sr = rep["spec_readiness"]
        vals = (r["url"], r["server_name"], r["transport"], rep["verdict"], json.dumps(rep["reasons"]), rep["status"],
            rep["http_status"], rep["latency_ms"], rep["protocol_version"], sr["readiness"],
            None if sr["supports_2026_07_28"] is None else int(sr["supports_2026_07_28"]),
            json.dumps(sr["legacy_versions"]), json.dumps(rep["auth"]), rep["tool_count"], json.dumps(names),
            json.dumps(rep["last_tool_change"]), rep["last_checked"], json.dumps(alts))
        old = cur.get(r["url"])
        if old is None:
            n_new += 1; stmts.append("INSERT OR REPLACE INTO servers VALUES(" + ",".join(lit(v) for v in vals) + ")")
        else:
            changed = _sig(old) != _sig(vals)
            due = _ts(vals[16]) > _ts(old[16]) and now - _ts(old[16]) >= REFRESH_S
            if changed or due:
                if changed: n_changed += 1
                else: n_fresh += 1
                if old[1] != vals[1]:   # name is indexed; only then pay for a full replace
                    stmts.append("INSERT OR REPLACE INTO servers VALUES(" + ",".join(lit(v) for v in vals) + ")")
                else:
                    sets = ",".join(f"{k}={lit(v)}" for k, v in zip(COLS, vals) if k not in ("url", "name"))
                    stmts.append(f"UPDATE servers SET {sets} WHERE url={lit(r['url'])}")
        if tl:
            new_fts[r["url"]] = (r["server_name"], " ".join(f'{t["name"].replace("_"," ")} {(t.get("description") or "")[:200]}' for t in tl)[:4000])
    stale = [u for u in cur_fts if new_fts.get(u) != cur_fts[u]]
    fts = [f"DELETE FROM tools_fts WHERE url IN ({','.join(lit(u) for u in stale[i:i + 200])})" for i in range(0, len(stale), 200)]
    fts += [f"INSERT INTO tools_fts(url,name,body) VALUES({lit(u)},{lit(v[0])},{lit(v[1])})" for u, v in new_fts.items() if cur_fts.get(u) != v]
    ch = c.execute("SELECT e.url, e.server_name, c.ts, c.severity, c.kind, c.tool, c.detail FROM changes c JOIN endpoints e ON e.id=c.endpoint_id").fetchall()
    cstmts = [f"INSERT OR IGNORE INTO changes VALUES({','.join(lit(v) for v in [x[0], x[1], tw.iso(x[2]), x[3], x[4], x[5], x[6]])})" for x in ch]
    d1.batch(stmts); d1.batch(fts); d1.batch(cstmts)
    # Track record: only (re)write recent days; older days in local history may be partly pruned, so D1 keeps them frozen.
    cut = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    acc = [r for r in accuracy.compute(tw.DB) if r[0] >= cut]
    if acc: d1.batch([f"INSERT OR REPLACE INTO accuracy_daily VALUES({','.join(lit(v) for v in r)})" for r in acc])
    d1.query("INSERT OR REPLACE INTO meta VALUES('last_ingest', ?)", [datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")])
    print(f"ingested servers={len(rows)} written={len(stmts)} (new={n_new} changed={n_changed} refreshed={n_fresh}) "
          f"fts_stmts={len(fts)} changes={len(cstmts)}")
if __name__ == "__main__": main()
