// x402 v2 "exact" payments (USDC on Base), modeled on Upstream. Free quota first; x402 only when it is used up or on /x402/* lanes.
import { x402ResourceServer, HTTPFacilitatorClient } from "@x402/core/server";
import { ExactEvmScheme } from "@x402/evm/exact/server";
import { encodePaymentRequiredHeader, decodePaymentSignatureHeader, encodePaymentResponseHeader } from "@x402/core/http";

export const PRICES = { check: "$0.002", live: "$0.005", alternatives: "$0.002", batch: "$0.02", watch: "$0.05" };
export const BATCH_MAX = 25;
export const PRICE_TABLE = [
  { item: "check_mcp_server_health / GET /v1/is_safe_to_call", free: "within 1,000 calls/day per key", paid: PRICES.check + " per call" },
  { item: "live recheck (fresh=true or bearer_token)", free: "200/day per key", paid: PRICES.live + " per call" },
  { item: "find_alternative_mcp_servers / GET /v1/suggest_alternatives", free: "within 1,000 calls/day per key", paid: PRICES.alternatives + " per call" },
  { item: "batch check, up to 25 servers (POST /v1/batch_check)", free: "each server counts as 1 call", paid: PRICES.batch + " per batch" },
  { item: "watch (change-alert webhook)", free: "3 per key", paid: PRICES.watch + " per extra watch per 30 days" },
  { item: "Upstream tools (API status, model deprecation, changes)", free: "Upstream's own quota", paid: "Upstream's own prices ($0.003 / $0.003 / $0.01)" },
];
export const PAY_INFO = (env) => ({ protocol: "x402 v2", scheme: "exact", network: env.X402_NETWORK, asset: "USDC", pay_to: env.PAY_TO, prices_usd: PRICES, table: PRICE_TABLE });

let X = null;
async function srv(env) {
  if (!X) {
    const server = new x402ResourceServer(new HTTPFacilitatorClient({ url: env.FACILITATOR_URL })).register(env.X402_NETWORK, new ExactEvmScheme());
    X = { server, ready: server.initialize() };
  }
  await X.ready; return X.server;
}
const opt = (env, price) => ({ scheme: "exact", price, network: env.X402_NETWORK, payTo: env.PAY_TO });
export const paymentHeader = (req) => req.headers.get("payment-signature") || req.headers.get("x-payment");
const CORS = { "access-control-allow-origin": "*", "access-control-expose-headers": "PAYMENT-REQUIRED, PAYMENT-RESPONSE, X-PAYMENT-RESPONSE, X-RateLimit-Limit, X-RateLimit-Remaining, X-Free-Remaining, X-Price-After-Free" };

// HTTP: 402 challenge, or verify -> run -> settle (only charged on success).
export async function payHttp(req, env, price, desc, run, why) {
  const s = await srv(env);
  const reqs = await s.buildPaymentRequirements(opt(env, price));
  const u = new URL(req.url);
  const resource = { url: u.origin + u.pathname, description: "Toolweather: " + desc, mimeType: "application/json" };
  const hdr = paymentHeader(req);
  if (!hdr) {
    const msg = why || "Payment required.";
    const pr = await s.createPaymentRequiredResponse(reqs, resource, msg);
    return new Response(JSON.stringify({ ...pr, message: `${msg} This call costs ${price} USDC on Base via x402. Retry with a PAYMENT-SIGNATURE header (any x402 client, e.g. @x402/fetch), or wait for the free quota to reset at 00:00 UTC.`, price_usd: price, pricing: "https://toolweather.pages.dev/#pricing" }, null, 2),
      { status: 402, headers: { "content-type": "application/json", "PAYMENT-REQUIRED": encodePaymentRequiredHeader(pr), ...CORS } });
  }
  let payload; try { payload = decodePaymentSignatureHeader(hdr); } catch { return new Response(JSON.stringify({ error: "invalid payment header" }), { status: 400, headers: { "content-type": "application/json", ...CORS } }); }
  const match = s.findMatchingRequirements(reqs, payload);
  const v = match && (await s.verifyPayment(payload, match).catch((e) => ({ isValid: false, invalidReason: String(e).slice(0, 200) })));
  if (!v?.isValid) return new Response(JSON.stringify({ error: "payment_invalid", reason: v?.invalidReason || "no matching requirement" }), { status: 402, headers: { "content-type": "application/json", ...CORS } });
  const res = await run();
  if (res.status >= 400) return res; // never charge for errors
  const st = await s.settlePayment(payload, match).catch((e) => ({ success: false, errorReason: String(e).slice(0, 200) }));
  if (!st?.success) return new Response(JSON.stringify({ error: "settlement_failed", detail: "you were not charged", reason: st?.errorReason }), { status: 402, headers: { "content-type": "application/json", ...CORS } });
  const h = new Headers(res.headers); h.set("PAYMENT-RESPONSE", encodePaymentResponseHeader(st));
  return new Response(res.body, { status: res.status, headers: h });
}

// MCP: requirement in _meta["x402/payment-required"]; payment in params._meta["x402/payment"].
export async function payMcp(req, env, name, price, pay, run, why) {
  const s = await srv(env);
  const reqs = await s.buildPaymentRequirements(opt(env, price));
  const resource = { url: new URL(req.url).origin + "/mcp#" + name, description: "Toolweather MCP tool " + name, mimeType: "application/json" };
  if (!pay) {
    const pr = await s.createPaymentRequiredResponse(reqs, resource, why);
    return { isError: true, content: [{ type: "text", text: `${why} This call costs ${price} USDC on Base via x402: retry with params._meta["x402/payment"] set to the signed payment payload, or wait for the reset at 00:00 UTC.\n` + JSON.stringify(pr) }], structuredContent: pr, _meta: { "x402/payment-required": pr } };
  }
  let payload; try { payload = typeof pay === "string" ? JSON.parse(atob(pay)) : pay; } catch { return { isError: true, content: [{ type: "text", text: "invalid payment payload" }] }; }
  const match = s.findMatchingRequirements(reqs, payload);
  const v = match && (await s.verifyPayment(payload, match).catch((e) => ({ isValid: false, invalidReason: String(e).slice(0, 200) })));
  if (!v?.isValid) return { isError: true, content: [{ type: "text", text: "payment invalid: " + (v?.invalidReason || "no matching requirement") }] };
  const r = await run();
  if (r.isError) return r;
  const st = await s.settlePayment(payload, match).catch((e) => ({ success: false, errorReason: String(e).slice(0, 200) }));
  if (!st?.success) return { isError: true, content: [{ type: "text", text: "payment settlement failed, you were not charged: " + (st?.errorReason || "unknown") }] };
  return { ...r, _meta: { ...(r._meta || {}), "x402/payment-response": st } };
}
