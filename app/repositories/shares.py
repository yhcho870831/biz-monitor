from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notice, NoticeShareGuard, SlackFileShare, SlackShare


def _sqlite_datetime(value: object, fallback: datetime) -> datetime:
    """Convert a SQLite timestamp from a historical backup to a naive datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    raw = str(value or "").strip()
    if not raw:
        return fallback
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed.replace(tzinfo=None)


def backfill_share_guards_from_sqlite_backup(session: Session, backup_path: str | Path) -> int:
    """Create missing durable guards from a read-only SQLite backup.

    Retention may have deleted both a live notice and its ``slack_shares`` row
    before the guard table was introduced.  The live-schema migration cannot
    recover those identities, but a consistent SQLite backup can.  Existing
    guards always win, so the import is safe to repeat for every backup.
    """
    path = Path(backup_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("share-guard backup does not exist: %s" % path)

    uri = "file:%s?mode=ro&immutable=1" % path.as_posix()
    with sqlite3.connect(uri, uri=True) as backup:
        integrity = backup.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("share-guard backup integrity failed: %s" % path)

        tables = {
            row[0]
            for row in backup.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"notices", "slack_shares"}.issubset(tables):
            return 0

        share_columns = {
            row[1] for row in backup.execute("PRAGMA table_info(slack_shares)")
        }
        required_columns = {"notice_id", "channel_id", "shared_at"}
        if not required_columns.issubset(share_columns):
            return 0

        message_ts = "max(s.message_ts)" if "message_ts" in share_columns else "NULL"
        suppressed_at = (
            "max(s.suppressed_at)" if "suppressed_at" in share_columns else "NULL"
        )
        suppressed_reason = (
            "max(s.suppressed_reason)" if "suppressed_reason" in share_columns else "NULL"
        )
        rows = backup.execute(
            """
            SELECT n.site_code,
                   n.site_notice_key,
                   s.channel_id,
                   min(s.shared_at) AS first_shared_at,
                   %s AS last_message_ts,
                   %s AS suppressed_at,
                   %s AS suppressed_reason
              FROM slack_shares s
              JOIN notices n ON n.id = s.notice_id
             WHERE n.site_code IS NOT NULL
               AND n.site_notice_key IS NOT NULL
               AND s.channel_id IS NOT NULL
             GROUP BY n.site_code, n.site_notice_key, s.channel_id
            """
            % (message_ts, suppressed_at, suppressed_reason)
        ).fetchall()

    now = datetime.utcnow()
    created = 0
    for (
        site_code,
        site_notice_key,
        channel_id,
        first_shared_at,
        last_message_ts,
        suppressed_at,
        suppressed_reason,
    ) in rows:
        exists = session.execute(
            select(NoticeShareGuard.id).where(
                NoticeShareGuard.site_code == site_code,
                NoticeShareGuard.site_notice_key == site_notice_key,
                NoticeShareGuard.channel_id == channel_id,
            )
        ).first()
        if exists is not None:
            continue
        session.add(
            NoticeShareGuard(
                site_code=site_code,
                site_notice_key=site_notice_key,
                channel_id=channel_id,
                first_shared_at=_sqlite_datetime(first_shared_at, now),
                last_message_ts=str(last_message_ts or "").strip() or None,
                suppressed_at=(
                    _sqlite_datetime(suppressed_at, now)
                    if suppressed_at is not None
                    else None
                ),
                suppressed_reason=str(suppressed_reason or "").strip() or None,
                updated_at=now,
            )
        )
        created += 1
    session.flush()
    return created


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
