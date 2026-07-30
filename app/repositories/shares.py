from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notice, NoticeShareGuard, SlackFileShare, SlackShare


def _guard_identity(session: Session, notice_id: int) -> tuple[str, str] | None:
    notice = session.get(Notice, notice_id)
    if notice is None:
        return None
    return notice.site_code, notice.site_notice_key


def _upsert_share_guard(
    session: Session,
    notice_id: int,
    channel_id: str,
    *,
    message_ts: str = "",
    suppressed_at: datetime | None = None,
    suppressed_reason: str | None = None,
) -> None:
    identity = _guard_identity(session, notice_id)
    if identity is None:
        return
    site_code, site_notice_key = identity
    now = datetime.utcnow()

    guard = session.execute(
        select(NoticeShareGuard).where(
            NoticeShareGuard.site_code == site_code,
            NoticeShareGuard.site_notice_key == site_notice_key,
            NoticeShareGuard.channel_id == channel_id,
        )
    ).scalars().one_or_none()

    if guard is None:
        session.add(
            NoticeShareGuard(
                site_code=site_code,
                site_notice_key=site_notice_key,
                channel_id=channel_id,
                first_shared_at=now,
                last_message_ts=message_ts or None,
                suppressed_at=suppressed_at,
                suppressed_reason=suppressed_reason,
                updated_at=now,
            )
        )
        session.flush()
        return

    changed = False
    if message_ts and not guard.last_message_ts:
        guard.last_message_ts = message_ts
        changed = True
    if suppressed_at is not None and guard.suppressed_at is None:
        guard.suppressed_at = suppressed_at
        guard.suppressed_reason = suppressed_reason
        changed = True
    if changed:
        guard.updated_at = now
        session.flush()


def already_shared(session: Session, notice_id: int, channel_id: str) -> bool:
    """Whether this notice was ever queued, published or suppressed for a channel.

    The guard table is consulted in addition to ``slack_shares`` because
    retention cleanup deletes share rows along with their notice, and a notice
    that reappears in a later collection must not be re-queued just because its
    row aged out.
    """
    stmt = select(SlackShare.id).where(
        SlackShare.notice_id == notice_id,
        SlackShare.channel_id == channel_id,
    )
    if session.execute(stmt).first() is not None:
        return True

    identity = _guard_identity(session, notice_id)
    if identity is None:
        return False
    site_code, site_notice_key = identity
    guard_stmt = select(NoticeShareGuard.id).where(
        NoticeShareGuard.site_code == site_code,
        NoticeShareGuard.site_notice_key == site_notice_key,
        NoticeShareGuard.channel_id == channel_id,
    )
    return session.execute(guard_stmt).first() is not None


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
        _upsert_share_guard(session, notice_id, channel_id, message_ts=message_ts)
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
    _upsert_share_guard(session, notice_id, channel_id, message_ts=message_ts)
    if commit:
        session.commit()


def suppress_share(
    session: Session,
    share: SlackShare,
    reason: str,
    commit: bool = True,
) -> None:
    """Keep an ineligible queued share out of publication without deleting it."""
    if share.suppressed_at is None:
        share.suppressed_at = datetime.utcnow()
        share.suppressed_reason = reason[:255]
        session.flush()
    _upsert_share_guard(
        session,
        share.notice_id,
        share.channel_id,
        suppressed_at=share.suppressed_at,
        suppressed_reason=share.suppressed_reason,
    )
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
