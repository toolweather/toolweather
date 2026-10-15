"""Track record: how often a verdict matched the very next scheduled probe of the same endpoint.

Uses only existing probe history (mcp_health.db). Verdict classes follow the probe status:
ok -> safe, auth_required -> caution (auth), failed -> unsafe. A verdict "matched" when the next probe
(at most 60 min later) had the same status. This measures how stable/predictive a verdict is over the
next ~15 min; it does NOT test whether actual tool calls succeed.
"""
import datetime, sqlite3, os
CLS = {"ok": "safe", "auth_required": "caution_auth", "failed": "unsafe"}
MAX_GAP_S = 3600

def compute(db="mcp_health.db"):
    c = sqlite3.connect(db)
    rows = c.execute("SELECT endpoint_id, ts, status FROM probes WHERE status IN ('ok','auth_required','failed') ORDER BY endpoint_id, ts").fetchall()
    agg = {}
    for (e1, t1, s1), (e2, t2, s2) in zip(rows, rows[1:]):
        if e1 != e2 or t2 - t1 > MAX_GAP_S: continue
        day = datetime.datetime.fromtimestamp(t1, datetime.timezone.utc).date().isoformat()
        a = agg.setdefault((day, CLS[s1]), [0, 0, 0.0])
        a[0] += 1; a[1] += s1 == s2; a[2] += t2 - t1
    return [(d, v, n, m, round(g / n)) for (d, v), (n, m, g) in sorted(agg.items())]

if __name__ == "__main__":
    for r in compute(): print(r)
