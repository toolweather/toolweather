// Change alerts: keys watch servers; a cron trigger detects changes and POSTs signed webhooks with retry/backoff.
export const FREE_WATCHES = 3;
const BACKOFF_MIN = [1, 5, 30, 120, 360];   // minutes between attempts; after the last one the delivery is marked failed
const DISABLE_AFTER = 3;                      // consecutive failed deliveries before a watch is switched off
const RANK = { safe: 0, caution: 1, unsafe: 2 };
const now = () => new Date().toISOString();
const rid = (p) => p + "_" + [...crypto.getRandomValues(new Uint8Array(9))].map((x) => x.toString(16).padStart(2, "0")).join("");
const hex = (b) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");

// ---- webhook URL safety: https only, no private/loopback/link-local/metadata targets ----
function privV4(ip) {
  const m = ip.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/); if (!m) return false;
  const [a, b] = [+m[1], +m[2]];
  return a === 0 || a === 10 || a === 127 || a >= 224 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || (a === 192 && b === 0) || (a === 198 && (b === 18 || b === 19));
}
function privV6(ip) {
  const s = ip.toLowerCase().replace(/^\[|\]$/g, "");
  if (s === "::" || s === "::1") return true;
  if (s.startsWith("::ffff:")) return privV4(s.slice(7)) || !/\./.test(s.slice(7));
  return /^(fc|fd|fe8|fe9|fea|feb|ff)/.test(s) || s.startsWith("64:ff9b") || s.startsWith("2001:db8");
}
async function dohIps(host, type) {
  try {
    const r = await fetch(`https://cloudflare-dns.com/dns-query?name=${encodeURIComponent(host)}&type=${type}`, { headers: { accept: "application/dns-json" } });
    const j = await r.json();
    return (j.Answer || []).filter((a) => a.type === (type === "A" ? 1 : 28)).map((a) => a.data);
  } catch { return null; }
}
export async function checkWebhook(raw) {
  let u; try { u = new URL(raw); } catch { return "webhook_url is not a valid URL"; }
  if (u.protocol !== "https:") return "webhook_url must use https";
  if (u.username || u.password) return "webhook_url must not contain credentials";
  if (u.port && u.port !== "443") return "webhook_url must use the default https port (443)";
  const h = u.hostname.toLowerCase().replace(/\.$/, "");
  if (!h.includes(".") || /(^|\.)(localhost|local|internal|intranet|lan|home|corp|localdomain)$/.test(h)) return "webhook_url host is not public";
  if (/^[\d.]+$/.test(h) || h.startsWith("[")) {
    if (/^[\d.]+$/.test(h) ? (privV4(h) || !/^\d+\.\d+\.\d+\.\d+$/.test(h)) : privV6(h)) return "webhook_url points to a private or loopback address";
    return null;
  }
  const [a, aaaa] = await Promise.all([dohIps(h, "A"), dohIps(h, "AAAA")]);
  if (a === null && aaaa === null) return "could not resolve webhook_url host";
  const ips = [...(a || []), ...(aaaa || [])];
  if (!ips.length) return "webhook_url host does not resolve";
  if (ips.some((ip) => (ip.includes(":") ? privV6(ip) : privV4(ip)))) return "webhook_url resolves to a private or loopback address";
  return null;
}

// Current state of a server name: best endpoint by verdict (same rule as is_safe_to_call).
async function serverState(env, server) {
  const { results } = await env.DB.prepare("SELECT name,url,verdict,status,reasons,auth,last_checked FROM servers WHERE name=? OR url=?").bind(server, server).all();
  if (!results.length) return null;
  results.sort((x, y) => RANK[x.verdict] - RANK[y.verdict]);
  return results[0];
}
async function lastBreakingTs(env, name) {
  const r = await env.DB.prepare("SELECT max(ts) t FROM changes WHERE name=? AND severity='breaking'").bind(name).first();
  return (r && r.t) || "";
}
const pub = (w) => ({ id: w.id, server: w.server, webhook_url: w.webhook_url, active: !!w.active, disabled_reason: w.disabled_reason || null,
  created: w.created, plan: w.paid ? "paid" : "free", expires: w.expires || null, last_known: { verdict: w.last_verdict, status: w.last_status }, consecutive_failures: w.fail_count, last_delivery: w.last_delivery_ts || null });

