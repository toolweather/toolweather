#!/usr/bin/env bash
# Idempotent watchdog: ensures cron, the Toolweather API (:8787) and the static site (:8795) are running.
# Run from cron (@reboot + every minute) and from shell startup. Safe to call repeatedly.
cd /workspace/mcp-health; mkdir -p logs
pgrep -x cron >/dev/null || sudo -n service cron start >/dev/null 2>&1
if ! curl -sf -m 2 http://127.0.0.1:8787/health >/dev/null; then
  pkill -f "uvicorn api:app" 2>/dev/null
  nohup setsid .venv/bin/uvicorn api:app --host 127.0.0.1 --port 8787 >> logs/api.log 2>&1 < /dev/null &
fi
if ! curl -sf -m 2 http://127.0.0.1:8795/ >/dev/null; then
  nohup setsid python3 -m http.server 8795 --bind 127.0.0.1 --directory site >> logs/site.log 2>&1 < /dev/null &
fi
