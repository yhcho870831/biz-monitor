from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NoticeSummary


def get_notice_summary(session: Session, notice_id: int) -> NoticeSummary | None:
    stmt = select(NoticeSummary).where(NoticeSummary.notice_id == notice_id)
    return session.execute(stmt).scalars().one_or_none()


def list_notice_summaries_for_notice_ids(
    session: Session,
    notice_ids: list[int],
) -> list[NoticeSummary]:
    if not notice_ids:
        return []
    stmt = select(NoticeSummary).where(NoticeSummary.notice_id.in_(notice_ids))
    return list(session.execute(stmt).scalars().all())


def delete_notice_summaries_for_notice_ids(
    session: Session,
    notice_ids: list[int],
    *,
    commit: bool = True,
) -> int:
    if not notice_ids:
        return 0
    stmt = select(NoticeSummary).where(NoticeSummary.notice_id.in_(notice_ids))
    rows = list(session.execute(stmt).scalars().all())
    deleted = len(rows)
    for row in rows:
        session.delete(row)
    session.flush()
    if commit:
        session.commit()
    return deleted


def upsert_notice_summary(
    session: Session,
    *,
    notice_id: int,
    attachment_id: int | None,
    source_type: str,
    summary_status: str,
    failure_reason: str | None,
    purpose: str | None,
    core_tasks: str | None,
    required_performance: str | None,
    quantitative_targets: str | None,
    period_text: str | None,
    raw_extracted_text_path: str | None,
    commit: bool = True,
) -> NoticeSummary:
    existing = get_notice_summary(session, notice_id)
    now = datetime.utcnow()
    if existing is None:
        existing = NoticeSummary(
            notice_id=notice_id,
            attachment_id=attachment_id,
            source_type=source_type,
            summary_status=summary_status,
            failure_reason=failure_reason,
            purpose=purpose,
            core_tasks=core_tasks,
            required_performance=required_performance,
            quantitative_targets=quantitative_targets,
            period_text=period_text,
            raw_extracted_text_path=raw_extracted_text_path,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    else:
        existing.attachment_id = attachment_id
        existing.source_type = source_type
        existing.summary_status = summary_status
        existing.failure_reason = failure_reason
        existing.purpose = purpose
        existing.core_tasks = core_tasks
        existing.required_performance = required_performance
        existing.quantitative_targets = quantitative_targets
        existing.period_text = period_text
        existing.raw_extracted_text_path = raw_extracted_text_path
        existing.updated_at = now
    session.flush()
    if commit:
        session.commit()
    return existing
