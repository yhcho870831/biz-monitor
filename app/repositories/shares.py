from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notice, SlackFileShare, SlackShare


def already_shared(session: Session, notice_id: int, channel_id: str) -> bool:
    stmt = select(SlackShare).where(
        SlackShare.notice_id == notice_id,
        SlackShare.channel_id == channel_id,
    )
    return session.execute(stmt).scalars().first() is not None


def record_share(
    session: Session,
    notice_id: int,
    channel_id: str,
    message_ts: str,
    share_type: str,
    job_id: int,
    commit: bool = True,
) -> None:
    existing = session.execute(
        select(SlackShare).where(
            SlackShare.notice_id == notice_id,
            SlackShare.channel_id == channel_id,
        )
    ).scalars().one_or_none()
    if existing is not None:
        if message_ts and not existing.message_ts:
            existing.message_ts = message_ts
            session.flush()
            if commit:
                session.commit()
        return
    share = SlackShare(
        notice_id=notice_id,
        channel_id=channel_id,
        message_ts=message_ts,
        share_type=share_type,
        job_id=job_id,
        shared_at=datetime.utcnow(),
    )
    session.add(share)
    session.flush()
    if commit:
        session.commit()


def record_file_share(
    session: Session,
    notice_id: int,
    channel_id: str,
    file_id: str,
    thread_ts: str = "",
    commit: bool = True,
) -> None:
    if not file_id:
        return
    share = SlackFileShare(
        notice_id=notice_id,
        channel_id=channel_id,
        file_id=file_id,
        thread_ts=thread_ts or None,
        shared_at=datetime.utcnow(),
    )
    session.add(share)
    session.flush()
    if commit:
        session.commit()


def list_shared_notices(session: Session, channel_id: str) -> list[tuple[Notice, SlackShare]]:
    stmt = (
        select(Notice, SlackShare)
        .join(SlackShare, SlackShare.notice_id == Notice.id)
        .where(SlackShare.channel_id == channel_id)
        .order_by(SlackShare.shared_at.desc())
    )
    return list(session.execute(stmt).all())


def list_file_shares_for_notice_ids(
    session: Session,
    notice_ids: list[int],
) -> list[SlackFileShare]:
    if not notice_ids:
        return []
    stmt = select(SlackFileShare).where(SlackFileShare.notice_id.in_(notice_ids))
    return list(session.execute(stmt).scalars())
