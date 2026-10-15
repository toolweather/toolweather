"""Minimal Cloudflare D1 HTTP API client. Env: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, D1_DATABASE_ID."""
import json, os, urllib.request
def query(sql, params=None):
    a, d, t = os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["D1_DATABASE_ID"], os.environ["CLOUDFLARE_API_TOKEN"]
    body = {"sql": sql}
    if params is not None: body["params"] = params
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{a}/d1/database/{d}/query",
        data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"})
    try:
        j = json.load(urllib.request.urlopen(req, timeout=120))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"D1 error {e.code}: {e.read()[:500]!r}")
    if not j.get("success"): raise RuntimeError(f"D1 error: {j.get('errors')}")
    return j["result"]
def lit(v):
    if v is None: return "NULL"
    if isinstance(v, bool): return str(int(v))
    if isinstance(v, (int, float)): return repr(v)
    return "'" + str(v).replace("'", "''") + "'"
def batch(stmts, max_bytes=90_000):
    buf, size = [], 0
    for s in stmts:
        if buf and size + len(s) > max_bytes: query(";\n".join(buf)); buf, size = [], 0
        buf.append(s); size += len(s) + 2
    if buf: query(";\n".join(buf))
