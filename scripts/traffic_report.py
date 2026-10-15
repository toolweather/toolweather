"""Toolweather traffic report: page visits + API usage for the last 7 and 30 days.
Env: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, D1_DATABASE_ID.
Sources: D1 tables written by the Worker (page_daily/page_uniques/api_daily/usage), plus Cloudflare
GraphQL Analytics (Workers invocations) as a cross-check when the token allows it."""
import datetime, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(__file__)); import d1

def rows(sql, p=None): return d1.query(sql, p)[0]["results"]

def graphql_worker_requests(since):
    q = """query($a:String!,$s:Date!){viewer{accounts(filter:{accountTag:$a}){
      workersInvocationsAdaptive(limit:100,filter:{scriptName:"toolweather",date_geq:$s}){sum{requests errors} dimensions{date}}}}}"""
    req = urllib.request.Request("https://api.cloudflare.com/client/v4/graphql",
        data=json.dumps({"query": q, "variables": {"a": os.environ["CLOUDFLARE_ACCOUNT_ID"], "s": since}}).encode(),
        headers={"Authorization": "Bearer " + os.environ["CLOUDFLARE_API_TOKEN"], "Content-Type": "application/json"})
    try:
        j = json.load(urllib.request.urlopen(req, timeout=30))
        if j.get("errors"): return None, j["errors"][0].get("message")
        g = j["data"]["viewer"]["accounts"][0]["workersInvocationsAdaptive"]
        return sum(x["sum"]["requests"] for x in g), None
    except Exception as e: return None, str(e)

def report(days):
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    pv = rows("SELECT coalesce(sum(views),0) v FROM page_daily WHERE day>=?", [since])[0]["v"]
    uv = rows("SELECT count(*) n FROM page_uniques WHERE day>=?", [since])[0]["n"]
    refs = rows("SELECT ref, sum(views) v FROM page_daily WHERE day>=? AND ref!='' GROUP BY ref ORDER BY v DESC LIMIT 5", [since])
    api = rows("SELECT channel, endpoint, outcome, sum(calls) c FROM api_daily WHERE day>=? GROUP BY 1,2,3 ORDER BY c DESC", [since])
    keys = rows("SELECT count(DISTINCT key_hash) n, coalesce(sum(n),0) c FROM usage WHERE day>=?", [since])[0]
    gq, err = graphql_worker_requests(since)
    print(f"\n=== Last {days} days (since {since}, UTC) ===")
    print(f"Page views: {pv}   unique visitors (sum of daily uniques): {uv}")
    if refs: print("Top referrers: " + ", ".join(f"{r['ref']} ({r['v']})" for r in refs))
    tot = {}
    for r in api: tot[r["channel"]] = tot.get(r["channel"], 0) + r["c"]
    print(f"API calls: REST {tot.get('rest',0)}   MCP {tot.get('mcp',0)}   unique API keys with successful calls: {keys['n']} ({keys['c']} metered calls)")
    for r in api: print(f"  {r['channel']:4} {r['endpoint']:28} {r['outcome']:12} {r['c']}")
    print("Worker requests (Cloudflare GraphQL):", gq if gq is not None else f"unavailable ({err})")

