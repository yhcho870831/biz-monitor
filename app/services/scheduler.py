from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import or_, select

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from app.models import Notice, SlackShare
from app.repositories.jobs import (
    claim_next_pending_job,
    create_job,
    has_retry_job,
    heartbeat_running_jobs,
    mark_job_failed,
    mark_job_success,
    peek_next_pending_site_code,
    requeue_stale_running_jobs,
    summarize_job_counts,
)
from app.repositories.attachments import list_attachments_for_notice_ids, upsert_notice_attachment
from app.repositories.ai_evaluations import (
    get_latest_ai_evaluation,
    list_latest_ai_evaluations_for_notice_ids,
    mark_ai_recommendations_posted,
)
from app.repositories.notices import delete_expired_notices, upsert_notice
from app.repositories.shares import already_shared, record_file_share, record_share
from app.repositories.sites import list_enabled_site_keywords
from app.services.calendar import cleanup_inactive_saved_notices
from app.services.attachments import (
    attachment_category_label,
    download_priority_attachments,
    should_collect_attachments,
)
from app.services.business_scope_filter import excluded_scope_reason
from app.services.deadline import is_active_notice
from app.services.notice_meta import enrich_notice_candidate, format_priority_stars
from app.services.notice_amounts import ensure_candidate_amount
from app.services.notifier import (
    SITE_DISPLAY_NAMES,
    SlackNotifier,
    format_empty_scheduled,
    format_no_share_scheduled,
    get_notice_screenshot_path,
    format_site_notice_tables,
)
from app.services.recruitment_filter import is_recruitment_notice
from app.services.broad_search_terms import (
    is_broad_compound_token,
    is_exact_broad_compound_search_term,
)
from app.services.relevance import is_relevant
from app.services.summaries import format_summary_lines, generate_notice_summary, get_notice_summary_payload
from app.services.ai_relevance import (
    ai_relevance_enabled,
    ai_evaluation_payload,
    evaluate_notice_relevance,
    format_ai_evaluation_lines,
    should_share_ai_evaluation,
)
from app.services.screenshots import ensure_notice_screenshot
from app.types import NoticeCandidate
from app.utils import normalize_text

logger = logging.getLogger(__name__)

EXCLUDED_SCHEDULED_SITE_KEYWORDS: dict[str, set[str]] = {}
def _normalized_site_code_scope(site_codes: set[str] | list[str] | tuple[str, ...] | None) -> set[str] | None:
    if not site_codes:
        return None
    normalized = {
        normalize_text(site_code).lower()
        for site_code in site_codes
        if normalize_text(site_code)
    }
    return normalized or None


def _site_in_scope(site_code: str, site_scope: set[str] | None) -> bool:
    if site_scope is None:
        return True
    return normalize_text(site_code).lower() in site_scope


def _deferred_publish_enabled(settings) -> bool:
    return bool(getattr(settings, "slack_deferred_publish_enabled", False))


@dataclass
class ScheduledShareItem:
    notice_id: int
    candidate: NoticeCandidate
    channel_id: str
    share_type: str
    job_id: int


@dataclass
class AiEvaluatedItem:
    notice_id: int
    candidate: NoticeCandidate
    channel_id: str
    payload: dict | None


@dataclass
class AiCandidateItem:
    """A notice queued for trailing AI evaluation (decoupled from the body send)."""

    notice_id: int
    candidate: NoticeCandidate
    channel_id: str
    rule_passed: bool


@dataclass
class PendingRunStats:
    total_shared: int = 0
    total_search_results: int = 0
    failed_jobs: int = 0
    skipped_jobs: int = 0
    jobs_claimed: int = 0
    scheduled_jobs_claimed: int = 0
    ai_evaluations_used: int = 0
    site_ai_evaluations_used: dict[str, int] = field(default_factory=dict)
    ai_evaluated_items: list[AiEvaluatedItem] = field(default_factory=list)
    site_ai_candidates: dict[str, list[AiCandidateItem]] = field(default_factory=dict)
    site_search_counts: dict[str, int] = field(default_factory=dict)
    site_shared_counts: dict[str, int] = field(default_factory=dict)
    site_pending_shares: dict[str, dict[int, ScheduledShareItem]] = field(default_factory=dict)


def _web_base_url(settings) -> str:
    value = (settings.calendar_web_url or "").strip().rstrip("/")
    if value.endswith("/calendar"):
        return value[: -len("/calendar")]
    return value


def _attachment_download_url(settings, attachment_id: int) -> str:
    return f"{_web_base_url(settings)}/downloads/attachments/{attachment_id}"


def _is_excluded_scheduled_keyword(site_code: str, keyword: str | None) -> bool:
    normalized = normalize_text(keyword or "")
    return normalized in EXCLUDED_SCHEDULED_SITE_KEYWORDS.get(site_code, set())


def _title_link_overrides(settings, items: list[ScheduledShareItem], attachments) -> dict[str, str]:
    overrides: dict[str, str] = {}
    notice_to_key = {
        item.notice_id: (item.candidate.site_notice_key or "")
        for item in items
        if item.candidate.site_code in {"g2b", "d2b"}
    }
    for attachment in attachments:
        notice_key = notice_to_key.get(attachment.notice_id)
        if not notice_key or notice_key in overrides:
            continue
        overrides[notice_key] = _attachment_download_url(settings, attachment.id)
    return overrides


def _format_attachment_thread_message(settings, index_to_item: dict[int, ScheduledShareItem], attachments) -> str:
    if not attachments:
        return ""
    notice_to_index = {
        item.notice_id: index
        for index, item in index_to_item.items()
    }
    lines = ["첨부 다운로드"]
    for attachment in attachments:
        item_index = notice_to_index.get(attachment.notice_id)
        if item_index is None:
            continue
        lines.append(
            f"#{item_index} {attachment_category_label(attachment.attachment_category)}: "
            f"<{_attachment_download_url(settings, attachment.id)}|{attachment.attachment_name}>"
        )
    return "\n".join(lines) if len(lines) > 1 else ""


