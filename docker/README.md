# Docker deployment

This project runs as separate containers:

- `biz-monitor-web`: internal calendar web UI
- `biz-monitor-slack`: Slack Socket Mode listener
- `biz-monitor-scheduler`: twice-daily scheduled runner

## Expected server layout

Current production host: `koast@192.168.3.60`.

```text
/home/koast/biz-monitor
  ├─ .env
  ├─ docker-compose.yml
  ├─ Dockerfile
  ├─ app/
  ├─ docker/
  ├─ data/
  └─ output/
```

## First startup

```bash
cd /home/koast/biz-monitor
mkdir -p data output/logs output/downloads output/tmp
docker compose build
docker compose run --rm biz-monitor-slack python -m app.main init-db
docker compose up -d biz-monitor-web biz-monitor-slack biz-monitor-scheduler
```

## Useful commands

```bash
docker compose ps
docker compose logs -f biz-monitor-web
docker compose logs -f biz-monitor-slack
docker compose logs -f biz-monitor-scheduler
docker compose exec biz-monitor-slack python -m app.main show-config
docker compose exec biz-monitor-slack python -m app.main backup-db
docker compose exec biz-monitor-slack python -m app.main manual-search --site kimst --term 아쿠아포닉스 --dry-run
docker compose exec biz-monitor-slack python -m app.main test-slack
```

## Redeploy

```bash
cd /home/koast/biz-monitor
scripts/deploy-with-db-backup.sh
```

## Database backup and restore

The production database is the SQLite file mounted from `./data` into `/app/data`.
Create a backup before every redeploy:

```bash
cd /home/koast/biz-monitor
docker compose exec biz-monitor-slack python -m app.main backup-db
```

The command prints a path like `/app/data/backups/app-20260701-093000.db`.

Restore with app containers stopped:

```bash
cd /home/koast/biz-monitor
docker compose stop biz-monitor-web biz-monitor-slack biz-monitor-scheduler \
  biz-monitor-worker-g2b biz-monitor-worker-d2b \
  biz-monitor-worker-research biz-monitor-worker-light
docker compose run --rm biz-monitor-slack \
  python -m app.main restore-db --file /app/data/backups/app-YYYYMMDD-HHMMSS.db
docker compose up -d
```

Prefer restore over re-searching after a database loss. Re-searching can refill
notices, but it loses share history and can cause duplicate Slack behavior.
