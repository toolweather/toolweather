# Toolweather pricing research (2026-10-15)

## x402 per-call norms
- Census of the Coinbase Bazaar catalogue (14,696 resources, Aug 2026, fablerlabs.com/x402-pricing): p10 $0.001, p25 $0.003, **median $0.01**, p75 $0.05. 45% of priced entries sit between $0.001 and $0.01. Advertised prices, not revenue: most endpoints earn very little.
- x402scan's own data API: $0.01 per read, $0.02 for search; registry writes free (SIWX).
- Our Upstream: $0.003 for status/model, $0.01 for the change feed, $0.05 per extra watch per 30 days.
- Pattern: cheap lookups of precomputed data land at $0.001-$0.005; heavier or "live" work at $0.01+; subscriptions are rare on x402, so time-boxed passes (one payment = N days) stand in for them.

## Agent-first choice (shipped)
Autonomous agents pick the cheapest clear option with zero signup friction. So:
1. Keep 1,000 free calls/day per key and **50/day anonymous with no key** (one-shot trials).
2. Undercut the median hard: **$0.001** health check, **$0.003** live recheck, **$0.001** alternatives, **$0.01** batch of 25, **$0.05**/30d extra watch.
3. Pay-per-call only (no subscription). Every response exposes remaining free quota + next price; 402s name price, asset, network, payTo, and free reset.
4. Over the cap: x402 402 challenge instead of a hard 429, so an agent can keep going by paying.