def enqueue_scheduled_jobs(session, settings, site_codes: set[str] | None = None) -> int:
    count = 0
    now = datetime.utcnow()
    site_scope = _normalized_site_code_scope(site_codes)
    for site_code, keyword in list_enabled_site_keywords(session):
        if not _site_in_scope(site_code, site_scope):
            continue
        if _is_excluded_scheduled_keyword(site_code, keyword):
            logger.info(
                "skipping excluded scheduled keyword site=%s keyword=%s",
                site_code,
                keyword,
            )
            continue
        create_job(
            session=session,
            job_type="scheduled",
            site_code=site_code,
            status="pending",
            run_after=now,
            keyword=keyword,
            search_term=keyword,
            channel_id=settings.slack_briefing_channel_id,
        )
        count += 1
    return count


def enqueue_retry_job(session, job, settings) -> bool:
    if job.attempt >= settings.max_retry_count:
        return False
    if has_retry_job(session, job):
        return False

    create_job(
        session=session,
        job_type="retry",
        site_code=job.site_code,
        status="pending",
        run_after=datetime.utcnow() + timedelta(minutes=settings.retry_delay_minutes),
        keyword=job.keyword,
        search_term=job.search_term,
        channel_id=job.channel_id,
        requested_by=job.requested_by,
        attempt=job.attempt + 1,
    )
    return True


def _min_priority_for_site(site_code: str) -> int:
    if site_code == "g2b":
        return 1
    return 0


def _scheduled_item_sort_key(item: ScheduledShareItem) -> tuple:
    candidate = item.candidate
    deadline = (
        candidate.deadline_at
        or candidate.open_at
        or candidate.start_at
        or datetime.max
    )
    return (-candidate.priority_score, deadline, candidate.title)


def _site_notice_limit(settings, site_code: str) -> int:
    if site_code == "g2b":
        return int(getattr(settings, "slack_max_notices_g2b", 30))
    return int(getattr(settings, "slack_max_notices_per_site", 40))


def _limit_site_items(
    settings,
    site_code: str,
    items: list[ScheduledShareItem],
    already_shared_count: int = 0,
) -> tuple[list[ScheduledShareItem], str]:
    sorted_items = sorted(items, key=_scheduled_item_sort_key)
    max_total = _site_notice_limit(settings, site_code)
    zero_star_limit = max(0, int(getattr(settings, "slack_max_zero_star_per_site", 10)))
    if max_total <= 0:
        max_total = len(sorted_items)
    remaining_total = max(0, max_total - max(0, already_shared_count))
    if remaining_total <= 0:
        return [], ""

    selected: list[ScheduledShareItem] = []
    zero_star_items: list[ScheduledShareItem] = []
    for item in sorted_items:
        if item.candidate.priority_score <= 0:
            zero_star_items.append(item)
            continue
        if len(selected) < remaining_total:
            selected.append(item)

    zero_selected_limit = min(zero_star_limit, max(0, remaining_total - len(selected)))
    selected.extend(zero_star_items[:zero_selected_limit])

    omitted_count = max(0, len(sorted_items) - len(selected))
    zero_omitted_count = max(0, len(zero_star_items) - zero_selected_limit)
    if omitted_count <= 0 and zero_omitted_count <= 0:
        return selected, ""

    note_parts = [
        "공유 후보 %s건 중 %s건만 표시합니다." % (len(sorted_items), len(selected))
    ]
    if zero_star_items:
        note_parts.append(
            "별 0개는 상위 %s건만 표시합니다." % zero_selected_limit
        )
    web_url = (getattr(settings, "calendar_web_url", "") or "").strip()
    if web_url:
        note_parts.append("<%s|전체 목록은 웹에서 확인>" % web_url)
    return selected, " ".join(note_parts)


def _sleep_after_slack_send(settings) -> None:
    interval = float(getattr(settings, "slack_site_send_interval_seconds", 0) or 0)
    if interval > 0:
        time.sleep(interval)


def _should_request_ai_relevance(settings, candidate: NoticeCandidate, rule_passed: bool) -> bool:
    if not ai_relevance_enabled(settings):
        return False
    if candidate.priority_score < int(getattr(settings, "ai_relevance_min_rule_score", 1)):
        return False
    if rule_passed:
        return True
    return bool(getattr(settings, "ai_relevance_evaluate_rule_failed", True))


def _can_request_site_ai_relevance(settings, stats: PendingRunStats, site_code: str) -> bool:
    max_per_site = int(getattr(settings, "ai_relevance_max_per_run", 10))
    if max_per_site <= 0:
        return False
    return stats.site_ai_evaluations_used.get(site_code, 0) < max_per_site


def _mark_site_ai_relevance_used(stats: PendingRunStats, site_code: str) -> None:
    stats.ai_evaluations_used += 1
    stats.site_ai_evaluations_used[site_code] = stats.site_ai_evaluations_used.get(site_code, 0) + 1


def _candidate_ai_order_key(item: tuple[NoticeCandidate, bool, str]) -> tuple:
    candidate = item[0]
    return (-candidate.priority_score, candidate.title)


def _candidate_search_text(candidate: NoticeCandidate) -> str:
    values = [
        candidate.title,
        candidate.organization,
        candidate.period_text,
        candidate.source_url,
    ]
    for value in (candidate.raw_payload or {}).values():
        if isinstance(value, (str, int, float)):
            values.append(str(value))
    return normalize_text(" ".join(value for value in values if value)).lower()


def _is_broad_compound_term(search_term: str | None) -> bool:
    return is_exact_broad_compound_search_term(search_term)


def _has_additional_site_keyword_match(candidate: NoticeCandidate, site_keywords: list[str], search_term: str | None) -> bool:
    search_text = _candidate_search_text(candidate)
    normalized_search_term = normalize_text(search_term or "").lower()
    for keyword in site_keywords:
        normalized_keyword = normalize_text(keyword).lower()
        if not normalized_keyword:
            continue
        if normalized_keyword == normalized_search_term:
            continue
        if is_broad_compound_token(keyword):
            continue
        if normalized_keyword in search_text:
            return True
    return False


