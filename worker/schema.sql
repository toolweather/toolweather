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
