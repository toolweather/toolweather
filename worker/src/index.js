// Toolweather API + MCP endpoint on Cloudflare Workers (free tier). Verdicts are precomputed at ingest.
import CARD from "./static/server-card.json";
import OPENAPI from "./static/openapi.json";
import { createWatch, listWatches, deleteWatch, runAlerts } from "./watches.js";
const STALE_MS = 3600_000 * 1.5;
const MODERN = "2026-07-28";
const J = (o, s = 200, h = {}) => new Response(JSON.stringify(o, null, 2), { status: s, headers: { "content-type": "application/json", "access-control-allow-origin": "*", ...h } });
const parse = (v) => { try { return v == null ? null : JSON.parse(v); } catch { return v; } };

async function sha256(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
async function gate(env, key) {
  if (!key) return { ok: false, code: 401, msg: "missing API key (X-API-Key header)" };
  const h = await sha256(key);
  const k = await env.DB.prepare("SELECT daily_limit FROM api_keys WHERE key_hash=?").bind(h).first();
  if (!k) return { ok: false, code: 401, msg: "invalid API key" };
  const day = new Date().toISOString().slice(0, 10);
  const u = await env.DB.prepare("INSERT INTO usage VALUES(?,?,1) ON CONFLICT(key_hash,day) DO UPDATE SET n=n+1 RETURNING n").bind(h, day).first();
  if (u.n > k.daily_limit) return { ok: false, code: 429, h, msg: `free daily limit of ${k.daily_limit} reached` };
  return { ok: true, h, limit: k.daily_limit, remaining: k.daily_limit - u.n };
}
const ORDER = { safe: 0, caution: 1, unsafe: 2 };
function shape(r, tool) {
  let verdict = r.verdict, reasons = parse(r.reasons) || [];
  const names = parse(r.tool_names) || [];
  if (tool && r.status === "ok" && !names.includes(tool)) { verdict = "unsafe"; reasons = [`tool '${tool}' not in current tool list`]; }
  if (verdict === "safe" && Date.now() - Date.parse(r.last_checked) > STALE_MS) { verdict = "caution"; reasons = ["last check older than 90 min"]; }
  const lc = parse(r.last_change);
  if (tool && lc) lc.changes = lc.changes.filter((c) => c.tool === tool);
  return { server: r.name, url: r.url, transport: r.transport, tool: tool || null, verdict, reasons, status: r.status,
    http_status: r.http_status, latency_ms: r.latency_ms, protocol_version: r.protocol_version,
    spec_readiness: { readiness: r.readiness, supports_2026_07_28: r.supports_2026_07_28 == null ? null : !!r.supports_2026_07_28, legacy_versions: parse(r.legacy_versions) },
    auth: parse(r.auth), tool_count: r.tool_count, last_tool_change: lc && (!tool || lc.changes.length) ? lc : null, last_checked: r.last_checked };
}
async function isSafe(env, server, tool) {
  if (!server) return { error: "server is required" };
  let { results } = await env.DB.prepare("SELECT * FROM servers WHERE name=? OR url=? LIMIT 10").bind(server, server).all();
  if (!results.length) ({ results } = await env.DB.prepare("SELECT * FROM servers WHERE name LIKE ? LIMIT 5").bind(`%${server}%`).all());
  if (!results.length) return { server, tool: tool || null, verdict: "unknown", reasons: ["server not in Toolweather index"] };
  const reps = results.map((r) => shape(r, tool)).sort((a, b) => ORDER[a.verdict] - ORDER[b.verdict]);
  return { ...reps[0], other_endpoints: reps.slice(1).map((x) => ({ url: x.url, verdict: x.verdict, status: x.status })) };
}
async function alternatives(env, q, limit = 5) {
  if (!q) return { error: "q is required" };
  limit = Math.min(Math.max(+limit || 5, 1), 20);
  const src = await env.DB.prepare("SELECT name, alts FROM servers WHERE (name=? OR url=?) AND alts IS NOT NULL AND alts!='[]' LIMIT 1").bind(q, q).first();
  if (src) return { query: q, basis: "tools of " + src.name, alternatives: parse(src.alts).slice(0, limit) };
  const terms = (q.toLowerCase().match(/[a-z0-9]+/g) || []).filter((w) => w.length > 1).slice(0, 8);
  if (!terms.length) return { query: q, basis: "capability text", alternatives: [] };
  const m = terms.map((t) => `"${t}"`).join(" OR ");
  const { results } = await env.DB.prepare(
    `SELECT s.name, s.url, s.verdict, s.latency_ms, s.tool_names, s.last_checked, bm25(tools_fts) score FROM tools_fts f
     JOIN servers s ON s.url=f.url WHERE tools_fts MATCH ? AND s.verdict!='unsafe' ORDER BY score LIMIT ?`).bind(m, limit * 3).all();
  const seen = new Set(), out = [];
  for (const r of results) {
    if (seen.has(r.name)) continue; seen.add(r.name);
    const names = parse(r.tool_names) || [];
    const match = names.filter((n) => terms.some((t) => n.toLowerCase().includes(t))).slice(0, 5);
    out.push({ server: r.name, url: r.url, relevance: Math.round(-r.score * 100) / 100, verdict: r.verdict, latency_ms: r.latency_ms, matching_tools: match.length ? match : names.slice(0, 5), last_checked: r.last_checked });
    if (out.length >= limit) break;
  }
  return { query: q, basis: "capability text", alternatives: out };
}

// ---- MCP (streamable HTTP, stateless; answers 2026-07-28 server/discover and legacy initialize) ----
const TOOLS = [
  { name: "is_safe_to_call", description: "Check whether an MCP server (registry name or URL), and optionally one of its tools, is safe to call right now. Returns verdict safe/caution/unsafe, live status, latency, last tool change (breaking?), spec readiness, auth info, last_checked.",
    inputSchema: { type: "object", properties: { server: { type: "string" }, tool: { type: "string" } }, required: ["server"] } },
  { name: "suggest_alternatives", description: "Find working MCP servers with similar tools. query = a server name/URL, or capability text like 'send email'.",
    inputSchema: { type: "object", properties: { query: { type: "string" }, limit: { type: "integer" } }, required: ["query"] } },
  { name: "watch_server", description: "Get an HTTPS webhook when a server's verdict changes (safe/caution/unsafe), it starts requiring auth, or its tool list has a breaking change. Returns a per-watch secret for verifying the X-Toolweather-Signature header. Free tier: 3 watches per key.",
    inputSchema: { type: "object", properties: { server: { type: "string", description: "registry name or URL" }, webhook_url: { type: "string", description: "public https URL" } }, required: ["server", "webhook_url"] } },
  { name: "list_watches", description: "List this API key's watches and their delivery health.", inputSchema: { type: "object", properties: {} } },
  { name: "unwatch_server", description: "Delete a watch by id.", inputSchema: { type: "object", properties: { id: { type: "string" } }, required: ["id"] } },
];
const INFO = { name: "toolweather", version: "0.1.0" };
async function mcp(req, env, key, ctx) {
  let msg; try { msg = await req.json(); } catch { return J({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "parse error" } }, 400); }
  if (Array.isArray(msg)) return J({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "batch not supported" } }, 400);
  const { id, method, params = {} } = msg;
  const ok = (result) => J({ jsonrpc: "2.0", id, result });
  const err = (code, message) => J({ jsonrpc: "2.0", id, error: { code, message } });
  if (id === undefined) return new Response(null, { status: 202 });
  if (method !== "tools/call") track(env, ctx, "mcp:" + (method || "?").slice(0, 40), "mcp", "ok");
  switch (method) {
    case "server/discover": return ok({ resultType: "complete", supportedVersions: [MODERN, "2025-11-25", "2025-06-18", "2025-03-26"], capabilities: { tools: {} }, _meta: { "io.modelcontextprotocol/serverInfo": INFO }, ttlMs: 3600000, cacheScope: "public" });
    case "initialize": {
      const v = ["2025-11-25", "2025-06-18", "2025-03-26"].includes(params.protocolVersion) ? params.protocolVersion : "2025-06-18";
      return ok({ protocolVersion: v, capabilities: { tools: {} }, serverInfo: INFO });
    }
    case "ping": return ok({});
    case "tools/list": return ok({ tools: TOOLS });
    case "tools/call": {
      const g = await gate(env, key || params.arguments?.api_key);
      track(env, ctx, String(params.name || "?").slice(0, 40), "mcp", g.ok ? "ok" : g.code === 429 ? "rate_limited" : "unauthorized");
      trackKey(env, ctx, g, String(params.name || "?").slice(0, 40), "mcp", ["is_safe_to_call", "watch_server"].includes(params.name) ? params.arguments?.server : null);
      if (!g.ok) return ok({ isError: true, content: [{ type: "text", text: g.msg }] });
      const a = params.arguments || {};
      let r;
      if (params.name === "is_safe_to_call") r = await isSafe(env, a.server, a.tool);
      else if (params.name === "suggest_alternatives") r = await alternatives(env, a.query, a.limit);
      else if (params.name === "watch_server") r = (await createWatch(env, g.h, a.server, a.webhook_url)).body;
      else if (params.name === "list_watches") r = (await listWatches(env, g.h)).body;
      else if (params.name === "unwatch_server") r = (await deleteWatch(env, g.h, a.id)).body;
      else return err(-32602, "unknown tool");
      if (r.error === "watch_limit_reached") track(env, ctx, "watches.limit_hit", "mcp", "limited");
      if (r.error && params.name.includes("watch")) return ok({ isError: r.error !== "watch_limit_reached", content: [{ type: "text", text: r.message || r.error }], structuredContent: r });
      return ok({ content: [{ type: "text", text: JSON.stringify(r, null, 2) }], structuredContent: r });
    }
    default: return err(-32601, "method not found");
  }
}