def process_job(
    session,
    job,
    collector_registry,
    settings,
    stats: PendingRunStats,
    session_factory=None,
    notifier: SlackNotifier | None = None,
) -> int:
    collector = collector_registry.get(job.site_code)
    if collector is None:
        raise ValueError("Unsupported site: %s" % job.site_code)

    if _is_excluded_scheduled_keyword(job.site_code, job.search_term):
        logger.info(
            "skipping excluded queued job site=%s search_term=%s job_id=%s",
            job.site_code,
            job.search_term,
            job.id,
        )
        mark_job_success(session, job)
        return 0

    candidates = collector.search(job.search_term or job.keyword or "")
    search_count = len(candidates)

    prepared_candidates: list[tuple[NoticeCandidate, bool, str]] = []
    for candidate in candidates:
        if is_recruitment_notice(candidate):
            logger.info(
                "recruitment filtered site=%s search_term=%s title=%s",
                job.site_code,
                job.search_term,
                candidate.title,
            )
            continue
        scope_reason = excluded_scope_reason(candidate, settings)
        if scope_reason:
            logger.info(
                "scope filtered site=%s search_term=%s title=%s keyword=%s",
                job.site_code,
                job.search_term,
                candidate.title,
                scope_reason,
            )
            continue
        if not is_active_notice(candidate, datetime.utcnow()):
            logger.info(
                "deadline filtered site=%s search_term=%s title=%s",
                job.site_code,
                job.search_term,
                candidate.title,
            )
            continue

        enrich_notice_candidate(session, candidate)
        passed, reason = is_relevant(session, candidate, search_term=job.search_term)
        if passed and _is_broad_compound_term(job.search_term):
            site_keywords = [kw for sc, kw in list_enabled_site_keywords(session) if sc == job.site_code]
            if not _has_additional_site_keyword_match(candidate, site_keywords, job.search_term):
                passed = False
                reason = "broad_compound_without_site_keyword"
        logger.info(
            "relevance site=%s search_term=%s title=%s passed=%s reason=%s",
            job.site_code,
            job.search_term,
            candidate.title,
            passed,
            reason,
        )
        prepared_candidates.append((candidate, passed, reason))

    for candidate, passed, _reason in sorted(prepared_candidates, key=_candidate_ai_order_key):
        if candidate.priority_score >= 3 and job.site_code in {"g2b", "d2b"}:
            try:
                ensure_notice_screenshot(candidate)
            except Exception:
                logger.exception(
                    "screenshot capture failed site=%s title=%s",
                    job.site_code,
                    candidate.title,
                )
        notice = upsert_notice(session, candidate)
        channel_id = job.channel_id or settings.slack_briefing_channel_id
        if already_shared(session, notice.id, channel_id):
            continue

        if should_collect_attachments(candidate):
            try:
                for attachment in download_priority_attachments(candidate, settings):
                    upsert_notice_attachment(
                        session,
                        notice_id=notice.id,
                        site_code=candidate.site_code,
                        attachment_name=attachment.attachment_name,
                        attachment_category=attachment.attachment_category,
                        priority_rank=attachment.priority_rank,
                        stored_path=attachment.stored_path,
                        source_url=attachment.source_url,
                        mime_type=attachment.mime_type,
                        file_size=attachment.file_size,
                    )
            except Exception:
                logger.exception(
                    "attachment download failed site=%s title=%s",
                    candidate.site_code,
                    candidate.title,
                )
        try:
            generate_notice_summary(session, settings, notice, candidate)
        except Exception:
            logger.exception(
                "summary generation failed site=%s title=%s",
                candidate.site_code,
                candidate.title,
            )

        # AI relevance is intentionally NOT evaluated here. It is a slow,
        # timeout-prone call (gateway timeout up to AI_RELEVANCE_TIMEOUT_SECONDS).
        # Keeping it out of the body critical path guarantees notices are shared
        # to Slack even when AI evaluation later fails or times out. Eligible
        # notices are collected and evaluated in a trailing aggregator step once
        # the site's body has already been sent.
        if _should_request_ai_relevance(settings, candidate, passed):
            stats.site_ai_candidates.setdefault(job.site_code, []).append(
                AiCandidateItem(
                    notice_id=notice.id,
                    candidate=candidate,
                    channel_id=channel_id,
                    rule_passed=passed,
                )
            )

        if settings.enable_relevance_filter and not passed:
            continue

        if candidate.priority_score < _min_priority_for_site(job.site_code):
            logger.info(
                "priority filtered site=%s search_term=%s title=%s score=%s",
                job.site_code,
                job.search_term,
                candidate.title,
                candidate.priority_score,
            )
            continue

        if ensure_candidate_amount(candidate):
            enrich_notice_candidate(session, candidate)
            notice = upsert_notice(session, candidate)

        site_bucket = stats.site_pending_shares.setdefault(job.site_code, {})
        if notice.id in site_bucket:
            continue

        site_bucket[notice.id] = ScheduledShareItem(
            notice_id=notice.id,
            candidate=candidate,
            channel_id=channel_id,
            share_type=job.job_type,
            job_id=job.id,
        )
        if (
            _deferred_publish_enabled(settings)
            and not bool(getattr(settings, "slack_backfill_only", False))
            and job.job_type in {"scheduled", "retry"}
        ):
            record_share(
                session,
                notice.id,
                channel_id,
                "",
                job.job_type,
                job.id,
            )
        if session_factory is not None:
            _maybe_flush_g2b_pending_shares(
                session_factory,
                notifier,
                stats,
                settings,
                job.site_code,
            )

    mark_job_success(session, job)
    return search_count


def _requeue_stale_jobs(session_factory, settings) -> int:
    stale_minutes = int(getattr(settings, "job_running_stale_minutes", 0) or 0)
    if stale_minutes <= 0:
        return 0
    stale_before = datetime.utcnow() - timedelta(minutes=stale_minutes)
    with session_factory() as session:
        return requeue_stale_running_jobs(session, stale_before)


def _make_worker_id(site_scope: set[str] | None) -> str:
    """Stable-ish identity for a worker process: host/pid/scope + random suffix."""
    scope = "-".join(sorted(site_scope)) if site_scope else "all"
    return f"{socket.gethostname()}:{os.getpid()}:{scope}:{uuid.uuid4().hex[:8]}"


