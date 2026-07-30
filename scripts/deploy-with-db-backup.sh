#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DB_PATH="${DB_PATH:-data/app.db}"
BACKUP_DIR="${BACKUP_DIR:-data/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_PATH="$BACKUP_DIR/app-predeploy-$STAMP.db"

SERVICES=(
  biz-monitor-web
  biz-monitor-slack
  biz-monitor-scheduler
  biz-monitor-worker-g2b
  biz-monitor-worker-d2b
  biz-monitor-worker-research
  biz-monitor-worker-light
)

db_metric() {
  local path="$1"
  python3 - "$path" <<'PY'
import pathlib
import sqlite3
import sys

path = pathlib.Path(sys.argv[1])
if not path.exists():
    raise SystemExit("missing")
with sqlite3.connect(str(path)) as connection:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if not integrity or integrity[0] != "ok":
        raise SystemExit("integrity_failed:%s" % (integrity,))
    tables = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    has_notices = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='notices'"
    ).fetchone()[0]
    notices = 0
    if has_notices:
        notices = connection.execute("SELECT count(*) FROM notices").fetchone()[0]
print("%s %s" % (tables, notices))
PY
}

sqlite_backup() {
  local source="$1"
  local target="$2"
  python3 - "$source" "$target" <<'PY'
import pathlib
import sqlite3
import sys

source_path = pathlib.Path(sys.argv[1])
target_path = pathlib.Path(sys.argv[2])
if not source_path.exists():
    raise SystemExit("missing database: %s" % source_path)
target_path.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(str(source_path)) as source:
    integrity = source.execute("PRAGMA integrity_check").fetchone()
    if not integrity or integrity[0] != "ok":
        raise SystemExit("source integrity failed: %s" % (integrity,))
    with sqlite3.connect(str(target_path)) as target:
        source.backup(target)
with sqlite3.connect(str(target_path)) as target:
    integrity = target.execute("PRAGMA integrity_check").fetchone()
    if not integrity or integrity[0] != "ok":
        raise SystemExit("backup integrity failed: %s" % (integrity,))
print(target_path)
PY
}

restore_backup() {
  cp -f "$BACKUP_PATH" "$DB_PATH"
  rm -f "$DB_PATH-wal" "$DB_PATH-shm"
}

pre_metric="$(db_metric "$DB_PATH")"
pre_tables="${pre_metric%% *}"
pre_notices="${pre_metric##* }"
echo "predeploy database metric tables=$pre_tables notices=$pre_notices"
echo "backup: $(sqlite_backup "$DB_PATH" "$BACKUP_PATH")"

docker compose build
docker compose up -d --force-recreate "${SERVICES[@]}"

if ! post_metric="$(db_metric "$DB_PATH")"; then
  echo "postdeploy database check failed; restoring $BACKUP_PATH" >&2
  docker compose stop "${SERVICES[@]}" || true
  restore_backup
  docker compose up -d "${SERVICES[@]}"
  exit 1
fi

post_tables="${post_metric%% *}"
post_notices="${post_metric##* }"
echo "postdeploy database metric tables=$post_tables notices=$post_notices"

if (( post_tables < pre_tables )); then
  echo "postdeploy database looks regressed; restoring $BACKUP_PATH" >&2
  docker compose stop "${SERVICES[@]}" || true
  restore_backup
  docker compose up -d "${SERVICES[@]}"
  echo "restored database from $BACKUP_PATH"
  exit 1
fi

# Notice counts can legitimately decrease when retention runs during deploy.
# Recover durable re-post guards from prior consistent backups without changing
# the live notices or shares. A corrupt old backup is logged and skipped by the
# command rather than turning a successful deploy into a destructive rollback.
if ! docker compose exec -T biz-monitor-scheduler \
  python -m app.main backfill-share-guards --backup-dir /app/data/backups
then
  echo "deploy completed, but share-guard backfill failed; services are running and must not be rolled back" >&2
  exit 2
fi

echo "deploy complete; database preserved"
