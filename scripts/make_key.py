"""Issue a Toolweather API key (stored hashed in D1). Usage: python scripts/make_key.py "label" [daily_limit]"""
import hashlib, secrets, sys, datetime, os
sys.path.insert(0, os.path.dirname(__file__)); import d1
label = sys.argv[1] if len(sys.argv) > 1 else ""; lim = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
k = "tw_" + secrets.token_urlsafe(24)
d1.query("INSERT INTO api_keys VALUES(?,?,?,?)", [hashlib.sha256(k.encode()).hexdigest(), label, lim, datetime.date.today().isoformat()])
print(k)