export async function freeWatchCount(env, h) {
  return (await env.DB.prepare("SELECT count(*) c FROM watches WHERE key_hash=? AND active=1 AND coalesce(paid,0)=0").bind(h).first()).c;
}
// paid=true: an x402-paid extra watch (30 days). dryRun: validate only (used before asking for payment).
export async function createWatch(env, h, server, webhook_url, paid = false, dryRun = false) {
  if (!server || !webhook_url) return { code: 400, body: { error: "server and webhook_url are required" } };
  const bad = await checkWebhook(String(webhook_url)); if (bad) return { code: 400, body: { error: bad } };
  const st = await serverState(env, String(server));
  if (!st) return { code: 404, body: { error: "server not in Toolweather index; use a registry name or URL (see /v1/is_safe_to_call)" } };
  const n = { c: await freeWatchCount(env, h) };
  if (!paid && n.c >= FREE_WATCHES) return { code: 403, body: { error: "watch_limit_reached", limit: FREE_WATCHES,
    message: `You're watching ${FREE_WATCHES} servers, the free-tier limit. Remove one with DELETE /v1/watches/{id} to watch another. Or add an extra watch for $0.05 per 30 days via x402 (retry with a PAYMENT-SIGNATURE header).` } };
  const dup = await env.DB.prepare("SELECT id FROM watches WHERE key_hash=? AND server=? AND webhook_url=? AND active=1").bind(h, st.name, webhook_url).first();
  if (dup) return { code: 409, body: { error: "already watching this server with this webhook_url", id: dup.id } };
  if (dryRun) return { code: 200, body: { ok: true } };
  const expires = paid ? new Date(Date.now() + 30 * 864e5).toISOString() : null;
  const secret = "whsec_" + hex(crypto.getRandomValues(new Uint8Array(24)));
  const w = { id: rid("w"), server: st.name, webhook_url, created: now() };
  await env.DB.prepare("INSERT INTO watches(id,key_hash,server,webhook_url,secret,created,active,last_verdict,last_status,last_change_ts,fail_count,paid,expires) VALUES(?,?,?,?,?,?,1,?,?,?,0,?,?)")
    .bind(w.id, h, w.server, webhook_url, secret, w.created, st.verdict, st.status, await lastBreakingTs(env, st.name), paid ? 1 : 0, expires).run();
  return { code: 201, body: { ...w, active: true, secret, current: { verdict: st.verdict, status: st.status },
    events: ["verdict_changed", "auth_required", "breaking_tool_change"], signature: "X-Toolweather-Signature: t=<unix>,v1=hex(HMAC-SHA256(secret, `${t}.${raw_body}`))",
    plan: paid ? "paid extra watch (30 days)" : "free watch", expires: expires,
    free_tier: { watches_used: n.c + (paid ? 0 : 1), watches_limit: FREE_WATCHES }, note: "Store the secret now; it is not shown again." } };
}
export async function listWatches(env, h) {
  const { results } = await env.DB.prepare("SELECT * FROM watches WHERE key_hash=? ORDER BY created").bind(h).all();
  const act = results.filter((w) => w.active && !w.paid).length;
  return { code: 200, body: { watches: results.map(pub), free_tier: { watches_used: act, watches_limit: FREE_WATCHES } } };
}
export async function deleteWatch(env, h, id) {
  const r = await env.DB.prepare("DELETE FROM watches WHERE id=? AND key_hash=?").bind(id || "", h).run();
  if (!r.meta.changes) return { code: 404, body: { error: "watch not found" } };
  await env.DB.prepare("DELETE FROM deliveries WHERE watch_id=? AND state='pending'").bind(id).run();
  return { code: 200, body: { deleted: id } };
}

