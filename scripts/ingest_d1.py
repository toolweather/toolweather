"""Push latest probe results (with precomputed verdicts + alternatives) from local SQLite into Cloudflare D1."""
import json, os, sys, time, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tw_core as tw, d1, accuracy
from d1 import lit

def main():
    c = tw.con()
    rows = c.execute(tw.LATEST).fetchall()
    stmts, fts = [], []
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
        stmts.append("INSERT OR REPLACE INTO servers VALUES(" + ",".join(lit(v) for v in [
            r["url"], r["server_name"], r["transport"], rep["verdict"], json.dumps(rep["reasons"]), rep["status"],
            rep["http_status"], rep["latency_ms"], rep["protocol_version"], sr["readiness"],
            None if sr["supports_2026_07_28"] is None else int(sr["supports_2026_07_28"]),
            json.dumps(sr["legacy_versions"]), json.dumps(rep["auth"]), rep["tool_count"], json.dumps(names),
            json.dumps(rep["last_tool_change"]), rep["last_checked"], json.dumps(alts)]) + ")")
        if tl:
            body = " ".join(f'{t["name"].replace("_"," ")} {(t.get("description") or "")[:200]}' for t in tl)[:4000]
            fts.append(f"INSERT INTO tools_fts(url,name,body) VALUES({lit(r['url'])},{lit(r['server_name'])},{lit(body)})")
    ch = c.execute("SELECT e.url, e.server_name, c.ts, c.severity, c.kind, c.tool, c.detail FROM changes c JOIN endpoints e ON e.id=c.endpoint_id").fetchall()
    cstmts = [f"INSERT OR IGNORE INTO changes VALUES({','.join(lit(v) for v in [x[0], x[1], tw.iso(x[2]), x[3], x[4], x[5], x[6]])})" for x in ch]
    d1.batch(stmts); d1.query("DELETE FROM tools_fts"); d1.batch(fts); d1.batch(cstmts)
    # Track record: only (re)write recent days; older days in local history may be partly pruned, so D1 keeps them frozen.
    cut = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    acc = [r for r in accuracy.compute(tw.DB) if r[0] >= cut]
    if acc: d1.batch([f"INSERT OR REPLACE INTO accuracy_daily VALUES({','.join(lit(v) for v in r)})" for r in acc])
    d1.query("INSERT OR REPLACE INTO meta VALUES('last_ingest', ?)", [datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")])
    print(f"ingested servers={len(stmts)} fts={len(fts)} changes={len(cstmts)}")
if __name__ == "__main__": main()
