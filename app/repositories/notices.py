from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import CalendarSavedNotice, Notice, SlackFileShare, SlackShare
from app.repositories.attachments import delete_attachments_for_notice_ids, list_attachments_for_notice_ids
from app.repositories.ai_evaluations import delete_ai_evaluations_for_notice_ids
from app.repositories.shares import list_file_shares_for_notice_ids
from app.repositories.summaries import delete_notice_summaries_for_notice_ids, list_notice_summaries_for_notice_ids
from app.services.deadline import G2B_PRE_SPECIFICATION_STAGES
from app.types import NoticeCandidate
from app.utils import dumps_payload, parse_datetime


DEFAULT_NOTICE_RETENTION_DAYS = 30
G2B_MAIN_NOTICE_RETENTION_DAYS = 30


def upsert_notice(session: Session, candidate: NoticeCandidate) -> Notice:
    stmt = select(Notice).where(
        Notice.site_code == candidate.site_code,
        Notice.site_notice_key == candidate.site_notice_key,
    )
    existing = session.execute(stmt).scalars().first()
    now = datetime.utcnow()

    if existing is None:
        notice = Notice(
            site_code=candidate.site_code,
            site_notice_key=candidate.site_notice_key,
            title=candidate.title,
            organization=candidate.organization,
            notice_no=candidate.notice_no,
            reference_no=candidate.reference_no,
            start_at=candidate.start_at,
            deadline_at=candidate.deadline_at,
            open_at=candidate.open_at,
            period_text=candidate.period_text,
            source_url=candidate.source_url,
            raw_payload_json=dumps_payload(candidate.raw_payload),
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(notice)
        session.commit()
        session.refresh(notice)
        return notice

    existing.title = candidate.title
    existing.organization = candidate.organization
    existing.notice_no = candidate.notice_no
    existing.reference_no = candidate.reference_no
    existing.start_at = candidate.start_at
    existing.deadline_at = candidate.deadline_at
    existing.open_at = candidate.open_at
    existing.period_text = candidate.period_text
    existing.source_url = candidate.source_url
    existing.raw_payload_json = dumps_payload(candidate.raw_payload)
    existing.last_seen_at = now
    existing.updated_at = now
    session.commit()
    return existing


def delete_expired_notices(
    session: Session,
    now: datetime | None = None,
    retention_days: int = DEFAULT_NOTICE_RETENTION_DAYS,
    slack_file_deleter: Callable[[str], None] | None = None,
) -> int:
    reference_time = now or datetime.utcnow()
    cutoff = reference_time - timedelta(days=retention_days)
    protected_notice_ids = set(
        session.execute(
            select(CalendarSavedNotice.source_notice_id).where(
                CalendarSavedNotice.source_notice_id.is_not(None)
            )
        )
        .scalars()
        .all()
    )
    candidate_notices = list(
        session.execute(
            select(Notice).where(
                or_(
                    and_(Notice.deadline_at.is_not(None), Notice.deadline_at < cutoff),
                    Notice.deadline_at.is_(None),
                    func.coalesce(Notice.start_at, Notice.first_seen_at, Notice.created_at) < cutoff,
                )
            )
        ).scalars()
    )
    old_notices = [notice for notice in candidate_notices if _should_delete_notice(notice, reference_time, retention_days)]
    old_notices = [notice for notice in old_notices if notice.id not in protected_notice_ids]
    old_notice_ids = [notice.id for notice in old_notices]
    if not old_notice_ids:
        return 0

    for notice in old_notices:
        if not notice.raw_payload_json:
            continue
        try:
            raw_payload = json.loads(notice.raw_payload_json)
        except json.JSONDecodeError:
            continue
        screenshot_path = str(raw_payload.get("screenshot_path", "")).strip()
        if screenshot_path and os.path.exists(screenshot_path):
            try:
                os.remove(screenshot_path)
            except OSError:
                pass

    if slack_file_deleter is not None:
        for file_share in list_file_shares_for_notice_ids(session, old_notice_ids):
            try:
                slack_file_deleter(file_share.file_id)
            except Exception:
                pass

    for attachment in list_attachments_for_notice_ids(session, old_notice_ids):
        stored_path = str(attachment.stored_path or "").strip()
        if stored_path and os.path.exists(stored_path):
            try:
                os.remove(stored_path)
            except OSError:
                pass

    for summary in list_notice_summaries_for_notice_ids(session, old_notice_ids):
        extracted_path = str(summary.raw_extracted_text_path or "").strip()
        if extracted_path and os.path.exists(extracted_path):
            try:
                os.remove(extracted_path)
            except OSError:
                pass

    delete_attachments_for_notice_ids(session, old_notice_ids, commit=False)
    delete_ai_evaluations_for_notice_ids(session, old_notice_ids, commit=False)
    delete_notice_summaries_for_notice_ids(session, old_notice_ids, commit=False)
    session.execute(delete(SlackFileShare).where(SlackFileShare.notice_id.in_(old_notice_ids)))
    session.execute(delete(SlackShare).where(SlackShare.notice_id.in_(old_notice_ids)))
    session.execute(delete(Notice).where(Notice.id.in_(old_notice_ids)))
    session.commit()
    return len(old_notice_ids)


def _should_delete_notice(notice: Notice, reference_time: datetime, retention_days: int) -> bool:
    raw_payload = _notice_raw_payload(notice)
    cutoff = reference_time - timedelta(days=retention_days)

    if (
        notice.site_code == "g2b"
        and raw_payload.get("announcement_stage") not in G2B_PRE_SPECIFICATION_STAGES
    ):
        posted_at = (
            notice.start_at
            or _raw_payload_posted_at(raw_payload, notice.site_code)
            or notice.first_seen_at
            or notice.created_at
        )
        if posted_at is not None:
            if posted_at < (reference_time - timedelta(days=G2B_MAIN_NOTICE_RETENTION_DAYS)):
                return True

    if notice.deadline_at is not None:
        return notice.deadline_at < cutoff
    posted_at = _raw_payload_posted_at(raw_payload, notice.site_code)
    if posted_at is not None:
        max_age_days = (
            G2B_MAIN_NOTICE_RETENTION_DAYS
            if notice.site_code == "g2b"
            and raw_payload.get("announcement_stage") not in G2B_PRE_SPECIFICATION_STAGES
            else retention_days
        )
        return posted_at < (reference_time - timedelta(days=max_age_days))
    return (notice.first_seen_at or notice.created_at) < cutoff


def _notice_raw_payload(notice: Notice) -> dict:
    raw_json = str(notice.raw_payload_json or "").strip()
    if not raw_json:
        return {}
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _raw_payload_posted_at(raw_payload: dict, site_code: str = "") -> datetime | None:
    for key in ("posted_at", "prcsYmd"):
        parsed = parse_datetime(str(raw_payload.get(key) or "").strip())
        if parsed is not None:
            return parsed
    if site_code == "nia":
        for key in ("title", "body_excerpt"):
            parsed = parse_datetime(str(raw_payload.get(key) or "").strip())
            if parsed is not None:
                return parsed
    return None