const today = () => new Date().toISOString().slice(0, 10);
function track(env, ctx, endpoint, channel, outcome) {
  ctx.waitUntil(env.DB.prepare("INSERT INTO api_daily VALUES(?,?,?,?,1) ON CONFLICT(day,endpoint,channel,outcome) DO UPDATE SET calls=calls+1")
    .bind(today(), endpoint, channel, outcome).run().catch(() => {}));
}
// Per-key behaviour (hashed key only, no IPs): calls per day/endpoint/channel, cap hits, servers checked.
function trackKey(env, ctx, g, endpoint, channel, server) {
  if (!g.h) return;
  const day = today(), now = new Date().toISOString(), lim = g.code === 429 ? 1 : 0;
  const st = [env.DB.prepare(`INSERT INTO key_daily VALUES(?,?,?,?,1,?,?,?) ON CONFLICT(key_hash,day,endpoint,channel)
      DO UPDATE SET calls=calls+1, limited=limited+excluded.limited, last_ts=excluded.last_ts`).bind(g.h, day, endpoint, channel, lim, now, now)];
  if (server && g.ok) st.push(env.DB.prepare("INSERT INTO key_servers VALUES(?,?,?,1) ON CONFLICT(key_hash,day,server) DO UPDATE SET n=n+1")
      .bind(g.h, day, String(server).slice(0, 200)));
  ctx.waitUntil(env.DB.batch(st).catch(() => {}));
}
async function hit(req, env, ctx, url) {
  // Cookie-less page view counter. Unique visitors = sha256(daily salt + IP + UA); raw IP/UA never stored, salt rotates daily.
  const day = today(), path = (url.searchParams.get("p") || "/").slice(0, 100);
  let ref = ""; try { ref = new URL(url.searchParams.get("r") || "").hostname; } catch {}
  if (ref.endsWith("toolweather.pages.dev")) ref = "";
  const vh = (await sha256(`${day}|${env.SALT || "tw"}|${req.headers.get("cf-connecting-ip")}|${req.headers.get("user-agent")}`)).slice(0, 16);
  ctx.waitUntil(env.DB.batch([
    env.DB.prepare("INSERT INTO page_daily VALUES(?,?,?,1) ON CONFLICT(day,path,ref) DO UPDATE SET views=views+1").bind(day, path, ref.slice(0, 80)),
    env.DB.prepare("INSERT OR IGNORE INTO page_uniques VALUES(?,?)").bind(day, vh),
  ]).catch(() => {}));
  return new Response(null, { status: 204, headers: { "access-control-allow-origin": "*", "cache-control": "no-store" } });
}
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url), p = url.pathname;
    const key = req.headers.get("x-api-key") || (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "") || url.searchParams.get("key");
    if (req.method === "OPTIONS") return new Response(null, { headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "GET,POST,DELETE,OPTIONS" } });
    if (p === "/health") {
      const m = await env.DB.prepare("SELECT v FROM meta WHERE k='last_ingest'").first();
      return J({ ok: true, last_ingest: m && m.v });
    }
    if (["/.well-known/mcp", "/.well-known/mcp.json", "/.well-known/mcp/server-card", "/mcp/server-card", "/server-card"].includes(p)) return J(CARD, 200, { "cache-control": "max-age=3600" });
    if (p === "/openapi.json") return J(OPENAPI, 200, { "cache-control": "max-age=3600" });
    if (p === "/v1/hit") return hit(req, env, ctx, url);
    if (p === "/mcp") {
      if (req.method === "GET") return new Response("method not allowed", { status: 405 });
      return mcp(req, env, key, ctx);
    }
    if (p === "/v1/is_safe_to_call" || p === "/v1/suggest_alternatives") {
      const ep = p.slice(4);
      const g = await gate(env, key);
      track(env, ctx, ep, "rest", g.ok ? "ok" : g.code === 429 ? "rate_limited" : "unauthorized");
      trackKey(env, ctx, g, ep, "rest", ep === "is_safe_to_call" ? url.searchParams.get("server") : null);
      if (!g.ok) return J({ error: g.msg }, g.code);
      const h = { "x-ratelimit-limit": String(g.limit), "x-ratelimit-remaining": String(Math.max(g.remaining, 0)) };
      const r = p.endsWith("is_safe_to_call") ? await isSafe(env, url.searchParams.get("server"), url.searchParams.get("tool"))
        : await alternatives(env, url.searchParams.get("q"), url.searchParams.get("limit"));
      return J(r, r.error ? 400 : 200, h);
    }
    if (p === "/v1/watches" || p.startsWith("/v1/watches/")) {
      const id = p.length > 12 ? decodeURIComponent(p.slice(12)) : null;
      const ep = id ? "watches.delete" : req.method === "POST" ? "watches.create" : "watches.list";
      if (!((id && req.method === "DELETE") || (!id && ["GET", "POST"].includes(req.method)))) return J({ error: "method not allowed" }, 405);
      let body = {};
      if (req.method === "POST") { try { body = await req.json(); } catch { return J({ error: "JSON body required: {server, webhook_url}" }, 400); } }
      const g = await gate(env, key);
      track(env, ctx, ep, "rest", g.ok ? "ok" : g.code === 429 ? "rate_limited" : "unauthorized");
      trackKey(env, ctx, g, ep, "rest", ep === "watches.create" ? body.server : null);
      if (!g.ok) return J({ error: g.msg }, g.code);
      const r = id ? await deleteWatch(env, g.h, id) : req.method === "POST" ? await createWatch(env, g.h, body.server, body.webhook_url) : await listWatches(env, g.h);
      if (r.body.error === "watch_limit_reached") track(env, ctx, "watches.limit_hit", "rest", "limited");
      return J(r.body, r.code);
    }
    if (p.startsWith("/badge/")) {
      const name = decodeURIComponent(p.slice(7).replace(/\.svg$/, ""));
      const r = await env.DB.prepare("SELECT verdict, status, latency_ms FROM servers WHERE name=? OR url=? ORDER BY CASE verdict WHEN 'safe' THEN 0 WHEN 'caution' THEN 1 ELSE 2 END LIMIT 1").bind(name, name).first();
      const [txt, col] = !r ? ["unknown", "#6b7280"] : r.status === "ok" ? [`${r.verdict === "safe" ? "healthy" : r.verdict} · ${Math.round(r.latency_ms)}ms`, r.verdict === "safe" ? "#3f8f8a" : "#b7791f"]
        : r.status === "auth_required" ? ["auth required", "#5b84b1"] : ["down", "#a2463a"];
      const lw = 78, rw = 10 + txt.length * 6.6;
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${lw + rw}" height="20" role="img" aria-label="toolweather: ${txt}"><rect width="${lw}" height="20" rx="3" fill="#4b5563"/><rect x="${lw}" width="${rw}" height="20" rx="3" fill="${col}"/><rect x="${lw - 3}" width="6" height="20" fill="${col}"/><g fill="#fff" font-family="Verdana,sans-serif" font-size="11"><text x="8" y="14">toolweather</text><text x="${lw + 6}" y="14">${txt}</text></g></svg>`;
      return new Response(svg, { headers: { "content-type": "image/svg+xml", "cache-control": "max-age=300" } });
    }
    if (p === "/") return J({ name: "Toolweather API", docs: "https://github.com/toolweather/toolweather", openapi: "/openapi.json", server_card: "/.well-known/mcp", endpoints: ["/v1/is_safe_to_call?server=&tool=", "/v1/suggest_alternatives?q=", "/v1/watches", "/mcp", "/health"] });
    return J({ error: "not found" }, 404);
  },
  async scheduled(ev, env, ctx) { ctx.waitUntil(runAlerts(env)); },
};
