"""Toolweather HTTP API. Run: .venv/bin/uvicorn api:app --host 127.0.0.1 --port 8787"""
from fastapi import FastAPI, Header, HTTPException, Query, Response
import tw_core as tw

app = FastAPI(title="Toolweather", version="0.1",
              description="Ask before you call: is this MCP server/tool safe to call right now?")

def gate(key, resp):
    if not key: raise HTTPException(401, "missing X-API-Key")
    ok, used, lim = tw.check_and_count(key)
    if lim == 0: raise HTTPException(401, "invalid API key")
    resp.headers["X-RateLimit-Limit"] = str(lim); resp.headers["X-RateLimit-Remaining"] = str(max(lim - used, 0))
    if not ok: raise HTTPException(429, f"free daily limit of {lim} reached")

@app.get("/v1/is_safe_to_call")
def is_safe(response: Response, server: str, tool: str | None = None, x_api_key: str | None = Header(None)):
    gate(x_api_key, response); return tw.is_safe_to_call(server, tool)

@app.get("/v1/suggest_alternatives")
def alts(response: Response, q: str = Query(..., description="server name/url or capability text"),
         limit: int = Query(5, le=20), x_api_key: str | None = Header(None)):
    gate(x_api_key, response); return tw.suggest_alternatives(q, limit)

@app.get("/health")
def health(): return {"ok": True}
