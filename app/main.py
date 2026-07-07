from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from app.bootstrap import create_schema, ensure_directories, seed_sites_and_keywords
from app.collectors import build_collector_registry
from app.config import load_settings
from app.db import create_db_engine, create_session_factory, ensure_sqlite_parent
from app.logging_config import configure_logging
from app.repositories.notices import delete_expired_notices
from app.repositories.projects import replace_projects
from app.services.history_import import (
    apply_history_site_search_import,
    apply_history_import,
    preview_history_site_search_import,
    preview_history_import,
    write_preview_report,
)
from app.services.business_days import get_non_business_day_reason, is_scheduled_run_day
from app.services.collector_health import run_collector_health_check
from app.services.notifier import SlackNotifier
from app.services.pipeline import run_manual_search
from app.services.calendar import now_in_timezone
from app.services.calendar import backfill_saved_notice_deadlines_from_notices
from app.services.scheduler import (
    enqueue_scheduled_cycle,
    enqueue_scheduled_jobs,
    prepare_deferred_ai_evaluations,
    publish_deferred_scheduled_notices,
    run_pending_jobs,
    run_scheduled_cycle,
)
from app.slack_app import start_socket_mode
from app.web import create_web_app

logger = logging.getLogger(__name__)


def _scheduler_site_codes_from_env() -> set[str] | None:
    raw = os.getenv("SCHEDULER_SITE_CODES", "").strip()
    if not raw:
        return None
    site_codes = {
        part.strip().lower()
        for part in raw.split(",")
        if part.strip()
    }
    return site_codes or None


def build_runtime():
    settings = load_settings()
    configure_logging(settings)
    ensure_directories(settings)
    ensure_sqlite_parent(settings.database_url)
    engine = create_db_engine(settings.database_url)
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        seed_sites_and_keywords(session, settings)
    notifier = SlackNotifier(
        token=settings.slack_bot_token,
        retry_count=settings.slack_send_retry_count,
    )
    notifier.settings = settings
    registry = build_collector_registry()
    return settings, session_factory, notifier, registry


def command_init_db() -> None:
    build_runtime()
    logger.info("database initialized")


def _sqlite_path_from_database_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("backup-db only supports sqlite DATABASE_URL values")
    return Path(database_url[len(prefix) :])