class _HeartbeatThread:
    """Background thread that keeps the running job's ``heartbeat_at`` fresh.

    This is what makes stale recovery safe: a long-but-alive job (e.g. a slow
    g2b collector search) keeps emitting heartbeats from this thread even while
    the main thread is blocked inside the search, so it never crosses the stale
    threshold. Only a crashed/stopped worker lets the heartbeat go cold.
    """

    def __init__(self, session_factory, worker_id: str, interval_seconds: float):
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._interval = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="job-heartbeat", daemon=True
        )

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with self._session_factory() as session:
                    heartbeat_running_jobs(session, self._worker_id)
            except Exception:  # pragma: no cover - heartbeat must never crash the worker
                logger.exception("heartbeat update failed worker_id=%s", self._worker_id)

    def start(self) -> "_HeartbeatThread":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 1)

    def __enter__(self) -> "_HeartbeatThread":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()


def _should_flush_site_pending_shares(
    stats: PendingRunStats,
    settings,
    site_code: str,
    next_site_code: str | None,
) -> bool:
    if next_site_code != site_code:
        return True
    if site_code != "g2b":
        return False

    threshold = int(getattr(settings, "slack_g2b_early_flush_count", 0) or 0)
    if threshold <= 0:
        return False
    pending_count = len(stats.site_pending_shares.get(site_code, {}))
    return pending_count >= threshold


def _flush_site_pending_shares(
    session_factory,
    notifier: SlackNotifier | None,
    stats: PendingRunStats,
    settings,
    site_code: str,
    *,
    reason: str,
) -> int:
    if notifier is None:
        return 0
    if _deferred_publish_enabled(settings):
        logger.info(
            "deferred publish enabled; keeping site shares queued site=%s count=%s reason=%s",
            site_code,
            len(stats.site_pending_shares.get(site_code, {})),
            reason,
        )
        return 0
    pending_count = len(stats.site_pending_shares.get(site_code, {}))
    if pending_count <= 0:
        return 0
    logger.info(
        "flushing pending site shares site=%s count=%s reason=%s",
        site_code,
        pending_count,
        reason,
    )
    shared = send_grouped_site_messages(
        session_factory,
        notifier,
        stats,
        settings,
        site_codes={site_code},
        clear_sent=True,
    )
    stats.total_shared += shared
    return shared


def _maybe_flush_g2b_pending_shares(
    session_factory,
    notifier: SlackNotifier | None,
    stats: PendingRunStats,
    settings,
    site_code: str,
) -> int:
    if site_code != "g2b":
        return 0
    threshold = int(getattr(settings, "slack_g2b_early_flush_count", 0) or 0)
    if threshold <= 0:
        return 0
    pending_count = len(stats.site_pending_shares.get(site_code, {}))
    if pending_count < threshold:
        return 0
    return _flush_site_pending_shares(
        session_factory,
        notifier,
        stats,
        settings,
        site_code,
        reason=f"g2b_threshold_{threshold}",
    )


def run_pending_jobs(
    session_factory,
    collector_registry,
    notifier_or_settings,
    settings=None,
    site_codes: set[str] | None = None,
) -> PendingRunStats:
    notifier = None if settings is None else notifier_or_settings
    settings = notifier_or_settings if settings is None else settings
    site_scope = _normalized_site_code_scope(site_codes)
    stale_requeued = _requeue_stale_jobs(session_factory, settings)
    if stale_requeued:
        logger.warning(
            "requeued stale running jobs count=%s older_than_minutes=%s",
            stale_requeued,
            getattr(settings, "job_running_stale_minutes", 0),
        )
    stats = PendingRunStats(
        site_search_counts={
            site_code: 0
            for site_code, enabled in settings.site_enabled.items()
            if enabled and _site_in_scope(site_code, site_scope)
        }
    )

    # A background heartbeat keeps this worker's running job alive in the DB so
    # stale recovery never requeues a long-but-alive job (see requeue logic).
    worker_id = _make_worker_id(site_scope)
    heartbeat = _HeartbeatThread(
        session_factory,
        worker_id,
        getattr(settings, "worker_heartbeat_interval_seconds", 30),
    ).start()
    try:
        _drain_pending_jobs(
            session_factory,
            collector_registry,
            notifier,
            settings,
            stats,
            site_scope,
            worker_id,
        )
    finally:
        heartbeat.stop()

    return stats


def _drain_pending_jobs(
    session_factory,
    collector_registry,
    notifier: SlackNotifier | None,
    settings,
    stats: PendingRunStats,
    site_scope: set[str] | None,
    worker_id: str,
) -> None:
    while True:
        with session_factory() as session:
            job = claim_next_pending_job(
                session,
                datetime.utcnow(),
                site_codes=site_scope,
                worker_id=worker_id,
            )
            if job is None:
                break
            stats.jobs_claimed += 1
            if job.job_type == "scheduled":
                stats.scheduled_jobs_claimed += 1

            try:
                search_count = process_job(
                    session,
                    job,
                    collector_registry,
                    settings,
                    stats,
                    session_factory=session_factory,
                    notifier=notifier,
                )
                stats.total_search_results += search_count
                stats.site_search_counts[job.site_code] = (
                    stats.site_search_counts.get(job.site_code, 0) + search_count
                )
            except NotImplementedError:
                job.status = "skipped"
                job.finished_at = datetime.utcnow()
                job.error_message = "collector_not_implemented"
                session.commit()
                stats.skipped_jobs += 1
            except Exception as exc:
                logger.exception(
                    "job failed site=%s search_term=%s job_id=%s",
                    job.site_code,
                    job.search_term,
                    job.id,
                )
                mark_job_failed(session, job, str(exc))
                enqueue_retry_job(session, job, settings)
                stats.failed_jobs += 1

            if notifier is not None:
                next_site_code = peek_next_pending_site_code(
                    session,
                    datetime.utcnow(),
                    site_codes=site_scope,
                )
                if _should_flush_site_pending_shares(
                    stats,
                    settings,
                    job.site_code,
                    next_site_code,
                ):
                    reason = (
                        f"site_change_to_{next_site_code or 'none'}"
                        if next_site_code != job.site_code
                        else f"g2b_threshold_{getattr(settings, 'slack_g2b_early_flush_count', 0)}"
                    )
                    _flush_site_pending_shares(
                        session_factory,
                        notifier,
                        stats,
                        settings,
                        job.site_code,
                        reason=reason,
                    )

    if notifier is not None:
        remaining_site_codes = {
            site_code
            for site_code, items in stats.site_pending_shares.items()
            if items and _site_in_scope(site_code, site_scope)
        }
        for site_code in sorted(remaining_site_codes):
            _flush_site_pending_shares(
                session_factory,
                notifier,
                stats,
                settings,
                site_code,
                reason="run_complete",
            )

    # Trailing AI aggregator: every site body has already been sent above, so a
    # slow or failing AI gateway can no longer delay (or block) notice delivery.
    if notifier is not None and not _deferred_publish_enabled(settings):
        ai_site_codes = sorted(
            site_code
            for site_code, candidates in stats.site_ai_candidates.items()
            if candidates and _site_in_scope(site_code, site_scope)
        )
        for site_code in ai_site_codes:
            _run_site_ai_trailing(
                session_factory,
                notifier,
                stats,
                settings,
                site_code,
            )
        if stats.ai_evaluated_items:
            send_ai_recommendation_result(notifier, stats, settings)

    # Only emit the "no results / nothing shared" summary for real scheduled
    # batches, never for retry-only or manual follow-up batches.
    if (
        notifier is not None
        and stats.scheduled_jobs_claimed > 0
        and not _deferred_publish_enabled(settings)
    ):
        _send_batch_summary(notifier, stats, settings, site_scope)


