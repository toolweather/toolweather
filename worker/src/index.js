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
// ---- self-serve keys: per-IP (salted daily hash, raw IP never stored) and global daily caps ----
const KEYS_PER_IP_DAY = 3, KEYS_GLOBAL_DAY = 300;
// IPv6 clients often rotate within their /64, so bucket by /64 (IPv4: full address). Only the salted daily hash is stored.
const netOf = (ip) => !ip ? "?" : ip.includes(":") ? ip.split(":").slice(0, 4).join(":") : ip;
async function newKey(env, req, source) {
  const day = new Date().toISOString().slice(0, 10);
  const log = (o) => env.DB.prepare("INSERT INTO keys_created VALUES(?,?,?,1) ON CONFLICT(day,source,outcome) DO UPDATE SET n=n+1").bind(day, source, o).run().catch(() => {});
  const ih = (await sha256(`keys|${day}|${env.SALT || "tw"}|${netOf(req.headers.get("cf-connecting-ip"))}`)).slice(0, 24);
  const g = await env.DB.prepare("SELECT coalesce(sum(n),0) c FROM keys_created WHERE day=? AND outcome='created'").bind(day).first();
  if (g.c >= KEYS_GLOBAL_DAY) { await log("global_cap"); return { code: 429, body: { error: "key_signups_paused", message: "We've hit today's limit for new free keys. Please try again tomorrow (UTC)." } }; }
  const r = await env.DB.prepare("INSERT INTO key_signups VALUES(?,?,1) ON CONFLICT(day,ih) DO UPDATE SET n=n+1 RETURNING n").bind(day, ih).first();
  if (r.n > KEYS_PER_IP_DAY) { await log("ip_cap"); return { code: 429, body: { error: "too_many_keys", message: `At most ${KEYS_PER_IP_DAY} new keys per network per day. Please reuse a key you already have, or try again tomorrow (UTC).` } }; }
  const k = "tw_" + [...crypto.getRandomValues(new Uint8Array(24))].map((x) => x.toString(16).padStart(2, "0")).join("");
  await env.DB.prepare("INSERT INTO api_keys VALUES(?,?,?,?)").bind(await sha256(k), "self:" + source, 1000, day).run();
  await log("created");
  return { code: 201, body: { api_key: k, daily_limit: 1000, watches_limit: 3, note: "Shown once: store it now. Send it as the X-API-Key header (REST or /mcp).",
    try: "curl -H 'X-API-Key: " + k + "' 'https://toolweather.toolweather.workers.dev/v1/is_safe_to_call?server=io.github.owner/name'" } };
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

// ---- fresher verdicts: inline read-only handshake (initialize only) when the stored check is >5 min old or fresh=true ----
const FRESH_S = 300, MIN_REPROBE_S = 60, LIVE_TIMEOUT_MS = 4000, LIVE_PER_KEY_DAY = 200;
const UA = "toolweather-live-check/0.1 (read-only; initialize only)";
async function liveProbe(url) {
  const t0 = Date.now(), ac = new AbortController(), tm = setTimeout(() => ac.abort(), LIVE_TIMEOUT_MS);
  try {
    const r = await fetch(url, { method: "POST", signal: ac.signal, redirect: "follow",
      headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "user-agent": UA },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "toolweather-live", version: "0.1" } } }) });
    const lat = Date.now() - t0;
    if (r.status === 401 || r.status === 403) { r.body?.cancel(); return { status: "auth_required", http_status: r.status, latency_ms: lat, error: "HTTP " + r.status }; }
    if (!r.ok) { r.body?.cancel(); return { status: "failed", http_status: r.status, latency_ms: lat, error: "HTTP " + r.status }; }
    let txt = "";
    const rd = r.body.getReader(), dec = new TextDecoder();
    while (txt.length < 16384) { const { value, done } = await rd.read(); if (done) break; txt += dec.decode(value, { stream: true }); if (/"result"\s*:|"error"\s*:/.test(txt) && /\}\s*(\n|$)/.test(txt)) break; }
    rd.cancel().catch(() => {});
    const m = txt.match(/"protocolVersion"\s*:\s*"([^"]+)"/);
    if (/"result"\s*:/.test(txt)) return { status: "ok", http_status: r.status, latency_ms: Date.now() - t0, protocol_version: m ? m[1] : null };
    return { status: "failed", http_status: r.status, latency_ms: Date.now() - t0, error: "no initialize result" };
  } catch (e) {
    return { status: "failed", http_status: null, latency_ms: Date.now() - t0, error: e.name === "AbortError" ? `timeout (${LIVE_TIMEOUT_MS / 1000}s)` : String(e.message || e).slice(0, 120) };
  } finally { clearTimeout(tm); }
}
// rep = shaped stored verdict. Returns rep with checked_at/age_seconds and, when applicable, a live result merged in.
async function freshen(env, ctx, rep, fresh, keyHash) {
  const age = (ts) => Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  rep.checked_at = rep.last_checked; rep.age_seconds = rep.last_checked ? age(rep.last_checked) : null; rep.source = "scheduled_probe";
  if (!rep.url || !/^https:/.test(rep.url) || rep.transport === "sse") { rep.live_check = "not_supported"; return rep; }
  const needed = fresh || rep.age_seconds == null || rep.age_seconds > FRESH_S;
  if (!needed) return rep;
  let lc = await env.DB.prepare("SELECT * FROM live_checks WHERE url=?").bind(rep.url).first();
  const lcAge = lc ? age(lc.ts) : Infinity;
  let src = "live_cache";
  if (!(lc && (lcAge <= MIN_REPROBE_S || (!fresh && lcAge <= FRESH_S)))) {
    if (keyHash) {
      const u = await env.DB.prepare("INSERT INTO live_usage VALUES(?,?,1) ON CONFLICT(key_hash,day) DO UPDATE SET n=n+1 RETURNING n").bind(keyHash, today()).first();
      if (u.n > LIVE_PER_KEY_DAY) { rep.live_check = `skipped: per-key limit of ${LIVE_PER_KEY_DAY} live checks/day reached; showing the newest result we have`; if (!lc) return rep; }
      else { lc = { url: rep.url, ts: new Date().toISOString(), ...(await liveProbe(rep.url)) }; src = "live_probe";
        ctx.waitUntil(env.DB.prepare("INSERT OR REPLACE INTO live_checks VALUES(?,?,?,?,?,?,?)").bind(lc.url, lc.ts, lc.status, lc.http_status ?? null, lc.latency_ms, lc.protocol_version ?? null, lc.error ?? null).run().catch(() => {})); }
    } else return rep;
  }
  if (!lc || age(lc.ts) >= rep.age_seconds) return rep;
  rep.source = src; rep.checked_at = lc.ts; rep.age_seconds = age(lc.ts);
  rep.live = { status: lc.status, http_status: lc.http_status, latency_ms: lc.latency_ms, protocol_version: lc.protocol_version || null, error: lc.error || null, checks: "initialize handshake only" };
  if (lc.status === "failed") { rep.verdict = "unsafe"; rep.reasons = [`live check failed: ${lc.error}`]; }
  else if (lc.status === "auth_required" && rep.status !== "auth_required") { rep.verdict = "caution"; rep.reasons = ["live check: server now requires authentication"]; }
  else if (lc.status === "ok" && rep.status !== "ok") { rep.verdict = "caution"; rep.reasons = ["live handshake OK (was " + rep.status + " at last full check); tool list not re-verified yet"]; }
  else if (lc.status === "ok" && rep.verdict === "caution" && rep.reasons.join().includes("older than")) { rep.verdict = "safe"; rep.reasons = ["live handshake OK; tool list from last full check"]; }
  rep.status = lc.status;
  return rep;
}