def _check_sqlite_integrity(database_path: Path) -> None:
    with sqlite3.connect(str(database_path)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError("sqlite integrity check failed for %s: %s" % (database_path, result))


def command_backup_db(output: str = "") -> None:
    settings = load_settings()
    source_path = _sqlite_path_from_database_url(settings.database_url)
    if not source_path.exists():
        raise FileNotFoundError("database file not found: %s" % source_path)
    _check_sqlite_integrity(source_path)
    backup_dir = Path(output).expanduser() if output else source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / ("app-%s.db" % timestamp)
    with sqlite3.connect(str(source_path)) as source:
        with sqlite3.connect(str(backup_path)) as target:
            source.backup(target)
    _check_sqlite_integrity(backup_path)
    logger.info("database backup created path=%s", backup_path)
    print(str(backup_path))


def command_restore_db(input_file: str) -> None:
    settings = load_settings()
    target_path = _sqlite_path_from_database_url(settings.database_url)
    backup_path = Path(input_file).expanduser()
    if not backup_path.exists():
        raise FileNotFoundError("backup file not found: %s" % backup_path)
    _check_sqlite_integrity(backup_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        pre_restore_path = target_path.parent / ("app.pre-restore-%s.db" % timestamp)
        shutil.copy2(target_path, pre_restore_path)
        logger.info("current database copied before restore path=%s", pre_restore_path)
    shutil.copy2(backup_path, target_path)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    logger.info("database restored from=%s to=%s", backup_path, target_path)
    print(str(target_path))


def command_run_slack() -> None:
    settings, session_factory, notifier, registry = build_runtime()
    start_socket_mode(settings, session_factory, registry, notifier)


def command_manual(site: str, term: str, channel_id: str = "", dry_run: bool = False) -> None:
    settings, session_factory, notifier, registry = build_runtime()
    collector = registry.get(site.lower())
    if collector is None:
        raise ValueError("Unsupported site: %s" % site)

    channel = channel_id or settings.slack_command_channel_id
    if dry_run:
        results = collector.search(term)
        for candidate in results:
            print(
                "[%s] %s | %s | %s"
                % (
                    candidate.site_code.upper(),
                    candidate.title,
                    candidate.period_text or "\uae30\uac04 \ubbf8\uae30\uc7ac",
                    candidate.source_url,
                )
            )
        print("count=%s" % len(results))
        return

    with session_factory() as session:
        result = run_manual_search(
            session=session,
            collector_registry=registry,
            notifier=notifier,
            site_code=site,
            term=term,
            channel_id=channel,
            requested_by="cli",
            use_relevance_filter=False,
        )
    logger.info("manual cli result=%s", result)


def command_enqueue_scheduled() -> None:
    settings, session_factory, _, _ = build_runtime()
    site_codes = _scheduler_site_codes_from_env()
    with session_factory() as session:
        count = enqueue_scheduled_jobs(session, settings, site_codes=site_codes)
    logger.info("enqueued scheduled jobs count=%s", count)


def command_run_pending_jobs(backfill_only: bool = False) -> None:
    settings, session_factory, notifier, registry = build_runtime()
    if backfill_only:
        setattr(settings, "slack_backfill_only", True)
        notifier = None
    site_codes = _scheduler_site_codes_from_env()
    stats = run_pending_jobs(
        session_factory,
        registry,
        notifier,
        settings,
        site_codes=site_codes,
    )
    logger.info(
        "pending jobs completed shared=%s search_results=%s failed=%s skipped=%s",
        stats.total_shared,
        stats.total_search_results,
        stats.failed_jobs,
        stats.skipped_jobs,
    )


def command_prepare_deferred_ai(limit: int | None = None) -> None:
    settings, session_factory, _, _ = build_runtime()
    count = prepare_deferred_ai_evaluations(
        session_factory,
        settings,
        limit=limit or settings.ai_relevance_max_per_run,
    )
    logger.info("manual deferred AI prepare completed count=%s", count)


def command_publish_deferred_notices() -> None:
    settings, session_factory, notifier, _ = build_runtime()
    stats = publish_deferred_scheduled_notices(session_factory, notifier, settings)
    logger.info("manual deferred publish completed shared=%s", stats.total_shared)


def command_run_scheduled() -> None:
    """Enqueue-only scheduled run.

    Under the worker-based topology the scheduler only creates jobs; per-site
    workers (``run-worker-loop``) claim and process them. This keeps one slow
    site (or a stuck AI gateway) from blocking the whole batch.
    """
    settings, session_factory, notifier, registry = build_runtime()
    site_codes = _scheduler_site_codes_from_env()
    now = _now_in_timezone(settings.app_timezone)
    if not is_scheduled_run_day(
        now,
        skip_weekends=settings.scheduler_skip_weekends,
        skip_holidays=settings.scheduler_skip_public_holidays,
    ):
        reason = get_non_business_day_reason(
            now,
            skip_weekends=settings.scheduler_skip_weekends,
            skip_holidays=settings.scheduler_skip_public_holidays,
        )
        logger.info("scheduled enqueue skipped date=%s reason=%s", now.date(), reason)
        return
    count = enqueue_scheduled_cycle(
        session_factory,
        notifier,
        settings,
        site_codes=site_codes,
    )
    logger.info("scheduled enqueue completed jobs=%s", count)


def command_run_full_cycle() -> None:
    """Run a complete enqueue + process + send cycle in one process.

    Intended for development/verification. Production uses the scheduler +
    worker split (enqueue-scheduled-jobs / run-worker-loop).
    """
    settings, session_factory, notifier, registry = build_runtime()
    site_codes = _scheduler_site_codes_from_env()
    stats = run_scheduled_cycle(
        session_factory,
        registry,
        notifier,
        settings,
        site_codes=site_codes,
    )
    logger.info(
        "full cycle completed shared=%s search_results=%s failed=%s skipped=%s ai=%s",
        stats.total_shared,
        stats.total_search_results,
        stats.failed_jobs,
        stats.skipped_jobs,
        stats.ai_evaluations_used,
    )


def command_run_worker_loop() -> None:
    """Continuously claim and process pending jobs for the configured site scope.

    Each worker is scoped via SCHEDULER_SITE_CODES so a slow site only delays
    its own queue. Bodies are sent per-site as soon as a site drains; AI
    recommendations run as a trailing step after the bodies are sent.
    """
    settings, session_factory, notifier, registry = build_runtime()
    site_codes = _scheduler_site_codes_from_env()
    logger.info(
        "worker loop started scope=%s poll=%ss",
        ",".join(sorted(site_codes)) if site_codes else "all",
        settings.runner_poll_interval_seconds,
    )
    while True:
        try:
            stats = run_pending_jobs(
                session_factory,
                registry,
                notifier,
                settings,
                site_codes=site_codes,
            )
            if stats.jobs_claimed:
                logger.info(
                    "worker batch done scope=%s claimed=%s shared=%s search_results=%s failed=%s skipped=%s ai=%s",
                    ",".join(sorted(site_codes)) if site_codes else "all",
                    stats.jobs_claimed,
                    stats.total_shared,
                    stats.total_search_results,
                    stats.failed_jobs,
                    stats.skipped_jobs,
                    stats.ai_evaluations_used,
                )
        except Exception:
            logger.exception(
                "worker loop iteration failed scope=%s",
                ",".join(sorted(site_codes)) if site_codes else "all",
            )
        time.sleep(settings.runner_poll_interval_seconds)


def _scheduler_state_file(kind: str = "enqueue") -> Path:
    state_dir = Path(os.getenv("SCHEDULER_STATE_DIR", "/app/data"))
    state_dir.mkdir(parents=True, exist_ok=True)
    state_key = os.getenv("SCHEDULER_STATE_KEY", "").strip()
    if not state_key:
        site_codes = _scheduler_site_codes_from_env()
        state_key = "-".join(sorted(site_codes)) if site_codes else "default"
    return state_dir / f"scheduler-state-{kind}-{state_key}"


def _now_in_timezone(timezone_name: str) -> datetime:
    try:
        return (
            datetime.now(ZoneInfo(timezone_name))
            if ZoneInfo is not None
            else datetime.now()
        )
    except Exception:  # pragma: no cover
        if timezone_name == "Asia/Seoul":
            return datetime.now(timezone(timedelta(hours=9), name="KST"))
        return datetime.now()


def _load_scheduler_mark(kind: str = "enqueue") -> str:
    state_file = _scheduler_state_file(kind)
    if not state_file.exists():
        return ""
    return state_file.read_text(encoding="utf-8").strip()


def _save_scheduler_mark(mark: str, kind: str = "enqueue") -> None:
    _scheduler_state_file(kind).write_text(mark, encoding="utf-8")


def _due_scheduler_mark(now: datetime, schedule_times: list[str], last_mark: str) -> str:
    today = now.strftime("%Y-%m-%d")
    due_marks: list[str] = []
    for slot in sorted(set(schedule_times)):
        slot_dt = datetime.strptime(f"{today} {slot}", "%Y-%m-%d %H:%M").replace(
            tzinfo=now.tzinfo
        )
        mark = f"{today} {slot}"
        if slot_dt <= now and mark > last_mark:
            due_marks.append(mark)
    return due_marks[-1] if due_marks else ""


def _times_before(times: list[str], minutes: int) -> list[str]:
    result: list[str] = []
    for value in times:
        try:
            parsed = datetime.strptime(value, "%H:%M") - timedelta(minutes=minutes)
        except ValueError:
            continue
        result.append(parsed.strftime("%H:%M"))
    return result


def command_run_scheduler_loop() -> None:
    settings, session_factory, notifier, registry = build_runtime()
    site_codes = _scheduler_site_codes_from_env()
    logger.info(
        "scheduler loop started timezone=%s collect_times=%s publish_times=%s poll=%ss site_scope=%s",
        settings.app_timezone,
        ",".join(settings.schedule_times),
        ",".join(getattr(settings, "slack_publish_times", [])),
        settings.runner_poll_interval_seconds,
        ",".join(sorted(site_codes)) if site_codes else "all",
    )
    last_skip_date = ""
    failure_backoff_mark = ""
    failure_backoff_until = None

    while True:
        if not settings.scheduler_enabled:
            time.sleep(settings.runner_poll_interval_seconds)
            continue

        now = _now_in_timezone(settings.app_timezone)
        if not is_scheduled_run_day(
            now,
            skip_weekends=settings.scheduler_skip_weekends,
            skip_holidays=settings.scheduler_skip_public_holidays,
        ):
            today = now.date().isoformat()
            if today != last_skip_date:
                reason = get_non_business_day_reason(
                    now,
                    skip_weekends=settings.scheduler_skip_weekends,
                    skip_holidays=settings.scheduler_skip_public_holidays,
                )
                logger.info(
                    "scheduler loop skipped date=%s reason=%s",
                    now.date(),
                    reason,
                )
                last_skip_date = today
            time.sleep(settings.runner_poll_interval_seconds)
            continue
        last_skip_date = ""
        if getattr(settings, "slack_deferred_publish_enabled", False):
            prepare_times = _times_before(
                settings.slack_publish_times,
                settings.ai_relevance_prepare_minutes_before_publish,
            )
            prepare_mark = _due_scheduler_mark(
                now,
                prepare_times,
                _load_scheduler_mark("ai-prepare"),
            )
            if prepare_mark:
                logger.info("preparing deferred AI evaluations for %s", prepare_mark)
                try:
                    count = prepare_deferred_ai_evaluations(
                        session_factory,
                        settings,
                        limit=settings.ai_relevance_max_per_run,
                    )
                    _save_scheduler_mark(prepare_mark, "ai-prepare")
                    logger.info(
                        "prepared deferred AI evaluations for %s count=%s",
                        prepare_mark,
                        count,
                    )
                except Exception:
                    logger.exception("deferred AI prepare failed for %s", prepare_mark)

            publish_mark = _due_scheduler_mark(
                now,
                settings.slack_publish_times,
                _load_scheduler_mark("publish"),
            )
            if publish_mark:
                logger.info("publishing deferred notices for %s", publish_mark)
                try:
                    stats = publish_deferred_scheduled_notices(
                        session_factory,
                        notifier,
                        settings,
                    )
                    _save_scheduler_mark(publish_mark, "publish")
                    logger.info(
                        "published deferred notices for %s shared=%s",
                        publish_mark,
                        stats.total_shared,
                    )
                except Exception:
                    logger.exception("deferred publish failed for %s", publish_mark)

        if getattr(settings, "collector_health_enabled", True):
            health_mark = _due_scheduler_mark(
                now,
                settings.slack_publish_times,
                _load_scheduler_mark("health"),
            )
            if health_mark:
                logger.info("running collector health check for %s", health_mark)
                try:
                    run_collector_health_check(session_factory, notifier, settings)
                    _save_scheduler_mark(health_mark, "health")
                except Exception:
                    logger.exception(
                        "collector health check failed for %s", health_mark
                    )

        last_mark = _load_scheduler_mark("enqueue")
        due_mark = _due_scheduler_mark(now, settings.schedule_times, last_mark)

        if due_mark:
            if (
                failure_backoff_mark == due_mark
                and failure_backoff_until is not None
                and now < failure_backoff_until
            ):
                logger.warning(
                    "scheduled cycle backoff active mark=%s retry_after=%s",
                    due_mark,
                    failure_backoff_until.isoformat(),
                )
                time.sleep(settings.runner_poll_interval_seconds)
                continue
            logger.info("enqueuing scheduled cycle for %s", due_mark)
            try:
                count = enqueue_scheduled_cycle(
                    session_factory,
                    notifier,
                    settings,
                    site_codes=site_codes,
                )
                _save_scheduler_mark(due_mark, "enqueue")
                failure_backoff_mark = ""
                failure_backoff_until = None
                logger.info(
                    "enqueued scheduled cycle for %s jobs=%s",
                    due_mark,
                    count,
                )
            except Exception:
                logger.exception("scheduled enqueue failed for %s", due_mark)
                failure_backoff_mark = due_mark
                failure_backoff_until = now + timedelta(
                    minutes=max(1, settings.retry_delay_minutes)
                )

        time.sleep(settings.runner_poll_interval_seconds)


def command_test_slack() -> None:
    settings, _, notifier, _ = build_runtime()
    ts = notifier.send_text(
        settings.slack_command_channel_id,
        "biz-monitor Slack \uc5f0\uacb0 \ud14c\uc2a4\ud2b8 \uba54\uc2dc\uc9c0\uc785\ub2c8\ub2e4.",
    )
    logger.info("slack test sent ts=%s", ts)


def command_collector_health_report(
    window_hours: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    settings, session_factory, notifier, _ = build_runtime()
    summary = run_collector_health_check(
        session_factory,
        notifier,
        settings,
        window_hours=window_hours,
        dry_run=dry_run,
        force=force,
    )
    logger.info("collector-health-report summary=%s", summary)
    print("COLLECTOR_HEALTH_SUMMARY=%s" % json.dumps(summary, ensure_ascii=False, default=str))


def command_show_config() -> None:
    settings, _, _, _ = build_runtime()
    print("APP_ENV=%s" % settings.app_env)
    print("DATABASE_URL=%s" % settings.database_url)
    print("SCHEDULE_TIMES=%s" % ",".join(settings.schedule_times))
    print("SCHEDULER_SKIP_WEEKENDS=%s" % settings.scheduler_skip_weekends)
    print("SCHEDULER_SKIP_PUBLIC_HOLIDAYS=%s" % settings.scheduler_skip_public_holidays)
    print("SLACK_USE_SOCKET_MODE=%s" % settings.slack_use_socket_mode)
    print("SLACK_BRIEFING_CHANNEL_ID=%s" % settings.slack_briefing_channel_id)
    print("SLACK_COMMAND_CHANNEL_ID=%s" % settings.slack_command_channel_id)
    print("SLACK_DEFERRED_PUBLISH_ENABLED=%s" % settings.slack_deferred_publish_enabled)
    print("SLACK_PUBLISH_TIMES=%s" % ",".join(settings.slack_publish_times))
    print("SLACK_DEFERRED_PUBLISH_SINCE=%s" % settings.slack_deferred_publish_since)
    print(
        "AI_RELEVANCE_PREPARE_MINUTES_BEFORE_PUBLISH=%s"
        % settings.ai_relevance_prepare_minutes_before_publish
    )
    print("AI_RELEVANCE_MAX_PER_RUN=%s" % settings.ai_relevance_max_per_run)
    print("COLLECTOR_HEALTH_ENABLED=%s" % settings.collector_health_enabled)
    print(
        "COLLECTOR_HEALTH_WINDOW_HOURS=%s" % settings.collector_health_window_hours
    )
    print(
        "COLLECTOR_HEALTH_FAILED_THRESHOLD=%s"
        % settings.collector_health_failed_threshold
    )
    print(
        "COLLECTOR_HEALTH_EXCLUDE_SITES=%s"
        % ",".join(settings.collector_health_exclude_sites)
    )
    print(
        "COLLECTOR_HEALTH_CHANNEL_ID=%s"
        % (settings.collector_health_channel_id or "(command channel)")
    )
    for site_code, enabled in settings.site_enabled.items():
        print(
            "SITE_%s_ENABLED=%s keywords=%s"
            % (
                site_code.upper(),
                enabled,
                ",".join(settings.site_keywords.get(site_code, [])),
            )
        )


def command_cleanup_expired_notices() -> None:
    _, session_factory, notifier, _ = build_runtime()
    with session_factory() as session:
        count = delete_expired_notices(session, slack_file_deleter=notifier.delete_file)
    logger.info("expired notices deleted count=%s", count)


def command_backfill_saved_notice_deadlines() -> None:
    _, session_factory, _, _ = build_runtime()
    with session_factory() as session:
        count = backfill_saved_notice_deadlines_from_notices(session)
    logger.info("backfilled saved notice deadlines count=%s", count)


def command_seed_company_projects(file_path: str) -> None:
    _, session_factory, _, _ = build_runtime()
    path = Path(file_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Seed file must contain a JSON array")

    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError("Invalid project entry at index %s" % index)
        if not str(item.get("project_name", "")).strip():
            raise ValueError("project_name is required at index %s" % index)

    with session_factory() as session:
        count = replace_projects(session, data)
    logger.info("company projects replaced count=%s file=%s", count, path)


def command_run_web(host: str, port: int) -> None:
    settings, session_factory, _, _ = build_runtime()
    web_app = create_web_app(settings, session_factory)
    uvicorn.run(web_app, host=host, port=port)


def command_preview_history_import(file_path: str, output_path: str = "") -> None:
    settings, _, _, _ = build_runtime()
    input_path = Path(file_path).expanduser().resolve()
    report = preview_history_import(input_path, now_in_timezone(settings.app_timezone))
    if output_path:
        write_preview_report(report, Path(output_path).expanduser().resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))


def command_import_history(file_path: str, selected_by: str, batch_id: str, output_path: str = "") -> None:
    settings, session_factory, _, _ = build_runtime()
    input_path = Path(file_path).expanduser().resolve()
    report = preview_history_import(input_path, now_in_timezone(settings.app_timezone))
    if output_path:
        write_preview_report(report, Path(output_path).expanduser().resolve())

    with session_factory() as session:
        result = apply_history_import(
            session,
            input_path,
            now=now_in_timezone(settings.app_timezone),
            selected_by=selected_by,
            import_batch_id=batch_id,
        )
    logger.info("history import completed result=%s", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_preview_history_site_search(
    file_path: str,
    output_path: str = "",
    years: str = "",
    sites: str = "g2b,iris",
) -> None:
    settings, _, _, registry = build_runtime()
    input_path = Path(file_path).expanduser().resolve()
    target_years = [int(part.strip()) for part in years.split(",") if part.strip()] or None
    site_codes = tuple(part.strip().lower() for part in sites.split(",") if part.strip()) or ("g2b", "iris")
    report = preview_history_site_search_import(
        input_path,
        now=now_in_timezone(settings.app_timezone),
        collector_registry=registry,
        target_years=target_years,
        site_codes=site_codes,
    )
    if output_path:
        write_preview_report(report, Path(output_path).expanduser().resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))


def command_import_history_site_search(
    file_path: str,
    selected_by: str,
    batch_id: str,
    output_path: str = "",
    years: str = "",
    sites: str = "g2b,iris",
) -> None:
    settings, session_factory, _, registry = build_runtime()
    input_path = Path(file_path).expanduser().resolve()
    target_years = [int(part.strip()) for part in years.split(",") if part.strip()] or None
    site_codes = tuple(part.strip().lower() for part in sites.split(",") if part.strip()) or ("g2b", "iris")
    report = preview_history_site_search_import(
        input_path,
        now=now_in_timezone(settings.app_timezone),
        collector_registry=registry,
        target_years=target_years,
        site_codes=site_codes,
    )
    if output_path:
        write_preview_report(report, Path(output_path).expanduser().resolve())

    with session_factory() as session:
        result = apply_history_site_search_import(
            session,
            input_path,
            now=now_in_timezone(settings.app_timezone),
            selected_by=selected_by,
            import_batch_id=batch_id,
            collector_registry=registry,
            target_years=target_years,
            site_codes=site_codes,
        )
    logger.info("history site search import completed result=%s", result)
    print(json.dumps({"preview": report, "result": result}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Business notice monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db")
    subparsers.add_parser("run-slack")
    subparsers.add_parser("enqueue-scheduled-jobs")
    pending_parser = subparsers.add_parser("run-pending-jobs")
    pending_parser.add_argument("--backfill-only", action="store_true")
    subparsers.add_parser("run-worker-loop")
    subparsers.add_parser("run-scheduled-once")
    subparsers.add_parser("run-full-cycle-once")
    subparsers.add_parser("run-scheduler-loop")
    prepare_ai_parser = subparsers.add_parser("prepare-deferred-ai")
    prepare_ai_parser.add_argument("--limit", type=int, default=0)
    subparsers.add_parser("publish-deferred-notices")
    subparsers.add_parser("test-slack")
    subparsers.add_parser("show-config")
    health_parser = subparsers.add_parser("collector-health-report")
    health_parser.add_argument("--window-hours", type=int, default=0)
    health_parser.add_argument("--dry-run", action="store_true")
    health_parser.add_argument("--force", action="store_true")
    subparsers.add_parser("cleanup-expired-notices")
    subparsers.add_parser("backfill-saved-notice-deadlines")
    backup_db_parser = subparsers.add_parser("backup-db")
    backup_db_parser.add_argument("--output", default="")
    restore_db_parser = subparsers.add_parser("restore-db")
    restore_db_parser.add_argument("--file", required=True)
    web_parser = subparsers.add_parser("run-web")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8080)
    seed_parser = subparsers.add_parser("seed-company-projects")
    seed_parser.add_argument("--file", required=True)
    preview_history_parser = subparsers.add_parser("preview-history-import")
    preview_history_parser.add_argument("--file", required=True)
    preview_history_parser.add_argument("--output", default="")
    preview_history_site_parser = subparsers.add_parser("preview-history-site-search")
    preview_history_site_parser.add_argument("--file", required=True)
    preview_history_site_parser.add_argument("--output", default="")
    preview_history_site_parser.add_argument("--years", default="")
    preview_history_site_parser.add_argument("--sites", default="g2b,iris")
    import_history_parser = subparsers.add_parser("import-history")
    import_history_parser.add_argument("--file", required=True)
    import_history_parser.add_argument("--selected-by", default="관리자")
    import_history_parser.add_argument(
        "--batch-id", default=datetime.utcnow().strftime("history-%Y%m%d%H%M%S")
    )
    import_history_parser.add_argument("--output", default="")
    import_history_site_parser = subparsers.add_parser("import-history-site-search")
    import_history_site_parser.add_argument("--file", required=True)
    import_history_site_parser.add_argument("--selected-by", default="관리자")
    import_history_site_parser.add_argument(
        "--batch-id", default=datetime.utcnow().strftime("history-site-%Y%m%d%H%M%S")
    )
    import_history_site_parser.add_argument("--output", default="")
    import_history_site_parser.add_argument("--years", default="")
    import_history_site_parser.add_argument("--sites", default="g2b,iris")

    manual_parser = subparsers.add_parser("manual-search")
    manual_parser.add_argument("--site", required=True)
    manual_parser.add_argument("--term", required=True)
    manual_parser.add_argument("--channel-id", default="")
    manual_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if args.command == "init-db":
        command_init_db()
    elif args.command == "run-slack":
        command_run_slack()
    elif args.command == "enqueue-scheduled-jobs":
        command_enqueue_scheduled()
    elif args.command == "run-pending-jobs":
        command_run_pending_jobs(backfill_only=args.backfill_only)
    elif args.command == "run-worker-loop":
        command_run_worker_loop()
    elif args.command == "run-scheduled-once":
        command_run_scheduled()
    elif args.command == "run-full-cycle-once":
        command_run_full_cycle()
    elif args.command == "run-scheduler-loop":
        command_run_scheduler_loop()
    elif args.command == "prepare-deferred-ai":
        command_prepare_deferred_ai(limit=args.limit or None)
    elif args.command == "publish-deferred-notices":
        command_publish_deferred_notices()
    elif args.command == "test-slack":
        command_test_slack()
    elif args.command == "show-config":
        command_show_config()
    elif args.command == "collector-health-report":
        command_collector_health_report(
            window_hours=args.window_hours or None,
            dry_run=args.dry_run,
            force=args.force,
        )
    elif args.command == "cleanup-expired-notices":
        command_cleanup_expired_notices()
    elif args.command == "backfill-saved-notice-deadlines":
        command_backfill_saved_notice_deadlines()
    elif args.command == "backup-db":
        command_backup_db(args.output)
    elif args.command == "restore-db":
        command_restore_db(args.file)
    elif args.command == "run-web":
        command_run_web(host=args.host, port=args.port)
    elif args.command == "seed-company-projects":
        command_seed_company_projects(args.file)
    elif args.command == "preview-history-import":
        command_preview_history_import(args.file, args.output)
    elif args.command == "preview-history-site-search":
        command_preview_history_site_search(args.file, args.output, args.years, args.sites)
    elif args.command == "import-history":
        command_import_history(args.file, args.selected_by, args.batch_id, args.output)
    elif args.command == "import-history-site-search":
        command_import_history_site_search(
            args.file,
            args.selected_by,
            args.batch_id,
            args.output,
            args.years,
            args.sites,
        )
    elif args.command == "manual-search":
        command_manual(
            site=args.site,
            term=args.term,
            channel_id=args.channel_id,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
