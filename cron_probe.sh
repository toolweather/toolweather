#!/usr/bin/env bash
# Cron-friendly entrypoint. Example crontab (NOT installed):
#   */15 * * * * /workspace/mcp-health/cron_probe.sh
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
exec /usr/bin/flock -n /tmp/mcp-health.lock \
  timeout 840 .venv/bin/python mcphealth.py probe --limit 2000 --concurrency 64 --timeout 12 --budget 780 \
  >> "logs/probe-$(date +%Y%m%d).log" 2>&1