// ---- detection + delivery (cron) ----
async function sign(secret, t, body) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(`${t}.${body}`)));
}
async function detect(env) {
  const { results: ws } = await env.DB.prepare("SELECT * FROM watches WHERE active=1 AND (expires IS NULL OR expires > ?)").bind(now()).all();
  let made = 0;
  for (const w of ws) {
    const st = await serverState(env, w.server); if (!st) continue;
    const ev = [];
    const base = { server: w.server, url: st.url, verdict: st.verdict, status: st.status, reasons: JSON.parse(st.reasons || "[]"), last_checked: st.last_checked };
    if (st.status === "auth_required" && w.last_status !== "auth_required")
      ev.push({ type: "auth_required", previous: { verdict: w.last_verdict, status: w.last_status }, auth: JSON.parse(st.auth || "null") });
    if (st.verdict !== w.last_verdict) ev.push({ type: "verdict_changed", previous: { verdict: w.last_verdict, status: w.last_status } });
    const bt = await lastBreakingTs(env, w.server);
    if (bt && bt > (w.last_change_ts || "")) {
      const { results: ch } = await env.DB.prepare("SELECT ts,kind,tool,detail FROM changes WHERE name=? AND severity='breaking' AND ts>? ORDER BY ts LIMIT 50").bind(w.server, w.last_change_ts || "").all();
      ev.push({ type: "breaking_tool_change", changes: ch });
    }
    const stm = [env.DB.prepare("UPDATE watches SET last_verdict=?, last_status=?, last_change_ts=? WHERE id=?").bind(st.verdict, st.status, bt || w.last_change_ts, w.id)];
    for (const e of ev) {
      const id = rid("evt");
      const payload = JSON.stringify({ id, type: e.type, watch_id: w.id, created: now(), ...base, ...e });
      stm.push(env.DB.prepare("INSERT INTO deliveries(id,watch_id,event,payload,created,attempts,next_attempt,state) VALUES(?,?,?,?,?,0,?,'pending')").bind(id, w.id, e.type, payload, now(), now()));
      stm.push(env.DB.prepare("INSERT INTO key_daily VALUES(?,?,?,?,1,0,?,?) ON CONFLICT(key_hash,day,endpoint,channel) DO UPDATE SET calls=calls+1, last_ts=excluded.last_ts")
        .bind(w.key_hash, now().slice(0, 10), "watch_event:" + e.type, "webhook", now(), now()));
      made++;
    }
    await env.DB.batch(stm);
  }
  return made;
}
async function deliver(env) {
  const { results } = await env.DB.prepare(`SELECT d.*, w.webhook_url, w.secret, w.key_hash, w.active FROM deliveries d JOIN watches w ON w.id=d.watch_id
    WHERE d.state='pending' AND d.next_attempt<=? ORDER BY d.next_attempt LIMIT 40`).bind(now()).all();
  const out = { sent: 0, failed_attempts: 0 };
  await Promise.all(results.map(async (d) => {
    if (!d.active) return env.DB.prepare("UPDATE deliveries SET state='cancelled' WHERE id=?").bind(d.id).run();
    const t = Math.floor(Date.now() / 1000);
    let http = null, err = null;
    const bad = await checkWebhook(d.webhook_url);   // re-check at send time (DNS may have changed)
    if (bad) err = bad;
    else try {
      const r = await fetch(d.webhook_url, { method: "POST", redirect: "manual", signal: AbortSignal.timeout(10000), headers: { "content-type": "application/json",
        "user-agent": "Toolweather-Webhooks/1.0 (+https://toolweather.pages.dev)", "x-toolweather-event": d.event, "x-toolweather-delivery": d.id,
        "x-toolweather-signature": `t=${t},v1=${await sign(d.secret, t, d.payload)}` }, body: d.payload });
      http = r.status; if (r.status < 200 || r.status >= 300) err = `HTTP ${r.status}`;
    } catch (e) { err = String(e.message || e).slice(0, 200); }
    const att = d.attempts + 1, day = now().slice(0, 10);
    const kd = (ep) => env.DB.prepare("INSERT INTO key_daily VALUES(?,?,?,?,1,0,?,?) ON CONFLICT(key_hash,day,endpoint,channel) DO UPDATE SET calls=calls+1, last_ts=excluded.last_ts").bind(d.key_hash, day, ep, "webhook", now(), now());
    if (!err) {
      out.sent++;
      return env.DB.batch([env.DB.prepare("UPDATE deliveries SET state='delivered', attempts=?, last_http=?, last_error=NULL, delivered=? WHERE id=?").bind(att, http, now(), d.id),
        env.DB.prepare("UPDATE watches SET fail_count=0, last_delivery_ts=? WHERE id=?").bind(now(), d.watch_id), kd("webhook_delivered")]);
    }
    out.failed_attempts++;
    if (att <= BACKOFF_MIN.length) {
      const next = new Date(Date.now() + BACKOFF_MIN[att - 1] * 60000).toISOString();
      return env.DB.batch([env.DB.prepare("UPDATE deliveries SET attempts=?, last_http=?, last_error=?, next_attempt=? WHERE id=?").bind(att, http, err, next, d.id), kd("webhook_retry")]);
    }
    return env.DB.batch([env.DB.prepare("UPDATE deliveries SET state='failed', attempts=?, last_http=?, last_error=? WHERE id=?").bind(att, http, err, d.id),
      env.DB.prepare(`UPDATE watches SET fail_count=fail_count+1, active=CASE WHEN fail_count+1>=${DISABLE_AFTER} THEN 0 ELSE active END,
        disabled_reason=CASE WHEN fail_count+1>=${DISABLE_AFTER} THEN 'disabled after ${DISABLE_AFTER} failed deliveries; delete and re-create to resume' ELSE disabled_reason END WHERE id=?`).bind(d.watch_id),
      kd("webhook_failed")]);
  }));
  return out;
}
export async function runAlerts(env) {
  const made = await detect(env);
  const r = await deliver(env);
  await env.DB.prepare("INSERT OR REPLACE INTO meta VALUES('last_alert_run', ?)").bind(JSON.stringify({ ts: now(), events: made, ...r })).run();
  return { events: made, ...r };
}
