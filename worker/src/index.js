import { withSources } from "./sources.js";
// Toolweather API + MCP endpoint on Cloudflare Workers (free tier). Verdicts are precomputed at ingest.
import CARD from "./static/server-card.json";
import OPENAPI from "./static/openapi.json";
import { createWatch, listWatches, deleteWatch, runAlerts, freeWatchCount, FREE_WATCHES } from "./watches.js";
import { PRICES, BATCH_MAX, PAY_INFO, payHttp, payMcp, paymentHeader, FREE_KEY_DAILY, FREE_ANON_DAILY } from "./pay.js";
const STALE_MS = 3600_000 * 1.5;
const MODERN = "2026-07-28";
const J = (o, s = 200, h = {}) => new Response(JSON.stringify(o, null, 2), { status: s, headers: { "content-type": "application/json", "access-control-allow-origin": "*", ...h } });
const parse = (v) => { try { return v == null ? null : JSON.parse(v); } catch { return v; } };

async function sha256(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
async function ensureAnon(env) {
  await env.DB.prepare("CREATE TABLE IF NOT EXISTS anon(ip_hash TEXT, day TEXT, used INT, PRIMARY KEY(ip_hash, day))").run().catch(() => {});
}
async function gate(env, key, count = true, req = null) {
  const day = new Date().toISOString().slice(0, 10);
  if (key) {
    const h = await sha256(key);
    const k = await env.DB.prepare("SELECT daily_limit FROM api_keys WHERE key_hash=?").bind(h).first();
    if (!k) return { ok: false, code: 401, msg: "invalid API key" };
    if (!count) return { ok: true, h, plan: "free_key", limit: k.daily_limit, remaining: null };
    const u = await env.DB.prepare("INSERT INTO usage VALUES(?,?,?) ON CONFLICT(key_hash,day) DO UPDATE SET n=n+excluded.n RETURNING n").bind(h, day, count === true ? 1 : count).first();
    if (u.n > k.daily_limit) return { ok: false, code: 429, over: true, h, plan: "free_key", limit: k.daily_limit, remaining: 0, msg: `Free daily quota of ${k.daily_limit} calls used up (resets 00:00 UTC). Pay ${PRICES.check} USDC on Base via x402, or wait for reset.` };
    return { ok: true, h, plan: "free_key", limit: k.daily_limit, remaining: k.daily_limit - u.n };
  }
  // Anonymous: enough free calls for one-shot agent trials; no key, no signup.
  if (!req) return { ok: false, code: 401, msg: `missing API key (X-API-Key). Free options: call without a key (${FREE_ANON_DAILY}/day anonymous), POST /v1/keys (${FREE_KEY_DAILY}/day), or pay per call on /x402/* (see /.well-known/x402).` };
  await ensureAnon(env);
  const lim = FREE_ANON_DAILY;
  const ih = (await sha256(`anon|${day}|${env.SALT || "tw"}|${netOf(req.headers.get("cf-connecting-ip"))}`)).slice(0, 24);
  if (!count) return { ok: true, h: "anon:" + ih, plan: "anonymous", limit: lim, remaining: null };
  const n = count === true ? 1 : count;
  const u = await env.DB.prepare("INSERT INTO anon(ip_hash,day,used) VALUES(?,?,?) ON CONFLICT(ip_hash,day) DO UPDATE SET used=used+excluded.used RETURNING used").bind(ih, day, n).first();
  if (u.used > lim) return { ok: false, code: 429, over: true, h: "anon:" + ih, plan: "anonymous", limit: lim, remaining: 0, msg: `Anonymous free quota of ${lim} calls/day used up (resets 00:00 UTC). Get a free key (POST /v1/keys, ${FREE_KEY_DAILY}/day) or pay ${PRICES.check} USDC on Base via x402.` };
  return { ok: true, h: "anon:" + ih, plan: "anonymous", limit: lim, remaining: lim - u.used };
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
  await env.DB.prepare("INSERT INTO api_keys VALUES(?,?,?,?)").bind(await sha256(k), "self:" + source, FREE_KEY_DAILY, day).run();
  await log("created");
  return { code: 201, body: { api_key: k, daily_limit: FREE_KEY_DAILY, watches_limit: 3, anonymous_daily: FREE_ANON_DAILY, note: "Shown once: store it now. Send it as the X-API-Key header (REST or /mcp).",
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
async function freshen(env, ctx, rep, fresh, keyHash, paid) {
  const age = (ts) => Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
  rep.checked_at = rep.last_checked; rep.age_seconds = rep.last_checked ? age(rep.last_checked) : null; rep.source = "scheduled_probe";
  if (!rep.url || !/^https:/.test(rep.url) || rep.transport === "sse") { rep.live_check = "not_supported"; return rep; }
  const needed = fresh || rep.age_seconds == null || rep.age_seconds > FRESH_S;
  if (!needed) return rep;
  let lc = await env.DB.prepare("SELECT * FROM live_checks WHERE url=?").bind(rep.url).first();
  const lcAge = lc ? age(lc.ts) : Infinity;
  let src = "live_cache";
  if (!(lc && (lcAge <= MIN_REPROBE_S || (!fresh && lcAge <= FRESH_S)))) {
    if (keyHash || paid) {
      const u = paid ? { n: 0 } : await env.DB.prepare("INSERT INTO live_usage VALUES(?,?,1) ON CONFLICT(key_hash,day) DO UPDATE SET n=n+1 RETURNING n").bind(keyHash, today()).first();
      if (u.n > LIVE_PER_KEY_DAY) { rep.live_check = `skipped: free limit of ${LIVE_PER_KEY_DAY} live checks/day reached; showing the newest result we have. For a guaranteed live recheck use GET /x402/live_check?server=... (${PRICES.live} via x402)`; if (!lc) return rep; }
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
async function withAuthedCheck(env, rep, token, keyHash, paid) {
  if (!token || !rep.url) return rep;
  if (!/^https:/.test(rep.url) || rep.transport === "sse") { rep.authenticated_check = { status: "not_supported" }; return rep; }
  const u = paid ? { n: 0 } : await env.DB.prepare("INSERT INTO live_usage VALUES(?,?,1) ON CONFLICT(key_hash,day) DO UPDATE SET n=n+1 RETURNING n").bind(keyHash, today()).first();
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
  let top = opt.ctx ? await freshen(env, opt.ctx, reps[0], !!opt.fresh, opt.keyHash, opt.paidLive) : reps[0];
  if (opt.bearer && (opt.keyHash || opt.paidLive)) top = await withAuthedCheck(env, top, opt.bearer, opt.keyHash, opt.paidLive);
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

async function batchCheck(env, servers) {
  if (!Array.isArray(servers) || !servers.length) return { error: "servers (array of registry names or URLs) is required" };
  if (servers.length > BATCH_MAX) return { error: `at most ${BATCH_MAX} servers per batch` };
  const results = await Promise.all(servers.map(async (s) => decorate(await isSafe(env, String(s).slice(0, 300)))));
  const n = (v) => results.filter((r) => r.verdict === v).length;
  return { summary: `${results.length} servers: ${n("safe")} safe, ${n("caution")} caution, ${n("unsafe")} unsafe, ${n("unknown")} unknown (stored verdicts, no live recheck).`, checked_at: new Date().toISOString(), results };
}
const quotaInfo = (g, price) => ({ plan: g.plan || "free_key", free_limit: g.limit, free_remaining: Math.max(g.remaining ?? 0, 0), resets: "00:00 UTC", price_after_free_usd: price, asset: "USDC", network: "eip155:8453", how_to_pay: "x402 v2 exact, USDC on Base; see /.well-known/x402" });
const quotaHeaders = (g, price) => ({ "x-ratelimit-limit": String(g.limit), "x-ratelimit-remaining": String(Math.max(g.remaining ?? 0, 0)), "x-free-remaining": String(Math.max(g.remaining ?? 0, 0)), "x-price-after-free": price + " USDC (x402, Base)" });
const BATCH_TOOL = { name: "batch_check_mcp_servers", title: "Check up to 25 MCP servers at once", description: `Read stored health verdicts for up to ${BATCH_MAX} MCP servers you already intend to call. Use at startup to vet that list. Do not use it for a live handshake or auth detail (check_mcp_server_health) or to discover servers by capability (find_alternative_mcp_servers). Does not contact the remotes and never spends a live-check credit. Each name still counts as one Toolweather quota call; over quota the batch is one x402 charge (${PRICES.batch}). Returns summary, checked_at, and results[] with the same verdict fields as a single check. Unknown names come back verdict unknown.`,
  inputSchema: { type: "object", properties: { servers: { type: "array", items: { type: "string" }, maxItems: BATCH_MAX, description: "Registry names or MCP endpoint URLs already chosen, at most 25. Not a capability search. Each entry counts as one daily Toolweather call. Unknown names return verdict unknown." } }, required: ["servers"] } };

// ---- MCP (streamable HTTP, stateless; answers 2026-07-28 server/discover and legacy initialize) ----
// openWorldHint is true when the call may touch an arbitrary host: a live MCP handshake, a caller webhook, a merchant URL passed to score_spend, or a payee passed to check_merchant. Index and catalog reads are closed.
const READ_OPEN = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true };
const READ_CLOSED = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const SRV = { type: "string", description: "Registry name (io.github.owner/name) or that server's MCP endpoint URL. Partial names match the Toolweather index. A product homepage usually will not match.", examples: ["com.ufcalendar/fight-api", "https://mcp.example.com/mcp"] };
const UP_PRICES = { status: "$0.003", changes: "$0.01", model: "$0.003" };
const UP_TOOLS = [
  { name: "check_api_status", title: "Is this API / provider down right now?",
    description: `Read whether one tracked AI or developer API provider is operational, degraded, or down. Use before calling that provider, or right after 5xx, 429, or timeouts. Do not use it for an MCP server (check_mcp_server_health) or a model id (check_model_deprecation); ids come from list_providers, not URLs. Does not invoke the provider's API. Returns summary, verdict, recommended_action, open incidents, affected components, checked_at, and freshness (about every 4 minutes). Uses Upstream's quota, then ${UP_PRICES.status} via x402, not the Toolweather check quota. Also callable as get_status.`,
    inputSchema: { type: "object", properties: { provider: { type: "string", description: "Lowercase id from list_providers, such as openai, anthropic, google-ai, github, or stripe. Not a URL and not an MCP registry name.", examples: ["openai", "anthropic", "github"] } }, required: ["provider"] }, annotations: { title: "Is this API down?", ...READ_CLOSED } },
  { name: "check_model_deprecation", title: "Is this model deprecated, and what replaces it?",
    description: `Read whether one LLM model id is active, deprecated, or retired, plus its shutdown date and the provider's replacement. Use before hard-coding a model id, or when a call fails as model-not-found. Do not use it to see if the provider is down (check_api_status) or to list many retirements (list_recent_api_changes). Prefer model; id is only the legacy argument name. Returns summary, verdict, recommended_action (keep_using|plan_migration|switch_now|verify_with_provider), replacement, shutdown_date, evidence, and freshness. Upstream quota, then ${UP_PRICES.model} via x402. Also callable as get_model.`,
    inputSchema: { type: "object", properties: { model: { type: "string", description: "Model id exactly as the provider API expects, such as gpt-4o or claude-3-opus-20240229. Not a provider name; that is check_api_status.", examples: ["gpt-4-0613", "claude-3-opus-20240229"] }, id: { type: "string", description: "Legacy name for the same model id. Prefer model. Not a provider id." } } }, annotations: { title: "Is this model deprecated?", ...READ_CLOSED } },
  { name: "list_recent_api_changes", title: "What changed recently in AI/dev APIs?",
    description: `List recent outages, deprecation-page edits, model retirements, and pricing-page edits across tracked AI and developer APIs. Use to see what changed in a window, or to explain a sudden integration error. Do not use it for one provider's live status (check_api_status) or for the once-a-day narrative (get_digest). Returns summary plus items with timestamps and source links. Upstream quota, then ${UP_PRICES.changes} via x402. Also callable as get_changes.`,
    inputSchema: { type: "object", properties: { since: { type: "string", description: "ISO 8601 lower bound. Omit for the last 7 days. Not a YYYY-MM-DD digest key; that is get_digest's date." }, provider: { type: "string", description: "Optional id from list_providers. Omit to span every tracked provider." }, kind: { type: "string", enum: ["status", "deprecations", "model_retirement", "pricing"], description: "Optional change class. Omit for every class. Not a provider id." }, limit: { type: "number", description: "Max rows to return. Default 100, max 500. Not a page token." } } }, annotations: { title: "Recent API changes", ...READ_CLOSED } },
  { name: "list_providers", title: "List tracked API providers", description: "List the AI and developer API providers Upstream tracks, each with state (operational, degraded, or outage) and checked_at. Use to choose a provider id before check_api_status. Do not use it as an MCP health check (check_mcp_server_health), as the change feed (list_recent_api_changes), as a spend score (score_spend), or as a merchant check (check_merchant). No arguments. Returns the catalog only. Does not probe providers or use the Toolweather check quota. Free.", inputSchema: { type: "object", properties: {} }, annotations: { title: "List providers", ...READ_CLOSED } },
  { name: "get_digest", title: "Daily AI/dev API breaking-changes digest", description: "Read one daily digest of AI/dev API incidents, model retirements, pricing-page edits, and breaking MCP server changes. Use once a day instead of polling list_recent_api_changes. Do not use it for a live provider check (check_api_status) or a filtered change feed (list_recent_api_changes). Omit date for the latest issue. Returns that issue's summary and items. Free: no Toolweather quota and no webhook.", inputSchema: { type: "object", properties: { date: { type: "string", description: "UTC day YYYY-MM-DD of one digest issue. Omit for the latest issue. Not an ISO timestamp and not a range." } } }, annotations: { title: "Daily digest", ...READ_CLOSED } },
];
// SpendGate's get_api_key is renamed: this server already exposes get_api_key for a Toolweather key.
// get_quota and score_spend do not match any Toolweather tool, so they keep SpendGate's names.
const SG_TOOLS = [
  { name: "score_spend", title: "Score a planned spend before an agent pays",
    description: "Score one planned payment and return approve, ask_human, or block before any tool sends money. Use before a crypto transfer, gift card, subscription, or paid API call. Do not use it for uptime (check_mcp_server_health, check_api_status), to judge whether a host or wallet is trusted (check_merchant), or to read remaining free scores (get_quota). Does not transfer funds. Returns score 0-100, verdict (approve means pay, ask_human means stop and ask a person, block means do not pay), reasons, and recommended_action. Scoring is deterministic, so rewording purpose will not change the verdict. amount is USD, not token units. api_key, when set, is a SpendGate sg_live_ key, not a Toolweather tw_ key from get_api_key. Uses SpendGate's quota (100/day anonymous, 1,000/day with an sg_live_ key), then $0.001 via x402, not the Toolweather check quota.",
    inputSchema: { type: "object", additionalProperties: false, required: ["amount"], properties: {
      amount: { type: "number", description: "Planned spend in USD, not in token atomic units. Compared with daily_budget_remaining and monthly_budget_remaining when those are set." },
      currency: { type: "string", description: "Payment currency. Defaults to USDC. Does not convert amount; amount stays in USD." },
      category: { type: "string", description: "One of status_check, api_call, saas, infra, gift_card, crypto_transfer, irreversible. Unknown categories are scored as unknown risk, not rejected." },
      purpose: { type: "string", description: "Short human description. Used as the category when category is omitted. Rewording it does not change a v1 verdict." },
      merchant: { type: "string", description: "Merchant name or https endpoint URL that would receive the spend. The call does not pay this URL." },
      endpoint: { type: "string", description: "Alias of merchant when the payee is an API URL. Pass one of them, not both for different payees." },
      agent_id: { type: "string", description: "Your agent or key id. Echoed back, not scored in v1." },
      api_key: { type: "string", description: "Optional SpendGate key (sg_live_…). Not a Toolweather tw_ key; that key belongs in the X-API-Key header. Omit to use the anonymous IP/session cap, which can reset on rotating egress." },
      daily_budget_remaining: { type: "number", description: "USD still available today. Amount above this is blocked. Omit if you have no daily cap to enforce." },
      monthly_budget_remaining: { type: "number", description: "USD still available this month. Amount above this is blocked. Omit if you have no monthly cap." },
      risk_hints: { type: "array", items: { type: "string" }, description: "Self-reported hints. Raising: new_payee, new_merchant, first_payment, high_velocity, mismatched_purpose, off_platform, shared_wallet. Lowering: trusted_repeat, within_policy, human_preapproved. known_merchant marks the payee known." },
    } }, annotations: { title: "Score a planned spend", ...READ_OPEN } },
  { name: "get_quota", title: "SpendGate free scores left today",
    description: "Read how many free SpendGate scores this caller has left today. Use before a run of score_spend calls. Do not use it for the Toolweather health-check quota, for PayCheck's merchant-check cap (paycheck_get_quota), or to mint a key (spendgate_get_api_key or get_api_key). Does not consume a score and is not billed. Pass an sg_live_ key to read that key's cap; omit it only for the anonymous IP/session cap, which can reset on rotating egress. A tw_ key does not select this cap. Returns the remaining SpendGate count only.",
    inputSchema: { type: "object", additionalProperties: false, properties: { api_key: { type: "string", description: "Optional sg_live_ key from spendgate_get_api_key. Not a Toolweather tw_ key. Omit only to read the anonymous IP/session cap, which can reset on rotating egress." } } }, annotations: { title: "SpendGate quota", ...READ_CLOSED } },
  { name: "spendgate_get_api_key", title: "Get a free SpendGate API key",
    description: "Mint one new SpendGate API key (sg_live_…) for score_spend and get_quota. Use it once, then pass the secret as api_key on those tools. Do not call it every turn, and do not use it when you need a Toolweather key for health checks and watches (get_api_key returns tw_…). No required arguments. Each call creates a different secret. Returns that secret once. Not billed, and not drawn from the Toolweather quota. label is an optional stored note, not an auth secret.",
    inputSchema: { type: "object", additionalProperties: false, properties: { label: { type: "string", description: "Optional note stored beside the new sg_live_ key, max 80 characters. Not a secret and not a Toolweather key." } } }, annotations: { title: "Get SpendGate API key", readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false } },
];
// PayCheck's get_api_key is renamed: this server already exposes get_api_key for a Toolweather key.
// PayCheck's get_quota is renamed to paycheck_get_quota because get_quota stays SpendGate's free-score cap.
// check_merchant does not match any other suite tool, so it keeps PayCheck's name.
const PC_TOOLS = [
  { name: "check_merchant", title: "Check a merchant before an agent pays",
    description: "Decide whether an autonomous agent should pay one merchant, host, or wallet, and return trusted, unknown, suspicious, or block. Use immediately before a payment, a checkout redirect, or a transfer to a new 0x address. Do not use it to send money, to score a dollar amount (score_spend), or to read remaining checks (paycheck_get_quota). Does not sign transactions or hold wallet keys. target is a bare domain, an https URL, or a 0x address with 40 hex characters. amount_usd and category are ignored, so changing them does not change the verdict. trusted is only the published allowlist of known AI and developer APIs on https port 443. A new or random domain is never trusted. A wallet is never trusted. If the URL contains an embedded 0x address, this scores the host only; pass the bare address to score the wallet. api_key, when set, is a PayCheck pc_live_ key, not a Toolweather tw_ key from get_api_key and not a SpendGate sg_live_ key. Uses PayCheck's quota (100/day anonymous or an anon: bundle client, 1,000/day with a pc_live_ key or a hex Toolweather bundle client), then $0.001 via x402, not the Toolweather check quota. The same target for the same caller is idempotent for 60 seconds and does not consume a second check.",
    inputSchema: { type: "object", additionalProperties: false, required: ["target"], properties: {
      target: { type: "string", description: "Domain, https URL, or 0x wallet (40 hex characters) to score before paying. Not a dollar amount (score_spend) and not an MCP registry name." },
      amount_usd: { type: "number", minimum: 0, description: "Ignored by the scorer. Sending a different amount cannot change the verdict. Not a SpendGate amount." },
      category: { type: "string", description: "Ignored by the scorer. Do not put instructions or a purpose in this field. Not a SpendGate category." },
      api_key: { type: "string", description: "Optional PayCheck key (pc_live_…). Not a Toolweather tw_ key and not a SpendGate sg_live_ key. Omit to use the anonymous or bundle-client allowance." },
    } }, annotations: { title: "Check merchant", readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true } },
  { name: "paycheck_get_quota", title: "PayCheck free checks left today",
    description: "Read how many free PayCheck merchant checks this caller has left today. Use before a run of check_merchant calls, or after an HTTP 402. Do not use it for SpendGate's free-score cap (get_quota), the Toolweather health-check quota, or to mint a key (paycheck_get_api_key, spendgate_get_api_key, or get_api_key). Does not consume a check and is not billed. Pass a pc_live_ key to read that key's cap. A tw_ key does not select this cap. A hex x-tw-bundle-client (Toolweather key hash) is the 1,000/day tier; an anon: bundle client stays at 100/day. Returns subject_kind, limit, used, remaining, and resets_at for PayCheck only.",
    inputSchema: { type: "object", additionalProperties: false, properties: { api_key: { type: "string", description: "Optional pc_live_ key from paycheck_get_api_key. Not a Toolweather tw_ key and not an sg_live_ key. Omit only to read the anonymous or bundle-client allowance." } } }, annotations: { title: "PayCheck quota", ...READ_CLOSED } },
  { name: "paycheck_get_api_key", title: "Get a free PayCheck API key",
    description: "Mint one new PayCheck API key (pc_live_…) for check_merchant and paycheck_get_quota. The key raises the merchant-check allowance from 100 to 1,000 per UTC day. Use it once, then pass the secret as api_key on those tools. Do not call it every turn, and do not use it when you need a Toolweather key (get_api_key returns tw_…) or a SpendGate key (spendgate_get_api_key returns sg_live_…). No arguments. Each call creates a different secret, returned once. Not billed, and not drawn from the Toolweather quota. Limited to 10 new keys per caller IP per UTC day.",
    inputSchema: { type: "object", additionalProperties: false, properties: {} }, annotations: { title: "Get PayCheck API key", readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false } },
];
const TOOLS = [
  { name: "check_mcp_server_health", title: "Is this MCP server healthy / safe to call right now?",
    description: "Read whether one remote MCP server, and optionally one tool on it, is safe to call right now. Use before adding or calling that server, or after its calls fail or time out. Do not use it to search by capability (find_alternative_mcp_servers), to vet a list (batch_check_mcp_servers; stored verdicts only), or to subscribe to later outages (watch_server). A result older than 5 minutes, or fresh/bearer_token, runs one read-only initialize and spends a live-check credit (200/day, then the cache). Counts on the Toolweather quota (1,000/key or 50/day anonymous). Returns summary, verdict (safe|caution|unsafe|unknown), recommended_action (call_it|call_with_care|use_alternative|verify_yourself), reasons, latency_ms, auth, last_tool_change, checked_at, and freshness. Also callable as is_safe_to_call.",
    inputSchema: { type: "object", properties: { server: SRV, tool: { type: "string", description: "Optional tool name on that server. If the server is up but this name is absent, verdict is unsafe. Omit to judge the server only." }, fresh: { type: "boolean", description: "True forces a live read-only initialize now and spends one of 200 live checks/day (priced as a live check). Omit to live-check only when the stored result is older than 5 minutes." }, bearer_token: { type: "string", description: "Token for the target server only, not a Toolweather key. One authenticated initialize + tools/list, never stored or logged. Setting it forces a live check." } }, required: ["server"] }, annotations: { title: "Is this MCP server healthy?", ...READ_OPEN } },
  { name: "find_alternative_mcp_servers", title: "Find a working MCP server that does X",
    description: "Search the Toolweather index for healthy MCP servers by capability, or for a replacement for one broken server. Use when check_mcp_server_health is unsafe or unknown, or when you lack a capability. Do not use it to judge one server you already chose (check_mcp_server_health) or to subscribe to later outages (watch_server). Does not call those servers and does not spend a live-check credit; unsafe rows are dropped. Returns summary, checked_at, and alternatives[] with server, url, verdict, latency_ms, and matching_tools. Counts on the Toolweather daily quota. Also callable as suggest_alternatives.",
    inputSchema: { type: "object", properties: { query: { type: "string", description: "Capability phrase such as 'send email', or the registry name/URL of a server to replace. Not a tool name on Toolweather itself.", examples: ["send email", "web search"] }, limit: { type: "integer", description: "Max ranked servers to return. Integer 1-20; omitted means 5. There is no paging cursor." } }, required: ["query"] }, annotations: { title: "Find alternative MCP servers", ...READ_CLOSED } },
  { name: "get_api_key", title: "Get a free Toolweather API key", description: "Mint one new Toolweather API key (tw_…) for health checks and watches on this server (1,000 calls/day, 3 watches). Use it once per install, then send that key as the X-API-Key header. Do not call it every turn, and do not use it when you need a SpendGate key (spendgate_get_api_key returns sg_live_…) or a PayCheck key (paycheck_get_api_key returns pc_live_…). Takes no arguments; the caller network is the limit. Each call creates a different secret. Returns api_key once, plus daily_limit and watches_limit; later calls cannot fetch it again. A few new keys per network per day, then reuse an existing key. Health checks also work with no key at 50 calls/day.",
    inputSchema: { type: "object", properties: {} }, annotations: { title: "Get free API key", readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false } },
  { name: "watch_server", title: "Alert me when an MCP server breaks", description: `Create one HTTPS webhook subscription for an indexed MCP server's verdict, auth requirement, or breaking tool-list change. Use instead of polling check_mcp_server_health. Do not use it to read health now (check_mcp_server_health) or to list subscriptions (list_watches). Requires a Toolweather key from get_api_key. The same server and webhook_url do not create a second watch; a different URL does. Three watches are free; each extra watch is ${PRICES.watch} for 30 days via x402. Returns id and a one-time secret for the signature header. Toolweather later POSTs alerts to the webhook_url you supply. Does not spend the daily check quota.`,
    inputSchema: { type: "object", properties: { server: SRV, webhook_url: { type: "string", description: "Public https URL Toolweather will POST JSON alerts to. Private, loopback, and non-https hosts are rejected. The same URL plus server does not create a second watch." } }, required: ["server", "webhook_url"] }, annotations: { title: "Watch a server", readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true } },
  { name: "list_watches", title: "List my server watches", description: "List webhook subscriptions stored for the Toolweather API key on this request, including delivery health. Use to copy a watch id before unwatch_server, or to see failures. Do not use it to learn if a server is up (check_mcp_server_health) or to create a subscription (watch_server). No arguments and no quota charge. The key is the X-API-Key header; anonymous callers are rejected. Returns watches[] (id, server, webhook_url, active, disabled_reason, last_known, consecutive_failures, last_delivery) and free_tier. Does not reveal webhook secrets and does not probe servers.", inputSchema: { type: "object", properties: {} }, annotations: { title: "List watches", ...READ_CLOSED } },
  { name: "unwatch_server", title: "Stop watching a server", description: "Delete one webhook subscription for this Toolweather API key and drop its pending deliveries. Use to stop alerts or to free a watch slot. Do not use it to change a webhook URL (delete, then watch_server) or to check health (check_mcp_server_health). Pass id from list_watches, not a server name. Repeating the same id deletes nothing further and returns watch not found. Returns {deleted: id}. Requires get_api_key first. Does not contact the MCP server and does not spend check quota.", inputSchema: { type: "object", properties: { id: { type: "string", description: "Watch id from list_watches (w_…). Not a server registry name. An unknown id returns watch not found and removes nothing else." } }, required: ["id"] }, annotations: { title: "Delete watch", readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: false } },
  { ...BATCH_TOOL, annotations: { title: "Batch health check", ...READ_CLOSED } },
  ...UP_TOOLS,
  ...SG_TOOLS,
  ...PC_TOOLS,
];
const ALIAS = { is_safe_to_call: "check_mcp_server_health", suggest_alternatives: "find_alternative_mcp_servers", get_status: "check_api_status", get_model: "check_model_deprecation", get_changes: "list_recent_api_changes" };
const SUITE = {
  upstream: { binding: "UPSTREAM", url: "https://upstream.toolweather.workers.dev" },
  spendgate: { binding: "SPENDGATE", url: "https://spendgate.toolweather.workers.dev" },
  paycheck: { binding: "PAYCHECK", url: "https://paycheck.toolweather.workers.dev" },
};
const SG_REMOTE = { spendgate_get_api_key: "get_api_key" };
const PC_REMOTE = { paycheck_get_api_key: "get_api_key", paycheck_get_quota: "get_quota" };
const SUITE_PROXY = new Map([
  ...UP_TOOLS.map((t) => [t.name, { id: "upstream", remote: t.name }]),
  ...SG_TOOLS.map((t) => [t.name, { id: "spendgate", remote: SG_REMOTE[t.name] || t.name }]),
  ...PC_TOOLS.map((t) => [t.name, { id: "paycheck", remote: PC_REMOTE[t.name] || t.name }]),
]);
const UP_URL = SUITE.upstream.url;
const SG_URL = SUITE.spendgate.url;
const PC_URL = SUITE.paycheck.url;
// Visible token chars only (UUID, hex, base64/base64url). 8–128 so a short or huge header cannot mint a client id.
const MCP_SESSION_RE = /^[A-Za-z0-9._~+/=-]{8,128}$/;
// x-tw-bundle-client. A Toolweather free key keeps g.h (sha256 hex, no anon: prefix).
// Anonymous + a safe Mcp-Session-Id: sha256("tw-mcp-session|"+id), 64 hex chars, not anon:,
// so the SpendGate/Upstream/PayCheck cap survives rotating cloud egress. No session: keep anon:+IP hash
// so SpendGate and PayCheck can apply the anonymous daily limit (100/day).
async function bundleClientId(g, req) {
  const h = g && g.h != null ? String(g.h) : "";
  if ((g && g.plan === "free_key") || (h && !h.startsWith("anon:"))) return h;
  const sid = String((req && req.headers && req.headers.get("mcp-session-id")) || "").trim();
  if (MCP_SESSION_RE.test(sid)) return sha256("tw-mcp-session|" + sid);
  return h;
}
// Upstream takeFree only accepts /^[a-f0-9]{16,128}$/i. SpendGate and PayCheck still get the
// anon: prefix (anonymous tier, 100/day). A hex client (Toolweather free-key hash, or a stable
// MCP session hash) is PayCheck's keyed free tier (1,000/day). Strip that prefix for Upstream
// only and forward the same hash; if the remainder is not that hex, omit the bundle client.
function bundleHeaderClient(productId, clientId) {
  if (productId === "upstream" && typeof clientId === "string" && clientId.startsWith("anon:")) {
    const hex = clientId.slice(5);
    return /^[a-f0-9]{16,128}$/i.test(hex) ? hex : "";
  }
  return clientId;
}
async function proxySuite(env, id, remoteName, args, meta, clientId) {
  const cfg = SUITE[id];
  const body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: remoteName, arguments: args || {}, ...(meta ? { _meta: meta } : {}) } });
  const headers = { "content-type": "application/json", "user-agent": "toolweather-suite/0.5", accept: "application/json" };
  const bundleClient = bundleHeaderClient(id, clientId);
  if (env.BUNDLE_PROXY_SECRET && bundleClient) {
    headers["x-tw-bundle-secret"] = env.BUNDLE_PROXY_SECRET;
    headers["x-tw-bundle-client"] = bundleClient;
  }
  const init = { method: "POST", headers, body };
  const endpoint = cfg.url + "/mcp";
  let r;
  const binding = env[cfg.binding];
  if (binding && typeof binding.fetch === "function") {
    try { r = await binding.fetch(new Request(endpoint, init)); }
    catch { r = null; }
    // A disconnected binding answers 503 with a non-JSON body. Use it only when it returned JSON-RPC; otherwise HTTPS.
    if (r) {
      let j = null; try { j = await r.clone().json(); } catch { j = null; }
      if (!j || (j.jsonrpc !== "2.0" && !("result" in j) && !("error" in j))) r = null;
    }
  }
  if (!r) r = await fetch(endpoint, init);
  const label = { upstream: "Upstream", spendgate: "SpendGate", paycheck: "PayCheck" }[id] || id;
  let j; try { j = await r.json(); } catch { return { isError: true, content: [{ type: "text", text: `${label} proxy failed (bad JSON)` }], structuredContent: { error: `${id}_proxy_failed`, status: r.status } }; }
  if (j.error) return { isError: true, content: [{ type: "text", text: j.error.message || `${id} error` }], structuredContent: j.error };
  return j.result || { isError: true, content: [{ type: "text", text: `empty ${id} result` }] };
}
function decorate(r) {
  if (!r || r.error) return r;
  if (r.alternatives) { const a = r.alternatives; return { summary: a.length ? `${a.length} working server(s) for "${r.query}". Best: ${a[0].server} (${a[0].verdict}${a[0].latency_ms ? ", " + Math.round(a[0].latency_ms) + " ms" : ""}).` : `No healthy servers found for "${r.query}". Try broader capability words.`, checked_at: new Date().toISOString(), ...r }; }
  const n = r.server + (r.tool ? " tool '" + r.tool + "'" : ""), why = (r.reasons || []).join("; ");
  const act = { safe: "call_it", caution: "call_with_care", unsafe: "use_alternative", unknown: "verify_yourself" }[r.verdict] || "verify_yourself";
  const txt = { safe: "Safe to call.", caution: "Usable, but call with retries/timeouts" + (r.status === "auth_required" ? " and valid credentials" : "") + ".", unsafe: "Do not rely on it now; use find_alternative_mcp_servers.", unknown: "Not in the Toolweather index; check it yourself." }[r.verdict] || "";
  const age = r.age_seconds ?? (r.last_checked ? Math.round((Date.now() - Date.parse(r.last_checked)) / 1000) : null);
  return { summary: `${n}: ${(r.verdict || "unknown").toUpperCase()}${why ? " (" + why + ")" : ""}. ${txt}`, verdict: r.verdict, recommended_action: act,
    checked_at: r.checked_at || r.last_checked || null, freshness: { age_seconds: age, fresh: age != null && age <= FRESH_S, source: r.source || "scheduled_probe" }, ...r };
}
const INFO = { name: "toolweather", title: "Toolweather", version: "0.5.0" };
const INSTR = "Toolweather suite: MCP health + AI/dev API status + model deprecations + spend scoring + merchant checks. Use check_mcp_server_health before adding/calling a third-party MCP server; if unsafe, use find_alternative_mcp_servers. Use check_api_status before calling an AI/dev API or after 5xx/timeouts; check_model_deprecation before using a model id; list_recent_api_changes / get_digest for what changed. Before an agent pays, transfers crypto, buys a gift card, or starts a paid subscription, call score_spend (approve = pay, ask_human = stop and ask a person, block = do not pay). get_quota reads SpendGate's free daily cap. Before paying a merchant, host, or wallet, call check_merchant (trusted = allowlisted https API, unknown or suspicious = verify, block = do not pay). paycheck_get_quota reads PayCheck's free daily cap (renamed because get_quota is SpendGate's). For a stable SpendGate daily cap through this bundle, call get_api_key first (Toolweather key) or pass a SpendGate sg_live_ key; anonymous callers share IP/session-based caps that can reset on rotating egress. spendgate_get_api_key mints a SpendGate key; paycheck_get_api_key mints a PayCheck pc_live_ key; get_api_key mints a Toolweather key. Anonymous bundle clients (anon:) stay on PayCheck's 100/day cap; a hex Toolweather client gets PayCheck's keyed free tier (1,000/day). Zero signup: call with no key (50 free/day anonymous) or get_api_key (1,000/day, 3 watches). Over the free quota, tools return an x402 payment requirement in _meta[\"x402/payment-required\"] (USDC on Base, $0.001 per check, pay-per-call, no subscription); retry with params._meta[\"x402/payment\"]. SpendGate and PayCheck tools use each product's own quota and prices. Every result starts with a one-line summary.";
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
      return ok({ protocolVersion: v, capabilities: { tools: {} }, serverInfo: INFO, instructions: INSTR });
    }
    case "ping": return ok({});
    case "tools/list": return ok({ tools: TOOLS });
    case "tools/call": {
      if (ALIAS[params.name]) params.name = ALIAS[params.name];
      if (params.name === "get_api_key") {
        const r = await newKey(env, req, "mcp");
        track(env, ctx, "get_api_key", "mcp", r.code === 201 ? "ok" : "rate_limited");
        return ok({ isError: r.code !== 201, content: [{ type: "text", text: r.code === 201 ? `Your Toolweather API key (shown once): ${r.body.api_key}\n1,000 calls/day, 3 watches (or 50/day with no key). Send it as the X-API-Key header.` : r.body.message }], structuredContent: r.body });
      }
      const via = SUITE_PROXY.get(params.name), pay = params._meta?.["x402/payment"];
      const units = params.name === "batch_check_mcp_servers" ? Math.min(Math.max((params.arguments?.servers || []).length, 1), BATCH_MAX) : 1;
      // SpendGate's api_key argument is an sg_live_ key, and PayCheck's is a pc_live_ key. Neither is a Toolweather key.
      const keyOrArg = via && (via.id === "spendgate" || via.id === "paycheck") ? key : (key || params.arguments?.api_key);
      if (["watch_server", "list_watches", "unwatch_server"].includes(params.name) && !keyOrArg) {
        return ok({ isError: true, content: [{ type: "text", text: "watch tools need an API key. Call get_api_key first (free, instant: 1,000 calls/day, 3 watches)." }] });
      }
      const counts = !via && !pay && !["list_watches", "unwatch_server", "watch_server"].includes(params.name);
      const g = await gate(env, keyOrArg, counts ? units : false, req);
      track(env, ctx, String(params.name || "?").slice(0, 40), "mcp", g.ok ? "ok" : g.code === 429 ? "rate_limited" : "unauthorized");
      trackKey(env, ctx, g, String(params.name || "?").slice(0, 40), "mcp", ["check_mcp_server_health", "watch_server"].includes(params.name) ? params.arguments?.server : null);
      if (["watch_server", "list_watches", "unwatch_server"].includes(params.name) && (!g.ok || g.plan === "anonymous" || !g.h || String(g.h).startsWith("anon:"))) {
        return ok({ isError: true, content: [{ type: "text", text: "watch tools need an API key. Call get_api_key first (free, instant)." }] });
      }
      const a = params.arguments || {};
      const MP = { check_mcp_server_health: (a.fresh === true || a.fresh === "true" || a.bearer_token) ? PRICES.live : PRICES.check, find_alternative_mcp_servers: PRICES.alternatives, batch_check_mcp_servers: PRICES.batch };
      const runTool = async (paid) => {
        let r;
        if (params.name === "check_mcp_server_health") r = decorate(await isSafe(env, a.server, a.tool, { ctx, fresh: a.fresh === true || a.fresh === "true", keyHash: paid ? null : g.h, paidLive: paid, bearer: a.bearer_token }));
        else if (params.name === "find_alternative_mcp_servers") r = decorate(await alternatives(env, a.query, a.limit));
        else r = await batchCheck(env, a.servers);
        if (!paid && g.ok && g.remaining != null) r.quota = quotaInfo(g, MP[params.name]);
        return { isError: !!r.error, content: [{ type: "text", text: JSON.stringify(r, null, 2) }], structuredContent: r };
      };
      if (MP[params.name] && (pay || g.over)) return ok(await payMcp(req, env, params.name, MP[params.name], pay, () => runTool(true), g.msg || "Paid call."));
      if (!g.ok) return ok({ isError: true, content: [{ type: "text", text: g.msg }] });
      if (via) return ok(await proxySuite(env, via.id, via.remote, a, params._meta, await bundleClientId(g, req)));
      if (MP[params.name]) return ok(await runTool(false));
      let r;
      if (params.name === "watch_server") {
        if (pay || (await freeWatchCount(env, g.h)) >= FREE_WATCHES) {
          const pre = await createWatch(env, g.h, a.server, a.webhook_url, true, true);
          if (pre.code >= 400) return ok({ isError: true, content: [{ type: "text", text: pre.body.error }], structuredContent: pre.body });
          return ok(await payMcp(req, env, "watch_server", PRICES.watch, pay, async () => { const w = await createWatch(env, g.h, a.server, a.webhook_url, true); return { isError: w.code >= 400, content: [{ type: "text", text: JSON.stringify(w.body, null, 2) }], structuredContent: w.body }; },
            `You're using all ${FREE_WATCHES} free watches. An extra watch costs ${PRICES.watch} for 30 days.`));
        }
        r = (await createWatch(env, g.h, a.server, a.webhook_url)).body;
      }
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
  if (!g.h || String(g.h).startsWith("anon:")) return;
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
const worker = {
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
      const paidReq = !!paymentHeader(req), fresh = ["1", "true"].includes(url.searchParams.get("fresh")), bearer = req.headers.get("x-upstream-authorization");
      const price = ep === "is_safe_to_call" ? (fresh || bearer ? PRICES.live : PRICES.check) : PRICES.alternatives;
      const g = await gate(env, key, !paidReq, req);
      track(env, ctx, ep, "rest", paidReq ? "paid" : g.ok ? "ok" : g.code === 429 ? "rate_limited" : "unauthorized");
      trackKey(env, ctx, g, ep, "rest", ep === "is_safe_to_call" ? url.searchParams.get("server") : null);
      const run = async (paid) => {
        const r = decorate(ep === "is_safe_to_call" ? await isSafe(env, url.searchParams.get("server"), url.searchParams.get("tool"), { ctx, fresh, keyHash: paid ? null : g.h, paidLive: paid, bearer })
          : await alternatives(env, url.searchParams.get("q"), url.searchParams.get("limit")));
        const h = paid ? {} : quotaHeaders(g, price); if (!paid) r.quota = quotaInfo(g, price);
        if (bearer) h["cache-control"] = "no-store";
        return J(r, r.error ? 400 : 200, h);
      };
      if (paidReq || g.over) return payHttp(req, env, price, ep, () => run(true), g.msg);
      if (!g.ok) return J({ error: g.msg }, g.code);
      return run(false);
    }
    if (p === "/v1/batch_check" || p === "/x402/batch_check") {
      if (req.method !== "POST") return J({ error: "use POST with JSON {servers:[...]}" }, 405);
      let b; try { b = await req.clone().json(); } catch { return J({ error: "JSON body required: {servers:[...]}" }, 400); }
      const lane = p.startsWith("/x402"), paidReq = lane || !!paymentHeader(req);
      const units = Math.min(Math.max(Array.isArray(b.servers) ? b.servers.length : 1, 1), BATCH_MAX);
      const g = lane ? { ok: true } : await gate(env, key, paidReq ? false : units, req);
      track(env, ctx, "batch_check", "rest", paidReq ? "paid" : g.ok ? "ok" : g.code === 429 ? "rate_limited" : "unauthorized");
      const run = async (paid) => { const r = await batchCheck(env, b.servers); if (!paid) r.quota = quotaInfo(g, PRICES.batch); return J(r, r.error ? 400 : 200, paid ? {} : quotaHeaders(g, PRICES.batch)); };
      if (paidReq || g.over) return payHttp(req, env, PRICES.batch, `batch health check of up to ${BATCH_MAX} MCP servers`, () => run(true), g.msg);
      if (!g.ok) return J({ error: g.msg }, g.code);
      return run(false);
    }
    // always-paid lanes (no key, no quota), for x402 directories and agents that prefer to just pay
    if (p === "/x402/is_safe_to_call" || p === "/x402/live_check" || p === "/x402/suggest_alternatives") {
      const live = p === "/x402/live_check", alt = p.endsWith("alternatives");
      let qb = {}; if (req.method === "POST") { try { qb = await req.clone().json(); } catch {} }
      const qp = (k) => qb[k] ?? url.searchParams.get(k);
      const price = live ? PRICES.live : alt ? PRICES.alternatives : PRICES.check;
      track(env, ctx, p.slice(1), "x402", paymentHeader(req) ? "paid" : "challenge");
      return payHttp(req, env, price, live ? "forced live recheck of one MCP server" : alt ? "find healthy alternative MCP servers" : "health verdict for one MCP server", async () => {
        const r = decorate(alt ? await alternatives(env, qp("q") ?? qp("query"), qp("limit")) : await isSafe(env, qp("server"), qp("tool"), { ctx, fresh: live, paidLive: true, bearer: req.headers.get("x-upstream-authorization") }));
        return J(r, r.error ? 400 : 200);
      }, "Paid lane.");
    }
    if (p === "/.well-known/x402") return J({ version: 1, resources: ["/x402/is_safe_to_call?server={server}", "/x402/live_check?server={server}", "/x402/suggest_alternatives?q={q}", "/x402/batch_check"].map((x) => url.origin + x), pricing: PAY_INFO(env) }, 200, { "cache-control": "max-age=3600" });
    if (p === "/v1/pricing") return J({ free: { calls_per_day_per_key: FREE_KEY_DAILY, anonymous_per_day: FREE_ANON_DAILY, live_rechecks_per_day: LIVE_PER_KEY_DAY, watches: FREE_WATCHES, key: "POST /v1/keys (instant, no signup)", no_key: `${FREE_ANON_DAILY}/day anonymous` }, ...PAY_INFO(env), notes: ["Zero signup: start with no key (anonymous free tier) or POST /v1/keys.", "Pay-per-call only via x402 (USDC on Base); no subscription.", "You are never charged for errors.", "Upstream tools in this bundle keep Upstream's own free quota and prices.", "SpendGate tools in this bundle keep SpendGate's own free quota and prices (100/day anonymous, 1,000/day per SpendGate key, then $0.001 per score). get_api_key is a Toolweather key; a SpendGate key is spendgate_get_api_key.", "PayCheck tools in this bundle keep PayCheck's own free quota and prices (100/day anonymous or an anon: bundle client, 1,000/day per PayCheck key or hex Toolweather bundle client, then $0.001 per check). get_api_key is a Toolweather key; a PayCheck key is paycheck_get_api_key. paycheck_get_quota is PayCheck's cap; get_quota stays SpendGate's."] }, 200, { "cache-control": "max-age=3600" });
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
      if (!key) return J({ error: "watches require an API key. Get one free: POST /v1/keys" }, 401);
      const g = await gate(env, key, false, req);
      track(env, ctx, ep, "rest", g.ok ? "ok" : g.code === 429 ? "rate_limited" : "unauthorized");
      trackKey(env, ctx, g, ep, "rest", ep === "watches.create" ? body.server : null);
      if (!g.ok) return J({ error: g.msg }, g.code);
      if (g.plan === "anonymous") return J({ error: "watches require an API key. Get one free: POST /v1/keys" }, 401);
      if (ep === "watches.create" && (paymentHeader(req) || (await freeWatchCount(env, g.h)) >= FREE_WATCHES)) {
        const pre = await createWatch(env, g.h, body.server, body.webhook_url, true, true);
        if (pre.code >= 400) return J(pre.body, pre.code);
        return payHttp(req, env, PRICES.watch, "one extra change-alert webhook watch for 30 days", async () => { const w = await createWatch(env, g.h, body.server, body.webhook_url, true); return J(w.body, w.code); },
          `You're using all ${FREE_WATCHES} free watches.`);
      }
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
    if (p === "/") return J({ name: "Toolweather", suite: ["mcp_health", "upstream_api_status", "spendgate", "paycheck"], what: "One MCP for MCP server health (~2,000 servers), AI/dev API status (~40 providers), model deprecations, spend scoring before an agent pays, and merchant checks before an agent pays a host or wallet.", docs: "https://github.com/toolweather/toolweather", openapi: "/openapi.json", server_card: "/.well-known/mcp", get_key: "POST /v1/keys", mcp_tools: TOOLS.map((t) => t.name), upstream_api: UP_URL, spendgate_api: SG_URL, paycheck_api: PC_URL, renamed_tools: { spendgate_get_api_key: "SpendGate get_api_key. get_api_key stays Toolweather's key. get_quota stays SpendGate's name; no Toolweather tool uses that name.", paycheck_get_api_key: "PayCheck get_api_key. get_api_key stays Toolweather's key.", paycheck_get_quota: "PayCheck get_quota. get_quota stays SpendGate's free-score cap." }, pricing: "/v1/pricing", x402: "/.well-known/x402", endpoints: ["/v1/keys", "/v1/pricing", "/v1/batch_check", "/v1/accuracy", "/v1/is_safe_to_call?server=&tool=", "/v1/suggest_alternatives?q=", "/v1/watches", "/mcp", "/health"] });
    return J({ error: "not found" }, 404);
  },
  async scheduled(ev, env, ctx) {
    ctx.waitUntil(runAlerts(env));
    if (new Date(ev.scheduledTime).getUTCMinutes() % 15 === 0) ctx.waitUntil(dispatchProbe(env));
  },
};

const TW_REST = [
  [/^(GET|POST) \/(v1|x402)\/is_safe_to_call$/, "is_safe_to_call"],
  [/^(GET|POST) \/(v1|x402)\/batch_check$/, "batch_check"],
  [/^(GET|POST) \/(v1|x402)\/suggest_alternatives$/, "suggest_alternatives"],
  [/^(GET|POST) \/x402\/live_check$/, "live_check"],
  [/^POST \/v1\/watches$/, "watch_server"],
];
const baseFetch = worker.fetch.bind(worker);
worker.fetch = withSources((req, env, ctx) => baseFetch(req, env, ctx), TW_REST);
export default worker;
