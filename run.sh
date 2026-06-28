#!/bin/bash
# Scheduled refresh: mirror orders to the Sheet/CSV and sync ClickUp.
# Used by com.otlobly.sync.plist (launchd) or cron. Logs to sync.log.
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; source .env; set +a; fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') run ==="
python3 sheets.py || echo "sheets.py failed"
python3 clickup.py || echo "clickup.py skipped/failed (check config.json + token)"