def _run_site_ai_trailing(
    session_factory,
    notifier: SlackNotifier | None,
    stats: PendingRunStats,
    settings,
    site_code: str,
) -> None:
    """Evaluate AI relevance for one site and accumulate it for one final Slack post.

    Runs only after the site body has been sent. AI failures/timeouts are recorded
    by ``evaluate_notice_relevance`` and never propagate, so a stuck gateway leaves
    an "AI 미완료" trail without blocking the batch.
    """
    candidates = stats.site_ai_candidates.pop(site_code, [])
    if notifier is None or not ai_relevance_enabled(settings) or not candidates:
        return

    ordered = sorted(
        candidates,
        key=lambda item: (-item.candidate.priority_score, item.candidate.title),
    )
    evaluated: list[AiEvaluatedItem] = []
    with session_factory() as session:
        for item in ordered:
            if not _can_request_site_ai_relevance(settings, stats, site_code):
                break
            notice = session.get(Notice, item.notice_id)
            if notice is None:
                continue
            payload = evaluate_notice_relevance(session, settings, notice, item.candidate)
            _mark_site_ai_relevance_used(stats, site_code)
            evaluated.append(
                AiEvaluatedItem(
                    notice_id=item.notice_id,
                    candidate=item.candidate,
                    channel_id=item.channel_id,
                    payload=payload,
                )
            )
    if not evaluated:
        return
    stats.ai_evaluated_items.extend(evaluated)


def _send_batch_summary(
    notifier: SlackNotifier,
    stats: PendingRunStats,
    settings,
    site_scope: set[str] | None,
) -> None:
    """Post the per-scope "no results / nothing shared" summary after a batch."""
    if stats.failed_jobs or stats.skipped_jobs:
        return
    if stats.total_search_results != 0 and stats.total_shared != 0:
        return
    try:
        run_at = (
            datetime.now(ZoneInfo(settings.app_timezone))
            if ZoneInfo is not None
            else datetime.now()
        )
    except Exception:  # pragma: no cover
        run_at = datetime.now()
    site_counts = [
        (
            SITE_DISPLAY_NAMES.get(site_code, site_code.upper()),
            stats.site_search_counts.get(site_code, 0),
        )
        for site_code, enabled in settings.site_enabled.items()
        if enabled and _site_in_scope(site_code, site_scope)
    ]
    if stats.total_search_results == 0:
        message = format_empty_scheduled(run_at, site_counts)
    else:
        message = format_no_share_scheduled(run_at, site_counts)
    notifier.send_text(settings.slack_briefing_channel_id, message)
    _sleep_after_slack_send(settings)