def key_signals():
    """Paid-tier readiness signals per hashed key (first 10 hex chars shown)."""
    today = datetime.date.today()
    d7, d14, d30 = [(today - datetime.timedelta(days=n - 1)).isoformat() for n in (7, 14, 30)]
    lims = {r["key_hash"]: (r["label"], r["daily_limit"]) for r in rows("SELECT key_hash,label,daily_limit FROM api_keys")}
    ks = rows("""SELECT key_hash, min(day) first, max(day) last, max(last_ts) last_ts,
        sum(CASE WHEN day>=? THEN calls END) c7, sum(CASE WHEN day>=? AND day<? THEN calls END) cprev7,
        count(DISTINCT CASE WHEN day>=? THEN day END) active7, sum(CASE WHEN day>=? THEN limited END) capped30,
        sum(CASE WHEN day>=? AND endpoint='suggest_alternatives' THEN calls END) alt7, sum(CASE WHEN day>=? AND channel='mcp' THEN calls END) mcp7
        FROM key_daily WHERE channel!='webhook' GROUP BY key_hash ORDER BY c7 DESC""", [d7, d14, d7, d7, d30, d7, d7])
    peak = {r["key_hash"]: r["p"] for r in rows("SELECT key_hash, max(c) p FROM (SELECT key_hash, day, sum(calls) c FROM key_daily WHERE day>=? AND channel!='webhook' GROUP BY 1,2) GROUP BY 1", [d30])}
    srv = {}
    for r in rows("SELECT key_hash, count(DISTINCT server) n FROM key_servers WHERE day>=? GROUP BY 1", [d7]): srv[r["key_hash"]] = r["n"]
    top = rows("SELECT server, sum(n) n, count(DISTINCT key_hash) k FROM key_servers WHERE day>=? GROUP BY 1 ORDER BY n DESC LIMIT 5", [d30])
    print("\n=== API key behaviour (paid-tier signals) ===")
    print(f"{'key':10} {'label':14} {'first':10} {'last':10} {'7d':>6} {'prev7d':>6} {'trend':>6} {'act/7':>5} {'peak%':>5} {'cap30':>5} {'alt%':>4} {'mcp%':>4} {'srv7':>4}  flags")
    for r in ks:
        lab, lim = lims.get(r["key_hash"], ("?", 1000)); c7, p7 = r["c7"] or 0, r["cprev7"] or 0
        trend = "new" if p7 == 0 and c7 else (f"{(c7 - p7) / p7 * 100:+.0f}%" if p7 else "-")
        pk = 100 * (peak.get(r["key_hash"]) or 0) / (lim or 1)
        flags = []
        if (r["capped30"] or 0) > 0: flags.append("HIT CAP")
        elif pk >= 70: flags.append("near cap")
        if p7 and c7 >= 1.5 * p7: flags.append("rising")
        if r["last"] < (today - datetime.timedelta(days=14)).isoformat(): flags.append("churned")
        elif r["last"] < d7: flags.append("dormant")
        if (r["active7"] or 0) >= 5: flags.append("habitual")
        print(f"{r['key_hash'][:10]} {(lab or '')[:14]:14} {r['first']} {r['last']} {c7:>6} {p7:>6} {trend:>6} {r['active7'] or 0:>5} {pk:>5.0f} {r['capped30'] or 0:>5} "
              f"{100*(r['alt7'] or 0)/(c7 or 1):>4.0f} {100*(r['mcp7'] or 0)/(c7 or 1):>4.0f} {srv.get(r['key_hash'],0):>4}  {', '.join(flags)}")
    if not ks: print("(no keyed traffic yet)")
    if top: print("Most-checked servers (30d): " + ", ".join(f"{t['server']} ({t['n']} calls, {t['k']} keys)" for t in top))
    print("Legend: trend = last 7d vs previous 7d; act/7 = active days in last 7; peak% = busiest day vs daily limit (30d);"
          " cap30 = calls rejected at cap (30d); churned = no calls in 14d; dormant = none in 7d.")

def key_signups():
    """Self-serve key creation by source (web / rest / mcp) and how often the per-network or global caps fired."""
    d30 = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    print("\n=== Self-serve keys (30d) ===")
    rs = rows("SELECT source, outcome, sum(n) n FROM keys_created WHERE day>=? GROUP BY 1,2 ORDER BY 1,2", [d30])
    print(", ".join(f"{r['source']}/{r['outcome']}: {r['n']}" for r in rs) or "none")

def watch_signals():
    """Change-alert usage: watches per key (free limit 3), limit hits, events and webhook delivery health."""
    d30 = (datetime.date.today() - datetime.timedelta(days=29)).isoformat()
    print("\n=== Change alerts (watches) ===")
    w = rows("SELECT count(*) n, count(DISTINCT key_hash) k, sum(active) a FROM watches")[0]
    full = rows("SELECT count(*) n FROM (SELECT key_hash FROM watches WHERE active=1 GROUP BY 1 HAVING count(*)>=3)")[0]["n"]
    print(f"Watches: {w['n'] or 0} ({w['a'] or 0} active) across {w['k'] or 0} keys; keys at the 3-watch free limit: {full}")
    lim = rows("SELECT coalesce(sum(calls),0) c FROM api_daily WHERE day>=? AND endpoint='watches.limit_hit'", [d30])[0]["c"]
    print(f"Watch-limit hits (30d, REST+MCP): {lim}  <- strongest signal of demand for a paid plan")
    for r in rows("SELECT endpoint, sum(calls) c, count(DISTINCT key_hash) k FROM key_daily WHERE day>=? AND (channel='webhook' OR endpoint IN ('watches.create','watches.list','watches.delete','watch_server','list_watches','unwatch_server')) GROUP BY 1 ORDER BY 1", [d30]):
        print(f"  {r['endpoint']:34} {r['c']:>6} calls/events  {r['k']} keys")
    top = rows("SELECT server, count(*) n FROM watches WHERE active=1 GROUP BY 1 ORDER BY n DESC LIMIT 5")
    if top: print("Most-watched servers: " + ", ".join(f"{t['server']} ({t['n']})" for t in top))
    dis = rows("SELECT count(*) n FROM watches WHERE active=0")[0]["n"]
    if dis: print(f"Watches disabled after failed deliveries: {dis}")

if __name__ == "__main__":
    for d in (7, 30): report(d)
    key_signals()
    key_signups()
    watch_signals()
