# Toolweather pricing research (2026-10-15)

## x402 per-call norms
- Census of the Coinbase Bazaar catalogue (14,696 resources, Aug 2026, fablerlabs.com/x402-pricing): p10 $0.001, p25 $0.003, **median $0.01**, p75 $0.05. 45% of priced entries sit between $0.001 and $0.01. Advertised prices, not revenue: most endpoints earn very little.
- x402scan's own data API: $0.01 per read, $0.02 for search; registry writes free (SIWX).
- Our Upstream: $0.003 for status/model, $0.01 for the change feed, $0.05 per extra watch per 30 days.
- Pattern: cheap lookups of precomputed data land at $0.001-$0.005; heavier or "live" work at $0.01+; subscriptions are rare on x402, so time-boxed passes (one payment = N days) stand in for them.

## API monitoring and status tools (human SaaS)
- StatusGator: free 3 monitors; $79/mo for 25 monitors; webhooks and API on paid plans.
- Better Stack: free 10 monitors at 3-min checks; +50 monitors $25/mo; 30 s checks on paid.
- UptimeRobot: 50 free monitors (non-commercial) at 5 min; ~$34/mo for 100 with faster checks.
- Pattern: **free tier = a few monitors at a slow interval; paid = more monitors, faster checks, more alert channels.** Each monitor costs about $0.25-$3 a month.

## MCP paid tools
- Most MCP servers are free with a key and a daily cap (often 100-1,000 calls a day). Paid MCPs either charge a SaaS subscription or, more and more often, use x402 per call, with the price in the tool description and an x402 requirement returned in `_meta` when the quota runs out.
- Agents plan from machine-readable signals: `X-RateLimit-*` headers, a price in the tool description, and a 402 with an exact amount instead of a hard 429.

## Takeaways for Toolweather
1. Keep 1,000 free calls/day per key and 3 free watches. That's more generous than the SaaS tools and fine for normal agent use.
2. Over the cap: an x402 402 challenge instead of a 429, so an agent can keep going by paying.
3. Price below the median, because the data is precomputed: $0.002 per health check, $0.005 per forced live recheck, $0.02 per 25-server batch, $0.05 per extra watch per 30 days (about $1.50 a month for 30 watches, far below SaaS per-monitor costs).
