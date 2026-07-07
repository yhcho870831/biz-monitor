from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import NoticeAttachment


def get_attachment(session: Session, attachment_id: int) -> NoticeAttachment | None:
    return session.get(NoticeAttachment, attachment_id)


def list_attachments_for_notice_ids(
    session: Session,
    notice_ids: list[int],
) -> list[NoticeAttachment]:
    if not notice_ids:
        return []
    stmt = (
        select(NoticeAttachment)
        .where(NoticeAttachment.notice_id.in_(notice_ids))
        .order_by(
            NoticeAttachment.notice_id.asc(),
            NoticeAttachment.priority_rank.asc(),
            NoticeAttachment.attachment_name.asc(),
        )
    )
    return list(session.execute(stmt).scalars().all())


def list_attachments_for_notice_id(
    session: Session,
    notice_id: int,
) -> list[NoticeAttachment]:
    return list_attachments_for_notice_ids(session, [notice_id])


def upsert_notice_attachment(
    session: Session,
    *,
    notice_id: int,
    site_code: str,
    attachment_name: str,
    attachment_category: str,
    priority_rank: int,
    stored_path: str,
    source_url: str | None,
    mime_type: str | None,
    file_size: int | None,
    is_summary_source: bool = False,
    commit: bool = True,
) -> NoticeAttachment:
    stmt = select(NoticeAttachment).where(
        NoticeAttachment.notice_id == notice_id,
        NoticeAttachment.attachment_name == attachment_name,
    )
    existing = session.execute(stmt).scalars().one_or_none()
    now = datetime.utcnow()

    if existing is None:
        existing = NoticeAttachment(
            notice_id=notice_id,
            site_code=site_code,
            attachment_name=attachment_name,
            attachment_category=attachment_category,
            priority_rank=priority_rank,
            stored_path=stored_path,
            source_url=source_url,
            mime_type=mime_type,
            file_size=file_size,
            is_summary_source=is_summary_source,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    else:
        existing.attachment_category = attachment_category
        existing.priority_rank = priority_rank
        existing.stored_path = stored_path
        existing.source_url = source_url
        existing.mime_type = mime_type
        existing.file_size = file_size
        existing.is_summary_source = is_summary_source
        existing.updated_at = now

    session.flush()
    if commit:
        session.commit()
    return existing


def clear_summary_source_flags(
    session: Session,
    notice_id: int,
    *,
    commit: bool = True,
) -> None:
    stmt = select(NoticeAttachment).where(NoticeAttachment.notice_id == notice_id)
    for row in session.execute(stmt).scalars().all():
        row.is_summary_source = False
    session.flush()
    if commit:
        session.commit()


def set_summary_source_attachment(
    session: Session,
    notice_id: int,
    attachment_id: int | None,
    *,
    commit: bool = True,
) -> NoticeAttachment | None:
    attachments = list_attachments_for_notice_id(session, notice_id)
    selected: NoticeAttachment | None = None
    for attachment in attachments:
        attachment.is_summary_source = attachment.id == attachment_id if attachment_id is not None else False
        if attachment.is_summary_source:
            selected = attachment
    session.flush()
    if commit:
        session.commit()
    return selected


def delete_attachments_for_notice_ids(
    session: Session,
    notice_ids: list[int],
    *,
    commit: bool = True,
) -> int:
    if not notice_ids:
        return 0
    stmt = delete(NoticeAttachment).where(NoticeAttachment.notice_id.in_(notice_ids))
    result = session.execute(stmt)
    if commit:
        session.commit()
    return int(result.rowcount or 0)
