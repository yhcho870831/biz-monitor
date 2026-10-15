#!/usr/bin/env bash
# Daily on-disk backup: consistent SQLite snapshot (backup API, WAL-safe) +
# scheduler state + .env files, compressed into one tar.gz. Keeps 7 days.
# Archives live in data/backups/daily/ so the *.db glob used by
# backfill-share-guards (data/backups/*.db) is unaffected.
set -euo pipefail
cd /home/koast/biz-monitor
exec 9>/tmp/biz-monitor-daily-backup.lock
flock -n 9 || exit 0

DAILY=data/backups/daily
mkdir -p "$DAILY"
STAMP=$(TZ=Asia/Seoul date +%Y%m%d-%H%M%S)

created=$(docker compose exec -T biz-monitor-web python -m app.main backup-db | tail -n 1)
raw="data/backups/$(basename "$created")"
trap 'rm -f "$raw" "$raw-wal" "$raw-shm" "${tmp:-}"' EXIT
[ -s "$raw" ] || { echo "backup file missing: $raw" >&2; exit 1; }

python3 - "$raw" <<'PY'
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
result = con.execute("PRAGMA quick_check").fetchone()[0]
guards = con.execute("SELECT count(*) FROM notice_share_guards").fetchone()[0]
con.close()
if result != "ok" or guards == 0:
    sys.exit("backup failed verification: quick_check=%s guards=%s" % (result, guards))
PY

tmp="$DAILY/.biz-monitor-$STAMP.tar.gz.part"
state_files=$(cd data && ls -d scheduler-state* 2>/dev/null | sed 's#^#data/#')
# shellcheck disable=SC2086
tar czf "$tmp" -C data/backups "$(basename "$raw")" -C /home/koast/biz-monitor .env.override $state_files
chmod 600 "$tmp"
mv "$tmp" "$DAILY/biz-monitor-$STAMP.tar.gz"

find "$DAILY" -maxdepth 1 -name 'biz-monitor-*.tar.gz' -mtime +7 -delete
echo "$(date -Is) backup ok: $DAILY/biz-monitor-$STAMP.tar.gz"
