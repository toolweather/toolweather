"""One-off: re-check latest modern_only probes with a legacy initialize and relabel dual where it succeeds."""
import asyncio, json, sqlite3, httpx, mcphealth as m
async def main():
    c = sqlite3.connect(m.DB)
    rows = c.execute("""SELECT p.id, e.url FROM probes p JOIN endpoints e ON e.id=p.endpoint_id
      WHERE p.spec_readiness='modern_only' AND p.id=(SELECT max(id) FROM probes WHERE endpoint_id=e.id)""").fetchall()
    sem, hl = asyncio.Semaphore(32), m.HostLimiter(2.0)
    async with httpx.AsyncClient(headers={"User-Agent": m.UA}, timeout=12, follow_redirects=True) as cl:
        async def one(pid, url):
            from urllib.parse import urlparse
            host = urlparse(url).netloc
            async with sem:
                lk = await hl.acquire(host)
                try: return pid, await asyncio.wait_for(m.probe_legacy_init(cl, url), 15)
                except Exception: return pid, None
                finally: hl.release(host, lk)
        res = await asyncio.gather(*(one(*r) for r in rows))
    n = 0
    for pid, lv in res:
        if lv:
            c.execute("UPDATE probes SET spec_readiness='dual', legacy_versions=? WHERE id=?", (json.dumps([lv]), pid)); n += 1
    c.commit(); print(f"checked {len(rows)}, relabelled dual {n}, truly modern_only {len(rows)-n}")
asyncio.run(main())
