from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict

from sqlalchemy.orm import Session

from app.repositories.jobs import create_job
from app.repositories.notices import upsert_notice
from app.repositories.attachments import list_attachments_for_notice_id, upsert_notice_attachment
from app.repositories.shares import already_shared, record_share
from app.services.attachments import (
    attachment_category_label,
    download_priority_attachments,
    should_collect_attachments,
)
from app.services.business_scope_filter import excluded_scope_reason
from app.services.deadline import is_active_notice
from app.services.notice_meta import enrich_notice_candidate
from app.services.notifier import (
    SlackNotifier,
    format_empty_manual,
    format_manual_header,
    format_notice,
)
from app.services.recruitment_filter import is_recruitment_notice
from app.services.relevance import is_relevant
from app.services.summaries import format_summary_lines, generate_notice_summary, get_notice_summary_payload
from app.utils import now_kst
from app.services.ai_relevance import (
    ai_relevance_enabled,
    evaluate_notice_relevance,
    format_ai_evaluation_lines,
    should_share_ai_evaluation,
)

logger = logging.getLogger(__name__)


def run_manual_search(
    session: Session,
    collector_registry: Dict[str, object],
    notifier: SlackNotifier,
    site_code: str,
    term: str,
    channel_id: str,
    requested_by: str,
    use_relevance_filter: bool,
):
    def _attachment_title_url(notice_id: int) -> str | None:
        if settings is None:
            return None
        if site_code not in {"g2b", "d2b"}:
            return None
        base_url = (settings.calendar_web_url or "").rstrip("/")
        if base_url.endswith("/calendar"):
            base_url = base_url[: -len("/calendar")]
        attachments = list_attachments_for_notice_id(session, notice_id)
        if not attachments:
            return None
        return f"{base_url}/downloads/attachments/{attachments[0].id}"

    site_code = site_code.lower()
    collector = collector_registry.get(site_code)
    if collector is None:
        raise ValueError("Unsupported site: %s" % site_code)

    job = create_job(
        session=session,
        job_type="manual",
        site_code=site_code,
        status="running",
        run_after=datetime.utcnow(),
        search_term=term,
        channel_id=channel_id,
        requested_by=requested_by,
    )

    results = collector.search(term)
    settings = getattr(notifier, "settings", None)
    filtered = []
    for candidate in results:
        if is_recruitment_notice(candidate):
            logger.info("recruitment filtered site=%s title=%s", site_code, candidate.title)
            continue
        scope_reason = excluded_scope_reason(candidate, settings)
        if scope_reason:
            logger.info("scope filtered site=%s title=%s keyword=%s", site_code, candidate.title, scope_reason)
            continue
        if not is_active_notice(candidate, now_kst()):
            logger.info("deadline filtered site=%s title=%s", site_code, candidate.title)
            continue
        enrich_notice_candidate(session, candidate)
        passed, reason = is_relevant(session, candidate)
        logger.info(
            "relevance site=%s title=%s passed=%s reason=%s",
            site_code,
            candidate.title,
            passed,
            reason,
        )
        if (
            use_relevance_filter
            and not passed
            and not bool(getattr(settings, "ai_relevance_enabled", False))
        ):
            continue
        filtered.append(candidate)

    if not filtered:
        ts = notifier.send_text(channel_id, format_empty_manual(site_code, term))
        logger.info("manual search empty site=%s term=%s ts=%s", site_code, term, ts)
        job.status = "success"
        job.finished_at = datetime.utcnow()
        session.commit()
        return {"job_id": job.id, "count": 0}

    notifier.send_text(channel_id, format_manual_header(site_code, term, len(filtered)))

    shared_count = 0
    for candidate in filtered:
        notice = upsert_notice(session, candidate)
        if already_shared(session, notice.id, channel_id):
            continue
        if should_collect_attachments(candidate):
            try:
                if settings is not None:
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
                logger.exception("manual attachment download failed site=%s title=%s", site_code, candidate.title)
        summary_payload = None
        ai_payload = None
        if settings is not None:
            try:
                summary_payload = generate_notice_summary(session, settings, notice, candidate)
            except Exception:
                logger.exception("manual summary generation failed site=%s title=%s", site_code, candidate.title)
            try:
                if ai_relevance_enabled(settings) and candidate.priority_score >= int(
                    getattr(settings, "ai_relevance_min_rule_score", 1)
                ):
                    ai_payload = evaluate_notice_relevance(session, settings, notice, candidate)
                    if should_share_ai_evaluation(ai_payload, settings) and candidate.priority_score < 1:
                        candidate.priority_score = 1
                    if should_share_ai_evaluation(ai_payload, settings):
                        candidate.raw_payload["ai_recommended"] = "true"
            except Exception:
                logger.exception("manual AI relevance failed site=%s title=%s", site_code, candidate.title)
            if (
                use_relevance_filter
                and not is_relevant(session, candidate)[0]
                and not should_share_ai_evaluation(ai_payload, settings)
            ):
                continue
        ts = notifier.send_text(
            channel_id,
            format_notice(candidate, title_link_override=_attachment_title_url(notice.id)),
        )
        record_share(session, notice.id, channel_id, ts, "manual", job.id)
        if settings is not None:
            attachment_lines = []
            base_url = (settings.calendar_web_url or "").rstrip("/")
            if base_url.endswith("/calendar"):
                base_url = base_url[: -len("/calendar")]
            for attachment in list_attachments_for_notice_id(session, notice.id):
                attachment_lines.append(
                    f"{attachment_category_label(attachment.attachment_category)}: "
                    f"<{base_url}/downloads/attachments/{attachment.id}|{attachment.attachment_name}>"
                )
            if attachment_lines:
                notifier.send_text(channel_id, "첨부 다운로드\n" + "\n".join(attachment_lines), thread_ts=ts)
            if not summary_payload:
                summary_payload = get_notice_summary_payload(session, notice.id)
            ai_lines = format_ai_evaluation_lines(ai_payload)
            if ai_lines:
                notifier.send_text(channel_id, "\n".join(ai_lines), thread_ts=ts)
            summary_lines = format_summary_lines(summary_payload or {})
            if summary_lines:
                notifier.send_text(channel_id, "요약\n" + "\n".join(summary_lines), thread_ts=ts)
        shared_count += 1

    job.status = "success"
    job.finished_at = datetime.utcnow()
    session.commit()
    return {"job_id": job.id, "count": shared_count}