// ---- one-off authenticated check with the caller's own bearer token. The token is used for this request only:
// never stored, cached, or logged, and the result is not written to live_checks.
async function rpcPost(url, body, headers, ms) {
  const ac = new AbortController(), tm = setTimeout(() => ac.abort(), ms);
  try {
    const r = await fetch(url, { method: "POST", signal: ac.signal, headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "user-agent": UA, ...headers }, body: JSON.stringify(body) });
    const txt = r.ok ? (await r.text()).slice(0, 400000) : (r.body?.cancel(), "");
    return { r, txt };
  } finally { clearTimeout(tm); }
}
const firstJson = (txt, id) => { for (const part of [txt, ...txt.split(/\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5))]) { try { const j = JSON.parse(part); if (j.id === id) return j; } catch {} } return null; };
async function authedCheck(url, token) {
  const t0 = Date.now(), auth = { authorization: /^bearer\s/i.test(token) ? token : "Bearer " + token };
  try {
    const a = await rpcPost(url, { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "toolweather-authed-check", version: "0.1" } } }, auth, LIVE_TIMEOUT_MS);
    if (a.r.status === 401 || a.r.status === 403) return { status: "token_rejected", http_status: a.r.status, latency_ms: Date.now() - t0 };
    const init = firstJson(a.txt, 1);
    if (!a.r.ok || !init?.result) return { status: "failed", http_status: a.r.status, latency_ms: Date.now() - t0, error: a.r.ok ? "no initialize result" : "HTTP " + a.r.status };
    const h = { ...auth, "mcp-protocol-version": init.result.protocolVersion || "2025-06-18" };
    const sid = a.r.headers.get("mcp-session-id"); if (sid) h["mcp-session-id"] = sid;
    await rpcPost(url, { jsonrpc: "2.0", method: "notifications/initialized" }, h, 2000).catch(() => {});
    const t = await rpcPost(url, { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, h, LIVE_TIMEOUT_MS);
    const tl = firstJson(t.txt, 2);
    const tools = tl?.result?.tools;
    return { status: Array.isArray(tools) ? "ok" : "failed", http_status: t.r.status, latency_ms: Date.now() - t0, protocol_version: init.result.protocolVersion || null,
      tool_count: Array.isArray(tools) ? tools.length : null, tool_names: Array.isArray(tools) ? tools.slice(0, 200).map((x) => x.name) : null, error: Array.isArray(tools) ? null : "tools/list failed" };
  } catch (e) { return { status: "failed", latency_ms: Date.now() - t0, error: e.name === "AbortError" ? `timeout (${LIVE_TIMEOUT_MS / 1000}s)` : String(e.message || e).slice(0, 120) }; }
}
async function withAuthedCheck(env, rep, token, keyHash) {
  if (!token || !rep.url) return rep;
  if (!/^https:/.test(rep.url) || rep.transport === "sse") { rep.authenticated_check = { status: "not_supported" }; return rep; }
  const u = await env.DB.prepare("INSERT INTO live_usage VALUES(?,?,1) ON CONFLICT(key_hash,day) DO UPDATE SET n=n+1 RETURNING n").bind(keyHash, today()).first();
  if (u.n > LIVE_PER_KEY_DAY) { rep.authenticated_check = { status: "skipped", reason: `per-key limit of ${LIVE_PER_KEY_DAY} live checks/day reached` }; return rep; }
  const c = await authedCheck(rep.url, String(token).slice(0, 4096));
  rep.authenticated_check = { ...c, checked_at: new Date().toISOString(), note: "one-off check with your token; the token was not stored or logged" };
  if (c.status === "ok") { rep.verdict = rep.verdict === "unsafe" && rep.status === "failed" ? "caution" : rep.tool && c.tool_names && !c.tool_names.includes(rep.tool) ? "unsafe" : "safe";
    rep.reasons = rep.verdict === "unsafe" ? [`tool '${rep.tool}' not in tool list returned with your token`] : ["authenticated handshake + tools/list OK with your token just now"]; }
  else if (c.status === "token_rejected") { rep.reasons = [...rep.reasons, "your token was rejected (HTTP " + c.http_status + ")"]; }
  else { rep.verdict = "unsafe"; rep.reasons = ["authenticated check failed: " + c.error]; }
  return rep;
}
async function isSafe(env, server, tool, opt = {}) {
  if (!server) return { error: "server is required" };
  let { results } = await env.DB.prepare("SELECT * FROM servers WHERE name=? OR url=? LIMIT 10").bind(server, server).all();
  if (!results.length) ({ results } = await env.DB.prepare("SELECT * FROM servers WHERE name LIKE ? LIMIT 5").bind(`%${server}%`).all());
  if (!results.length) return { server, tool: tool || null, verdict: "unknown", reasons: ["server not in Toolweather index"] };
  const reps = results.map((r) => shape(r, tool)).sort((a, b) => ORDER[a.verdict] - ORDER[b.verdict]);
  let top = opt.ctx ? await freshen(env, opt.ctx, reps[0], !!opt.fresh, opt.keyHash) : reps[0];
  if (opt.bearer) top = await withAuthedCheck(env, top, opt.bearer, opt.keyHash);
  return { ...top, other_endpoints: reps.slice(1).map((x) => ({ url: x.url, verdict: x.verdict, status: x.status })) };
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
  { name: "is_safe_to_call", description: "Check whether an MCP server (registry name or URL), and optionally one of its tools, is safe to call right now. Returns verdict safe/caution/unsafe, live status, latency, last tool change (breaking?), spec readiness, auth info, checked_at and age_seconds. Results older than 5 minutes are re-verified with a live handshake.",
    inputSchema: { type: "object", properties: { server: { type: "string" }, tool: { type: "string" }, fresh: { type: "boolean", description: "force a live handshake now (otherwise automatic when the stored check is >5 min old)" }, bearer_token: { type: "string", description: "optional: your own token for this server, for a one-off authenticated initialize + tools/list check. Used once, never stored or logged." } }, required: ["server"] } },
  { name: "suggest_alternatives", description: "Find working MCP servers with similar tools. query = a server name/URL, or capability text like 'send email'.",
    inputSchema: { type: "object", properties: { query: { type: "string" }, limit: { type: "integer" } }, required: ["query"] } },
  { name: "get_api_key", description: "Get a free Toolweather API key instantly (1,000 calls/day, 3 watches). No key or email needed. The key is shown once; pass it as the X-API-Key header or the api_key argument.",
    inputSchema: { type: "object", properties: {} } },
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
      if (params.name === "get_api_key") {
        const r = await newKey(env, req, "mcp");
        track(env, ctx, "get_api_key", "mcp", r.code === 201 ? "ok" : "rate_limited");
        return ok({ isError: r.code !== 201, content: [{ type: "text", text: r.code === 201 ? `Your Toolweather API key (shown once): ${r.body.api_key}\n1,000 calls/day, 3 watches. Send it as the X-API-Key header.` : r.body.message }], structuredContent: r.body });
      }
      const g = await gate(env, key || params.arguments?.api_key);
      track(env, ctx, String(params.name || "?").slice(0, 40), "mcp", g.ok ? "ok" : g.code === 429 ? "rate_limited" : "unauthorized");
      trackKey(env, ctx, g, String(params.name || "?").slice(0, 40), "mcp", ["is_safe_to_call", "watch_server"].includes(params.name) ? params.arguments?.server : null);
      if (!g.ok) return ok({ isError: true, content: [{ type: "text", text: g.msg }] });
      const a = params.arguments || {};
      let r;
      if (params.name === "is_safe_to_call") r = await isSafe(env, a.server, a.tool, { ctx, fresh: a.fresh === true || a.fresh === "true", keyHash: g.h, bearer: a.bearer_token });
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
// ---- on-time probing: the Worker cron (free) starts the GitHub probe workflow every 15 min via workflow_dispatch.
// The probe itself (~2,000 servers, several requests each) does not fit Worker free limits (50 subrequests/invocation).
async function dispatchProbe(env) {
  const now = new Date().toISOString();
  const note = (v) => env.DB.prepare("INSERT OR REPLACE INTO meta VALUES('last_dispatch', ?)").bind(JSON.stringify({ at: now, ...v })).run().catch(() => {});
  if (!env.GH_DISPATCH_TOKEN) return note({ ok: false, error: "no GH_DISPATCH_TOKEN" });
  const gh = (path, init = {}) => fetch("https://api.github.com/repos/toolweather/toolweather" + path, { ...init,
    headers: { authorization: "Bearer " + env.GH_DISPATCH_TOKEN, accept: "application/vnd.github+json", "user-agent": "toolweather-cron", "x-github-api-version": "2022-11-28", ...(init.headers || {}) } });
  try {
    for (const st of ["queued", "in_progress"]) {
      const r = await gh(`/actions/workflows/probe.yml/runs?status=${st}&per_page=1`);
      if (r.ok && (await r.json()).total_count > 0) return note({ ok: true, skipped: "run already " + st });
    }
    const r = await gh("/actions/workflows/probe.yml/dispatches", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ref: "main" }) });
    return note({ ok: r.status === 204, http_status: r.status });
  } catch (e) { return note({ ok: false, error: String(e.message || e).slice(0, 200) }); }
}
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url), p = url.pathname;
    const key = req.headers.get("x-api-key") || (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "") || url.searchParams.get("key");
    if (req.method === "OPTIONS") return new Response(null, { headers: { "access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "GET,POST,DELETE,OPTIONS" } });
    if (p === "/health") {
      const m = await env.DB.prepare("SELECT v FROM meta WHERE k='last_ingest'").first();
      const d = await env.DB.prepare("SELECT v FROM meta WHERE k='last_dispatch'").first();
      return J({ ok: true, last_ingest: m && m.v, last_probe_dispatch: d ? parse(d.v) : null });
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
      const r = p.endsWith("is_safe_to_call") ? await isSafe(env, url.searchParams.get("server"), url.searchParams.get("tool"), { ctx, fresh: ["1", "true"].includes(url.searchParams.get("fresh")), keyHash: g.h, bearer: req.headers.get("x-upstream-authorization") })
        : await alternatives(env, url.searchParams.get("q"), url.searchParams.get("limit"));
      if (req.headers.get("x-upstream-authorization")) h["cache-control"] = "no-store";
      return J(r, r.error ? 400 : 200, h);
    }
    if (p === "/v1/accuracy") {
      const { results } = await env.DB.prepare("SELECT * FROM accuracy_daily ORDER BY day DESC, verdict LIMIT 300").all();
      const days = {}, tot = {};
      for (const r of results) {
        (days[r.day] ||= { day: r.day })[r.verdict] = { pairs: r.pairs, matched: r.matched, match_rate: r.pairs ? +(r.matched / r.pairs).toFixed(4) : null, avg_gap_minutes: Math.round(r.avg_gap_s / 60) };
        const t = (tot[r.verdict] ||= { pairs: 0, matched: 0 }); t.pairs += r.pairs; t.matched += r.matched;
      }
      for (const t of Object.values(tot)) t.match_rate = t.pairs ? +(t.matched / t.pairs).toFixed(4) : null;
      track(env, ctx, "accuracy", "rest", "ok");
      return J({ metric: "verdict_matched_next_probe",
        definition: "For each scheduled probe of an endpoint, did the very next scheduled probe of the same endpoint (usually ~15 min later, at most 60 min) have the same status? safe = handshake + tools/list OK, caution_auth = requires authentication, unsafe = down/broken.",
        limitations: ["Measures how stable a verdict is until the next check, not whether real tool calls succeed.", "Scored by status class only; extra caution reasons (slow, recent breaking change, stale) are not scored.", "History starts 2026-10-15 (UTC); early days have small samples."],
        days_covered: Object.keys(days).length, totals: tot, daily: Object.values(days) }, 200, { "cache-control": "max-age=600" });
    }
    if (p === "/v1/keys") {
      if (req.method !== "POST") return J({ error: "use POST /v1/keys" }, 405);
      const src = url.searchParams.get("source") === "web" ? "web" : "rest";
      const r = await newKey(env, req, src);
      track(env, ctx, "keys.create", src === "web" ? "web" : "rest", r.code === 201 ? "ok" : "rate_limited");
      return J(r.body, r.code, { "cache-control": "no-store" });
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
    if (p === "/") return J({ name: "Toolweather API", docs: "https://github.com/toolweather/toolweather", openapi: "/openapi.json", server_card: "/.well-known/mcp", get_key: "POST /v1/keys", endpoints: ["/v1/keys", "/v1/accuracy", "/v1/is_safe_to_call?server=&tool=", "/v1/suggest_alternatives?q=", "/v1/watches", "/mcp", "/health"] });
    return J({ error: "not found" }, 404);
  },
  async scheduled(ev, env, ctx) {
    ctx.waitUntil(runAlerts(env));
    if (new Date(ev.scheduledTime).getUTCMinutes() % 15 === 0) ctx.waitUntil(dispatchProbe(env));
  },
};
