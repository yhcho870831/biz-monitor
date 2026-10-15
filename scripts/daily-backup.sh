#!/usr/bin/env bash
# Daily consistent SQLite backup (backup API, WAL-safe). Keeps 7 days of app-YYYYMMDD-*.db.
set -euo pipefail
cd /home/koast/biz-monitor
exec 9>/tmp/biz-monitor-daily-backup.lock
flock -n 9 || exit 0
docker compose exec -T biz-monitor-web python -m app.main backup-db
find data/backups -maxdepth 1 -name 'app-2*.db' -mtime +7 -delete