def send_grouped_site_messages(
    session_factory,
    notifier: SlackNotifier,
    stats: PendingRunStats,
    settings,
    site_codes: set[str] | None = None,
    clear_sent: bool = False,
) -> int:
    total_shared = 0
    sent_site_codes: set[str] = set()
    with session_factory() as session:
        target_site_codes = sorted(site_codes or stats.site_pending_shares.keys())
        for site_code in target_site_codes:
            raw_items = list(stats.site_pending_shares.get(site_code, {}).values())
            already_shared_count = stats.site_shared_counts.get(site_code, 0)
            site_limit = _site_notice_limit(settings, site_code)
            if site_limit > 0 and already_shared_count >= site_limit:
                if clear_sent:
                    sent_site_codes.add(site_code)
                continue
            items, header_note = _limit_site_items(
                settings,
                site_code,
                raw_items,
                already_shared_count=already_shared_count,
            )
            if not items:
                if clear_sent and raw_items:
                    sent_site_codes.add(site_code)
                continue
            site_attachments = list_attachments_for_notice_ids(
                session,
                [item.notice_id for item in items],
            )
            messages = format_site_notice_tables(
                site_code,
                [item.candidate for item in items],
                title_link_overrides=_title_link_overrides(settings, items, site_attachments),
                header_note=header_note,
            )
            if not messages:
                continue

            index_to_item = {
                index + 1: item
                for index, item in enumerate(sorted(items, key=_scheduled_item_sort_key))
            }
            chunk_size = 8
            for chunk_offset, message in enumerate(messages, start=0):
                ts = notifier.send_text(items[0].channel_id, message)
                _sleep_after_slack_send(settings)
                start_index = chunk_offset * chunk_size + 1
                end_index = start_index + chunk_size
                chunk_items = [
                    index_to_item[index]
                    for index in range(start_index, min(end_index, len(index_to_item) + 1))
                ]
                try:
                    for item in chunk_items:
                        record_share(
                            session,
                            item.notice_id,
                            item.channel_id,
                            ts,
                            item.share_type,
                            item.job_id,
                            commit=False,
                        )
                    session.commit()
                except Exception:
                    session.rollback()
                    logger.exception(
                        "share record failed after message send site=%s channel=%s ts=%s",
                        site_code,
                        items[0].channel_id,
                        ts,
                    )
                    raise

                file_records_pending = False
                for index in range(start_index, min(end_index, len(index_to_item) + 1)):
                    item = index_to_item[index]
                    screenshot_path = get_notice_screenshot_path(item.candidate)
                    if (
                        item.candidate.site_code in {"d2b", "g2b"}
                        and item.candidate.priority_score >= 3
                        and screenshot_path
                    ):
                        site_label = SITE_DISPLAY_NAMES.get(
                            item.candidate.site_code,
                            item.candidate.site_code.upper(),
                        )
                        file_id = notifier.send_file(
                            item.channel_id,
                            screenshot_path,
                            title=f"{site_label} 상세 캡처 - {item.candidate.title}",
                            initial_comment=f"#{index} 상세 캡처",
                            thread_ts=ts,
                        )
                        record_file_share(
                            session,
                            item.notice_id,
                            item.channel_id,
                            file_id,
                            thread_ts=ts,
                            commit=False,
                        )
                        _sleep_after_slack_send(settings)
                        file_records_pending = True
                    total_shared += 1
                if file_records_pending:
                    try:
                        session.commit()
                    except Exception:
                        session.rollback()
                        logger.exception(
                            "file share record failed site=%s channel=%s ts=%s",
                            site_code,
                            items[0].channel_id,
                            ts,
                        )
                        raise
                attachment_links = _format_attachment_thread_message(
                    settings,
                    {index: index_to_item[index] for index in range(start_index, min(end_index, len(index_to_item) + 1))},
                    [
                        attachment
                        for attachment in site_attachments
                        if attachment.notice_id in {item.notice_id for item in chunk_items}
                    ],
                )
                if attachment_links:
                    notifier.send_text(items[0].channel_id, attachment_links, thread_ts=ts)
                    _sleep_after_slack_send(settings)
                summary_lines: list[str] = []
                summaries_by_notice = {
                    item["notice_id"]: item
                    for item in [
                        get_notice_summary_payload(session, item.notice_id)
                        for item in chunk_items
                    ]
                    if item is not None
                }
                ai_evaluations_by_notice = {
                    notice_id: ai_evaluation_payload(evaluation)
                    for notice_id, evaluation in list_latest_ai_evaluations_for_notice_ids(
                        session,
                        [item.notice_id for item in chunk_items],
                    ).items()
                }
                for index in range(start_index, min(end_index, len(index_to_item) + 1)):
                    item = index_to_item[index]
                    ai_lines = format_ai_evaluation_lines(
                        ai_evaluations_by_notice.get(item.notice_id),
                        prefix=f"#{index}",
                    )
                    if ai_lines:
                        summary_lines.append("\n".join(ai_lines))
                    lines = format_summary_lines(
                        summaries_by_notice.get(item.notice_id, {}),
                        prefix=f"#{index}",
                    )
                    if lines:
                        summary_lines.append("\n".join(lines))
                if summary_lines:
                    notifier.send_text(
                        items[0].channel_id,
                        "요약\n" + "\n\n".join(summary_lines),
                        thread_ts=ts,
                    )
                    _sleep_after_slack_send(settings)
            stats.site_shared_counts[site_code] = already_shared_count + len(items)
            sent_site_codes.add(site_code)
    if clear_sent:
        for site_code in sent_site_codes:
            stats.site_pending_shares.pop(site_code, None)
    return total_shared


def _notice_raw_payload(notice: Notice) -> dict:
    raw_json = str(notice.raw_payload_json or "").strip()
    if not raw_json:
        return {}
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _notice_candidate_from_notice(notice: Notice) -> NoticeCandidate:
    raw_payload = _notice_raw_payload(notice)
    amount_value = raw_payload.get("amount_value")
    try:
        amount_value = int(amount_value) if amount_value not in {None, ""} else None
    except (TypeError, ValueError):
        amount_value = None
    priority_score = raw_payload.get("priority_score")
    try:
        priority_score = int(priority_score) if priority_score not in {None, ""} else 0
    except (TypeError, ValueError):
        priority_score = 0
    return NoticeCandidate(
        site_code=notice.site_code,
        site_notice_key=notice.site_notice_key,
        title=notice.title,
        source_url=notice.source_url,
        organization=notice.organization,
        notice_no=notice.notice_no,
        reference_no=notice.reference_no,
        start_at=notice.start_at,
        deadline_at=notice.deadline_at,
        open_at=notice.open_at,
        period_text=notice.period_text,
        raw_payload=raw_payload,
        amount_value=amount_value,
        notice_tag=raw_payload.get("notice_tag"),
        priority_score=priority_score,
    )


def _pending_slack_share_rows(session, settings) -> list[tuple[SlackShare, Notice]]:
    stmt = (
        select(SlackShare, Notice)
        .join(Notice, Notice.id == SlackShare.notice_id)
        .where(SlackShare.channel_id == settings.slack_briefing_channel_id)
        .where(or_(SlackShare.message_ts.is_(None), SlackShare.message_ts == ""))
        .order_by(SlackShare.shared_at.asc(), SlackShare.id.asc())
    )
    return list(session.execute(stmt).all())


def _deferred_ai_cutoff(settings) -> datetime:
    cutoff = datetime.utcnow() - timedelta(hours=36)
    raw_since = (getattr(settings, "slack_deferred_publish_since", "") or "").strip()
    if not raw_since:
        return cutoff
    normalized = raw_since.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        logger.warning("invalid SLACK_DEFERRED_PUBLISH_SINCE=%s", raw_since)
        return cutoff
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return max(cutoff, parsed)


def publish_deferred_scheduled_notices(
    session_factory,
    notifier: SlackNotifier,
    settings,
) -> PendingRunStats:
    """Publish queued scheduled notices and one combined AI recommendation message."""

    stats = PendingRunStats()
    if not _deferred_publish_enabled(settings):
        logger.info("deferred publish disabled; nothing to publish")
        return stats
    with session_factory() as session:
        rows = _pending_slack_share_rows(session, settings)
        for share, notice in rows:
            candidate = _notice_candidate_from_notice(notice)
            enrich_notice_candidate(session, candidate)
            stats.site_pending_shares.setdefault(notice.site_code, {})[notice.id] = (
                ScheduledShareItem(
                    notice_id=notice.id,
                    candidate=candidate,
                    channel_id=share.channel_id,
                    share_type=share.share_type,
                    job_id=share.job_id or 0,
                )
            )

    stats.total_shared = send_grouped_site_messages(
        session_factory,
        notifier,
        stats,
        settings,
        clear_sent=True,
    )
    _send_deferred_ai_recommendation_result(session_factory, notifier, settings)
    logger.info(
        "deferred publish completed queued=%s shared=%s",
        len(rows),
        stats.total_shared,
    )
    return stats


