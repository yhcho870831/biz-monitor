"""Collector stability metrics and alerting.

This module summarizes recent collector job health per site and emits a Slack
alert only when a monitored site looks unhealthy. It is intentionally scoped to
sites *other than* g2b by default (g2b is excluded via
``COLLECTOR_HEALTH_EXCLUDE_SITES``) because g2b has a separate, slower worker and
different failure characteristics that would otherwise create noise.

Design notes:
- The most reliable signal is ``jobs.status == 'failed'`` (an error was recorded).
- "Zero results" is *not* used as a failure signal: per-site daily yield is very
  low (0-10 notices/day) and zero-yield days are normal.
- ``finished_at`` is stored as naive UTC (``datetime.utcnow``), so the rolling
  window is computed in UTC.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Job, NoticeAiEvaluation

logger = logging.getLogger(__name__)

DEFAULT_EXCLUDED_SITES = ("g2b",)


@dataclass
class SiteHealth:
    site_code: str
    success_count: int = 0
    failed_count: int = 0
    last_success_at: datetime | None = None
    sample_error: str = ""
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class AiHealth:
    done_count: int = 0
    failed_count: int = 0
    last_done_at: datetime | None = None
    sample_error: str = ""
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)


def resolve_monitored_sites(settings, exclude_sites: Iterable[str] | None = None) -> list[str]:
    """Enabled sites minus the excluded ones (g2b by default)."""
    exclude = {
        str(code).strip().lower()
        for code in (
            exclude_sites
            if exclude_sites is not None
            else getattr(settings, "collector_health_exclude_sites", None)
            or DEFAULT_EXCLUDED_SITES
        )
        if str(code).strip()
    }
    site_enabled = getattr(settings, "site_enabled", {}) or {}
    monitored = [
        code
        for code, enabled in site_enabled.items()
        if enabled and code not in exclude
    ]
    return sorted(monitored)


def _first_line(text: str, width: int = 120) -> str:
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    if len(line) > width:
        return line[: width - 1] + "\u2026"
    return line


def evaluate_health(
    monitored_sites: Sequence[str],
    window_rows: Iterable[tuple[str, str, str]],
    last_success_map: dict[str, datetime | None],
    failed_threshold: int = 1,
) -> dict[str, SiteHealth]:
    """Pure health evaluation over already-windowed job rows.

    ``window_rows`` items are ``(site_code, status, error_message)`` tuples for
    jobs whose ``finished_at`` falls inside the monitoring window.
    ``last_success_map`` maps site_code -> all-time last successful ``finished_at``.

    A site is flagged when it recorded at least ``failed_threshold`` failures in
    the window, or when it produced zero successes in the window (a stale
    collector that stopped yielding while the cycle ran).
    """
    result = {code: SiteHealth(site_code=code) for code in monitored_sites}
    monitored = set(monitored_sites)

    for site_code, status, error_message in window_rows:
        if site_code not in monitored:
            continue
        health = result[site_code]
        if status == "success":
            health.success_count += 1
        elif status == "failed":
            health.failed_count += 1
            if not health.sample_error and error_message:
                health.sample_error = _first_line(error_message)

    for code, health in result.items():
        health.last_success_at = last_success_map.get(code)
        if health.failed_count >= max(1, failed_threshold):
            health.flagged = True
            health.reasons.append(f"실패 {health.failed_count}건")
        if health.success_count == 0:
            health.flagged = True
            health.reasons.append("성공 0건(수집 중단 의심)")

    return result


def load_window_rows(
    session: Session,
    cutoff_utc: datetime,
    monitored_sites: Sequence[str],
) -> list[tuple[str, str, str]]:
    stmt = (
        select(Job.site_code, Job.status, Job.error_message)
        .where(Job.site_code.in_(list(monitored_sites)))
        .where(Job.finished_at.is_not(None))
        .where(Job.finished_at >= cutoff_utc)
    )
    return [
        (str(site_code), str(status), error_message or "")
        for site_code, status, error_message in session.execute(stmt).all()
    ]


def load_last_success_map(
    session: Session,
    monitored_sites: Sequence[str],
) -> dict[str, datetime | None]:
    stmt = (
        select(Job.site_code, func.max(Job.finished_at))
        .where(Job.site_code.in_(list(monitored_sites)))
        .where(Job.status == "success")
        .group_by(Job.site_code)
    )
    result: dict[str, datetime | None] = {code: None for code in monitored_sites}
    for site_code, last_success in session.execute(stmt).all():
        result[str(site_code)] = last_success
    return result


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "없음"
    return value.strftime("%Y-%m-%d %H:%M") + " UTC"


def evaluate_ai_health(
    window_rows: Iterable[tuple[str, str | None]],
    last_done_at: datetime | None,
    min_failed: int = 2,
) -> AiHealth:
    """Flag an AI outage: evaluations were attempted in the window but none succeeded.

    ``window_rows`` items are ``(status, failure_reason)`` for evaluations updated
    inside the window. A window with no evaluations is normal (no candidates) and
    is never flagged; ``min_failed`` keeps a single transient failure quiet.
    """
    health = AiHealth(last_done_at=last_done_at)
    for status, failure_reason in window_rows:
        if status == "done":
            health.done_count += 1
        elif status == "failed":
            health.failed_count += 1
            if not health.sample_error and failure_reason:
                health.sample_error = _first_line(failure_reason)
    if health.done_count == 0 and health.failed_count >= max(1, min_failed):
        health.flagged = True
        health.reasons.append(f"평가 {health.failed_count}건 전부 실패(성공 0건)")
    return health


def load_ai_window_rows(session: Session, cutoff: datetime) -> list[tuple[str, str | None]]:
    stmt = select(NoticeAiEvaluation.status, NoticeAiEvaluation.failure_reason).where(
        NoticeAiEvaluation.updated_at >= cutoff
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def load_ai_last_done(session: Session) -> datetime | None:
    return session.execute(
        select(func.max(NoticeAiEvaluation.updated_at)).where(
            NoticeAiEvaluation.status == "done"
        )
    ).scalar()


def build_ai_report(
    session: Session,
    settings,
    window_hours: int,
    now_utc: datetime | None = None,
) -> AiHealth:
    cutoff = (now_utc or datetime.utcnow()) - timedelta(hours=window_hours)
    return evaluate_ai_health(
        load_ai_window_rows(session, cutoff),
        load_ai_last_done(session),
        min_failed=int(getattr(settings, "ai_health_min_failed", 2)),
    )


def format_ai_alert(health: AiHealth, window_hours: int, now: datetime | None = None) -> str:
    stamp = (now or datetime.utcnow()).strftime("%Y-%m-%d %H:%M")
    reason = ", ".join(health.reasons) if health.reasons else "이상 감지"
    lines = [
        f":rotating_light: *AI 평가 장애 의심* (최근 {window_hours}시간 기준, {stamp} UTC)",
        "• %s | 성공 %d · 실패 %d | 마지막 성공: %s"
        % (reason, health.done_count, health.failed_count, _fmt_dt(health.last_done_at)),
    ]
    if health.sample_error:
        lines.append(f"    ↳ 최근 오류: {health.sample_error}")
    lines.append("    ↳ 게이트웨이 /healthz는 정상이어도 모델 호출은 실패할 수 있습니다. 모델·인증 설정을 확인하세요.")
    return "\n".join(lines)


def format_health_alert(
    unhealthy: Sequence[SiteHealth],
    window_hours: int,
    now: datetime | None = None,
) -> str:
    stamp = (now or datetime.utcnow()).strftime("%Y-%m-%d %H:%M")
    lines = [
        f":rotating_light: *수집기 안정성 경고* (최근 {window_hours}시간 기준, {stamp} UTC)",
    ]
    for health in unhealthy:
        reason = ", ".join(health.reasons) if health.reasons else "이상 감지"
        lines.append(
            "• *%s* — %s | 성공 %d · 실패 %d | 마지막 성공: %s"
            % (
                health.site_code,
                reason,
                health.success_count,
                health.failed_count,
                _fmt_dt(health.last_success_at),
            )
        )
        if health.sample_error:
            lines.append(f"    ↳ 최근 오류: {health.sample_error}")
    return "\n".join(lines)


def build_report(
    session: Session,
    settings,
    window_hours: int | None = None,
    now_utc: datetime | None = None,
) -> dict[str, SiteHealth]:
    now_utc = now_utc or datetime.utcnow()
    window_hours = int(
        window_hours
        if window_hours is not None
        else getattr(settings, "collector_health_window_hours", 24)
    )
    failed_threshold = int(getattr(settings, "collector_health_failed_threshold", 1))
    monitored = resolve_monitored_sites(settings)
    if not monitored:
        return {}
    cutoff = now_utc - timedelta(hours=window_hours)
    window_rows = load_window_rows(session, cutoff, monitored)
    last_success_map = load_last_success_map(session, monitored)
    return evaluate_health(monitored, window_rows, last_success_map, failed_threshold)


def run_collector_health_check(
    session_factory,
    notifier,
    settings,
    window_hours: int | None = None,
    dry_run: bool = False,
    force: bool = False,
    now_utc: datetime | None = None,
) -> dict:
    """Compute per-site health and alert Slack when unhealthy.

    Returns a summary dict. Sends a Slack message only when there is at least one
    unhealthy site (``problem_only`` policy), unless ``force`` is set. ``dry_run``
    never sends to Slack.
    """
    if not getattr(settings, "collector_health_enabled", True):
        logger.info("collector health check disabled")
        return {"enabled": False, "unhealthy": []}

    effective_window = int(
        window_hours
        if window_hours is not None
        else getattr(settings, "collector_health_window_hours", 24)
    )

    with session_factory() as session:
        report = build_report(
            session,
            settings,
            window_hours=effective_window,
            now_utc=now_utc,
        )

    ai_health: AiHealth | None = None
    if getattr(settings, "ai_health_enabled", True):
        try:
            with session_factory() as session:
                ai_health = build_ai_report(
                    session, settings, effective_window, now_utc=now_utc
                )
        except Exception:
            # An AI-monitor failure must never block the collector alert.
            logger.exception("ai health evaluation failed")

    unhealthy = [health for health in report.values() if health.flagged]
    ai_flagged = bool(ai_health and ai_health.flagged)
    summary = {
        "enabled": True,
        "window_hours": effective_window,
        "monitored": sorted(report.keys()),
        "unhealthy": [health.site_code for health in unhealthy],
        "ai_unhealthy": ai_flagged,
    }
    if ai_health is not None:
        logger.info(
            "ai-health done=%d failed=%d flagged=%s last_done=%s",
            ai_health.done_count,
            ai_health.failed_count,
            ai_flagged,
            _fmt_dt(ai_health.last_done_at),
        )

    for code in sorted(report.keys()):
        health = report[code]
        logger.info(
            "collector-health site=%s success=%d failed=%d flagged=%s last_success=%s",
            code,
            health.success_count,
            health.failed_count,
            health.flagged,
            _fmt_dt(health.last_success_at),
        )

    should_send = bool(unhealthy) or ai_flagged or force
    if dry_run or not should_send:
        if not unhealthy and not ai_flagged:
            logger.info("collector-health OK, no alert sent")
        summary["sent"] = False
        return summary

    channel_id = (
        getattr(settings, "collector_health_channel_id", "")
        or getattr(settings, "slack_command_channel_id", "")
        or getattr(settings, "slack_briefing_channel_id", "")
    )
    if not channel_id:
        logger.warning("collector-health alert skipped: no Slack channel configured")
        summary["sent"] = False
        return summary

    parts = []
    if unhealthy or force or not ai_flagged:
        parts.append(
            format_health_alert(
                unhealthy or list(report.values()),
                effective_window,
                now=now_utc,
            )
        )
    if ai_flagged:
        parts.append(format_ai_alert(ai_health, effective_window, now=now_utc))
    message = "\n\n".join(parts)
    try:
        ts = notifier.send_text(channel_id, message)
        summary["sent"] = True
        summary["message_ts"] = ts
        logger.info(
            "collector-health alert sent sites=%s ai=%s", summary["unhealthy"], ai_flagged
        )
    except Exception:
        logger.exception("collector-health alert send failed")
        summary["sent"] = False
    return summary
