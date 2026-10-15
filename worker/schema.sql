CREATE TABLE IF NOT EXISTS servers(url TEXT PRIMARY KEY, name TEXT, transport TEXT, verdict TEXT, reasons TEXT,
  status TEXT, http_status INT, latency_ms REAL, protocol_version TEXT, readiness TEXT, supports_2026_07_28 INT,
  legacy_versions TEXT, auth TEXT, tool_count INT, tool_names TEXT, last_change TEXT, last_checked TEXT, alts TEXT);
CREATE INDEX IF NOT EXISTS ix_servers_name ON servers(name);
CREATE VIRTUAL TABLE IF NOT EXISTS tools_fts USING fts5(url UNINDEXED, name UNINDEXED, body);
CREATE TABLE IF NOT EXISTS changes(url TEXT, name TEXT, ts TEXT, severity TEXT, kind TEXT, tool TEXT, detail TEXT,
  UNIQUE(url, ts, kind, tool));
CREATE TABLE IF NOT EXISTS api_keys(key_hash TEXT PRIMARY KEY, label TEXT, daily_limit INT, created TEXT);
CREATE TABLE IF NOT EXISTS usage(key_hash TEXT, day TEXT, n INT, PRIMARY KEY(key_hash, day));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS api_daily(day TEXT, endpoint TEXT, channel TEXT, outcome TEXT, calls INT, PRIMARY KEY(day, endpoint, channel, outcome));
CREATE TABLE IF NOT EXISTS page_daily(day TEXT, path TEXT, ref TEXT, views INT, PRIMARY KEY(day, path, ref));
CREATE TABLE IF NOT EXISTS page_uniques(day TEXT, vh TEXT, PRIMARY KEY(day, vh));
CREATE TABLE IF NOT EXISTS key_daily(key_hash TEXT, day TEXT, endpoint TEXT, channel TEXT, calls INT, limited INT, first_ts TEXT, last_ts TEXT, PRIMARY KEY(key_hash, day, endpoint, channel));
CREATE TABLE IF NOT EXISTS key_servers(key_hash TEXT, day TEXT, server TEXT, n INT, PRIMARY KEY(key_hash, day, server));
CREATE TABLE IF NOT EXISTS watches(id TEXT PRIMARY KEY, key_hash TEXT, server TEXT, webhook_url TEXT, secret TEXT, created TEXT,
  active INT DEFAULT 1, disabled_reason TEXT, last_verdict TEXT, last_status TEXT, last_change_ts TEXT, fail_count INT DEFAULT 0, last_delivery_ts TEXT, paid INT DEFAULT 0, expires TEXT);
CREATE INDEX IF NOT EXISTS ix_watches_key ON watches(key_hash);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, watch_id TEXT, event TEXT, payload TEXT, created TEXT, attempts INT DEFAULT 0,
  next_attempt TEXT, state TEXT, last_http INT, last_error TEXT, delivered TEXT);
CREATE INDEX IF NOT EXISTS ix_deliv_pending ON deliveries(state, next_attempt);
CREATE TABLE IF NOT EXISTS key_signups(day TEXT, ih TEXT, n INT, PRIMARY KEY(day, ih));
CREATE TABLE IF NOT EXISTS keys_created(day TEXT, source TEXT, outcome TEXT, n INT, PRIMARY KEY(day, source, outcome));
CREATE TABLE IF NOT EXISTS live_checks(url TEXT PRIMARY KEY, ts TEXT, status TEXT, http_status INT, latency_ms REAL, protocol_version TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS live_usage(key_hash TEXT, day TEXT, n INT, PRIMARY KEY(key_hash, day));
CREATE TABLE IF NOT EXISTS accuracy_daily(day TEXT, verdict TEXT, pairs INT, matched INT, avg_gap_s INT, PRIMARY KEY(day, verdict));

CREATE TABLE IF NOT EXISTS anon(ip_hash TEXT, day TEXT, used INT, PRIMARY KEY(ip_hash, day));