def prepare_deferred_ai_evaluations(
    session_factory,
    settings,
    *,
    limit: int | None = None,
) -> int:
    """Evaluate AI relevance for queued or previously incomplete posted notices."""

    if not ai_relevance_enabled(settings):
        return 0
    max_count = int(limit or getattr(settings, "ai_relevance_max_per_run", 30) or 30)
    cutoff = _deferred_ai_cutoff(settings)
    evaluated = 0
    with session_factory() as session:
        stmt = (
            select(SlackShare, Notice)
            .join(Notice, Notice.id == SlackShare.notice_id)
            .where(SlackShare.channel_id == settings.slack_briefing_channel_id)
            .where(SlackShare.shared_at >= cutoff)
            .order_by(SlackShare.shared_at.asc(), SlackShare.id.asc())
        )
        rows = list(session.execute(stmt).all())
        candidates: list[tuple[NoticeCandidate, Notice]] = []
        seen_notice_ids: set[int] = set()
        for _share, notice in rows:
            if notice.id in seen_notice_ids:
                continue
            seen_notice_ids.add(notice.id)
            latest = get_latest_ai_evaluation(session, notice.id)
            if latest is not None and latest.status == "done":
                continue
            candidate = _notice_candidate_from_notice(notice)
            enrich_notice_candidate(session, candidate)
            if candidate.priority_score < int(
                getattr(settings, "ai_relevance_min_rule_score", 1)
            ):
                continue
            candidates.append((candidate, notice))
        candidates.sort(key=lambda item: _candidate_ai_order_key((item[0], True, "")))
        for candidate, notice in candidates[:max_count]:
            payload = evaluate_notice_relevance(session, settings, notice, candidate)
            if payload is not None:
                evaluated += 1
    logger.info("deferred AI prepare completed evaluated=%s limit=%s", evaluated, max_count)
    return evaluated


def _latest_unposted_ai_items(session, settings) -> tuple[list[AiEvaluatedItem], list[int]]:
    cutoff = _deferred_ai_cutoff(settings)
    stmt = (
        select(SlackShare, Notice)
        .join(Notice, Notice.id == SlackShare.notice_id)
        .where(SlackShare.channel_id == settings.slack_briefing_channel_id)
        .where(SlackShare.shared_at >= cutoff)
        .order_by(SlackShare.shared_at.asc(), SlackShare.id.asc())
    )
    items: list[AiEvaluatedItem] = []
    posted_evaluation_ids: list[int] = []
    seen_notice_ids: set[int] = set()
    for share, notice in session.execute(stmt).all():
        if notice.id in seen_notice_ids:
            continue
        seen_notice_ids.add(notice.id)
        candidate = _notice_candidate_from_notice(notice)
        enrich_notice_candidate(session, candidate)
        if candidate.priority_score < int(getattr(settings, "ai_relevance_min_rule_score", 1)):
            continue
        evaluation = get_latest_ai_evaluation(session, notice.id)
        if (
            evaluation is not None
            and evaluation.ai_recommendation_posted_at is not None
        ):
            continue
        payload = ai_evaluation_payload(evaluation)
        items.append(
            AiEvaluatedItem(
                notice_id=notice.id,
                candidate=candidate,
                channel_id=share.channel_id,
                payload=payload,
            )
        )
        if evaluation is not None and evaluation.status == "done":
            posted_evaluation_ids.append(evaluation.id)
    return items, posted_evaluation_ids


def _send_deferred_ai_recommendation_result(session_factory, notifier, settings) -> int:
    if not ai_relevance_enabled(settings):
        return 0
    with session_factory() as session:
        items, posted_evaluation_ids = _latest_unposted_ai_items(session, settings)
        if not items:
            return 0
        message = _format_ai_recommendation_result(items, settings)
        notifier.send_text(settings.slack_briefing_channel_id, message)
        _sleep_after_slack_send(settings)
        mark_ai_recommendations_posted(
            session,
            posted_evaluation_ids,
            posted_at=datetime.utcnow(),
        )
        logger.info(
            "deferred AI recommendation sent items=%s marked=%s",
            len(items),
            len(posted_evaluation_ids),
        )
        return len(items)


def _dedupe_ai_items(items: list[AiEvaluatedItem], settings) -> list[AiEvaluatedItem]:
    by_notice_id: dict[int, AiEvaluatedItem] = {}
    for item in items:
        existing = by_notice_id.get(item.notice_id)
        if existing is None:
            by_notice_id[item.notice_id] = item
            continue
        if should_share_ai_evaluation(item.payload, settings) and not should_share_ai_evaluation(
            existing.payload,
            settings,
        ):
            by_notice_id[item.notice_id] = item
    return list(by_notice_id.values())


def _ai_action_label(action: str) -> str:
    return {
        "bid": "\uc785\ucc30\uac80\ud1a0",
        "review": "\uac80\ud1a0",
        "watch": "\uad00\ucc30",
        "ignore": "\uc81c\uc678",
        "participate": "\uc785\ucc30\uac80\ud1a0",
    }.get(action.strip().lower(), action or "\ubbf8\uae30\uc7ac")


def _candidate_deadline_text(candidate: NoticeCandidate) -> str:
    deadline = candidate.deadline_at or candidate.open_at or candidate.start_at
    if deadline is None:
        return "\ubbf8\uae30\uc7ac"
    return deadline.strftime("%Y-%m-%d %H:%M")


