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

if __name__ == "__main__":
    for d in (7, 30): report(d)