def _clip_slack_text(value: str, limit: int = 180) -> str:
    value = " ".join((value or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "\u2026"


def _format_ai_recommendation_result(
    items: list[AiEvaluatedItem],
    settings,
    site_code: str | None = None,
) -> str:
    items = _dedupe_ai_items(items, settings)
    recommendations = [
        item
        for item in items
        if should_share_ai_evaluation(item.payload, settings)
    ]
    failures = [
        item
        for item in items
        if not item.payload or item.payload.get("status") != "done"
    ]
    recommendations = sorted(
        recommendations,
        key=lambda item: (
            -int((item.payload or {}).get("fit_score") or 0),
            item.candidate.title,
        ),
    )

    header = "*AI \ucd94\ucc9c \uacb0\uacfc*"
    if site_code:
        header = "*AI \ucd94\ucc9c \uacb0\uacfc - %s*" % SITE_DISPLAY_NAMES.get(
            site_code, site_code.upper()
        )
    lines = [
        header,
        "AI \ud3c9\uac00: %s\uac74 / \ucd94\ucc9c: %s\uac74 / \ubbf8\uc644\ub8cc: %s\uac74"
        % (len(items), len(recommendations), len(failures)),
    ]
    if not items:
        lines.append("AI \ud3c9\uac00 \ub300\uc0c1 \uacf5\uace0\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.")
        return "\n".join(lines)
    if not recommendations:
        lines.append("AI \ucd94\ucc9c \uacf5\uace0\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.")
    for index, item in enumerate(recommendations, start=1):
        candidate = item.candidate
        payload = item.payload or {}
        score = payload.get("fit_score")
        action = _ai_action_label(str(payload.get("recommended_action") or ""))
        title = candidate.title
        if candidate.source_url:
            title = "<%s|%s>" % (candidate.source_url, candidate.title)
        summary = payload.get("summary_for_slack") or payload.get("reason") or "\ubbf8\uae30\uc7ac"
        lines.extend(
            [
                "",
                "%s. %s [%s] *AI %s\uc810 / %s*"
                % (
                    index,
                    format_priority_stars(candidate.priority_score),
                    SITE_DISPLAY_NAMES.get(candidate.site_code, candidate.site_code.upper()),
                    score if score is not None else "\ubbf8\ud655\uc778",
                    action,
                ),
                "   %s" % title,
                "   \uc785\ucc30\ub9c8\uac10: %s | \ubc1c\uc8fc\ucc98: %s"
                % (_candidate_deadline_text(candidate), candidate.organization or "\ubbf8\uae30\uc7ac"),
                "   \ucd94\ucc9c\uc0ac\uc720: %s" % _clip_slack_text(str(summary)),
            ]
        )
    if failures:
        lines.extend(
            [
                "",
                "\ucc38\uace0: AI \ud3c9\uac00 \ubbf8\uc644\ub8cc %s\uac74\uc740 \ub2e4\uc74c \uac8c\uc2dc \uc8fc\uae30\uc5d0 \ub2e4\uc2dc \ud3c9\uac00/\uac8c\uc2dc\ud569\ub2c8\ub2e4."
                % len(failures),
            ]
        )
    return "\n".join(lines)


def send_ai_recommendation_result(
    notifier: SlackNotifier,
    stats: PendingRunStats,
    settings,
) -> None:
    if not ai_relevance_enabled(settings):
        return
    notifier.send_text(
        settings.slack_briefing_channel_id,
        _format_ai_recommendation_result(stats.ai_evaluated_items, settings),
    )
    _sleep_after_slack_send(settings)


def run_scheduled_cycle(
    session_factory,
    collector_registry,
    notifier: SlackNotifier,
    settings,
    site_codes: set[str] | None = None,
):
    site_scope = _normalized_site_code_scope(site_codes)
    with session_factory() as session:
        deleted_saved_count = cleanup_inactive_saved_notices(session, datetime.utcnow())
    if deleted_saved_count:
        logger.info("deleted inactive saved notices count=%s", deleted_saved_count)

    with session_factory() as session:
        deleted_count = delete_expired_notices(session, slack_file_deleter=notifier.delete_file)
    if deleted_count:
        logger.info("deleted expired notices count=%s", deleted_count)

    with session_factory() as session:
        enqueue_scheduled_jobs(session, settings, site_codes=site_scope)
    _log_job_status_summary(session_factory, "after_enqueue", site_scope)
    stats = run_pending_jobs(
        session_factory,
        collector_registry,
        notifier,
        settings,
        site_codes=site_scope,
    )
    # The per-scope batch summary and per-site AI recommendations are emitted
    # inside run_pending_jobs (trailing aggregator), so nothing else to send here.
    return stats


def _log_job_status_summary(
    session_factory,
    phase: str,
    site_scope: set[str] | None = None,
) -> dict[str, int]:
    """Log a pending/running/failed/success/skipped snapshot for observability."""
    with session_factory() as session:
        counts = summarize_job_counts(session, site_codes=site_scope)
    logger.info(
        "job status summary phase=%s scope=%s pending=%s running=%s failed=%s success=%s skipped=%s retry=%s",
        phase,
        ",".join(sorted(site_scope)) if site_scope else "all",
        counts.get("pending", 0),
        counts.get("running", 0),
        counts.get("failed", 0),
        counts.get("success", 0),
        counts.get("skipped", 0),
        counts.get("retry", 0),
    )
    return counts


def enqueue_scheduled_cycle(
    session_factory,
    notifier: SlackNotifier,
    settings,
    site_codes: set[str] | None = None,
) -> int:
    """Enqueue-only scheduled cycle: maintenance + job creation, no processing.

    This is the scheduler's sole responsibility under the worker-based topology.
    Per-site workers (``run-worker-loop``) claim and process the jobs created here.
    """
    site_scope = _normalized_site_code_scope(site_codes)
    with session_factory() as session:
        deleted_saved_count = cleanup_inactive_saved_notices(session, datetime.utcnow())
    if deleted_saved_count:
        logger.info("deleted inactive saved notices count=%s", deleted_saved_count)

    with session_factory() as session:
        deleted_count = delete_expired_notices(session, slack_file_deleter=notifier.delete_file)
    if deleted_count:
        logger.info("deleted expired notices count=%s", deleted_count)

    with session_factory() as session:
        count = enqueue_scheduled_jobs(session, settings, site_codes=site_scope)
    logger.info(
        "enqueued scheduled jobs count=%s scope=%s",
        count,
        ",".join(sorted(site_scope)) if site_scope else "all",
    )
    _log_job_status_summary(session_factory, "after_enqueue", site_scope)
    return count
