from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import CalendarSavedNotice, Notice


def get_saved_notice_by_source_notice_id(
    session: Session,
    source_notice_id: int,
) -> CalendarSavedNotice | None:
    stmt = select(CalendarSavedNotice).where(
        CalendarSavedNotice.source_notice_id == source_notice_id
    )
    return session.execute(stmt).scalars().one_or_none()


def get_saved_notice(session: Session, saved_notice_id: int) -> CalendarSavedNotice | None:
    return session.get(CalendarSavedNotice, saved_notice_id)


def _payload_json(raw_payload: dict) -> str:
    return json.dumps(raw_payload, ensure_ascii=False, sort_keys=True)


def upsert_saved_notice_from_notice(
    session: Session,
    notice: Notice,
    site_name: str,
    selected_by: str,
    primary_deadline_at: datetime | None,
    deadline_confidence: str,
    priority_score: int,
    notice_tag: str | None,
    amount_text: str | None,
    amount_value: int | None,
    raw_payload: dict,
) -> CalendarSavedNotice:
    now = datetime.utcnow()
    saved = get_saved_notice_by_source_notice_id(session, notice.id)
    payload_json = _payload_json(raw_payload)

    if saved is None:
        saved = CalendarSavedNotice(
            source_notice_id=notice.id,
            site_id=None,
            site_code=notice.site_code,
            site_name=site_name,
            title=notice.title,
            organization=notice.organization,
            primary_deadline_at=primary_deadline_at,
            amount_text=amount_text,
            amount_value=amount_value,
            priority_score=priority_score,
            notice_tag=notice_tag,
            source_url=notice.source_url,
            raw_payload_json=payload_json,
            status="participating",
            owner_name=None,
            selected_at=now,
            deselected_at=None,
            updated_at=now,
            selected_by=selected_by,
            is_active=True,
            memo=None,
            origin_type="notice",
            deadline_confidence=deadline_confidence,
            legacy_year=None,
            import_batch_id=None,
        )
        session.add(saved)
        session.commit()
        session.refresh(saved)
        return saved

    saved.site_code = notice.site_code
    saved.site_name = site_name
    saved.title = notice.title
    saved.organization = notice.organization
    saved.primary_deadline_at = primary_deadline_at
    saved.amount_text = amount_text
    saved.amount_value = amount_value
    saved.priority_score = priority_score
    saved.notice_tag = notice_tag
    saved.source_url = notice.source_url
    saved.raw_payload_json = payload_json
    saved.status = "participating"
    saved.selected_by = selected_by
    saved.is_active = True
    saved.deselected_at = None
    saved.updated_at = now
    saved.origin_type = "notice"
    saved.deadline_confidence = deadline_confidence
    saved.legacy_year = None
    saved.import_batch_id = None
    session.commit()
    return saved


def create_manual_saved_notice(
    session: Session,
    *,
    title: str,
    organization: str | None,
    primary_deadline_at: datetime | None,
    amount_text: str | None,
    amount_value: int | None,
    priority_score: int,
    notice_tag: str | None,
    source_url: str,
    status: str,
    owner_name: str | None,
    memo: str | None,
    selected_by: str,
    deadline_confidence: str,
    raw_payload: dict,
) -> CalendarSavedNotice:
    now = datetime.utcnow()
    saved = CalendarSavedNotice(
        source_notice_id=None,
        site_id=None,
        site_code="manual",
        site_name="\uc9c1\uc811\ub4f1\ub85d",
        title=title,
        organization=organization,
        primary_deadline_at=primary_deadline_at,
        amount_text=amount_text,
        amount_value=amount_value,
        priority_score=priority_score,
        notice_tag=notice_tag,
        source_url=source_url,
        raw_payload_json=_payload_json(raw_payload),
        status=status,
        owner_name=owner_name,
        selected_at=now,
        deselected_at=None,
        updated_at=now,
        selected_by=selected_by,
        is_active=True,
        memo=memo,
        origin_type="manual",
        deadline_confidence=deadline_confidence,
        legacy_year=None,
        import_batch_id=None,
    )
    session.add(saved)
    session.commit()
    session.refresh(saved)
    return saved


def upsert_imported_saved_notice(
    session: Session,
    *,
    title: str,
    organization: str | None,
    site_code: str,
    site_name: str,
    primary_deadline_at: datetime | None,
    amount_text: str | None,
    amount_value: int | None,
    priority_score: int,
    notice_tag: str | None,
    source_url: str,
    status: str,
    owner_name: str | None,
    memo: str | None,
    selected_by: str,
    deadline_confidence: str,
    raw_payload: dict,
    legacy_year: int | None,
    import_batch_id: str | None,
) -> CalendarSavedNotice:
    stmt = select(CalendarSavedNotice).where(
        CalendarSavedNotice.origin_type == "imported",
        CalendarSavedNotice.title == title,
        CalendarSavedNotice.organization == organization,
        CalendarSavedNotice.legacy_year == legacy_year,
    )
    saved = session.execute(stmt).scalars().one_or_none()
    now = datetime.utcnow()
    payload_json = _payload_json(raw_payload)

    if saved is None:
        saved = CalendarSavedNotice(
            source_notice_id=None,
            site_id=None,
            site_code=site_code,
            site_name=site_name,
            title=title,
            organization=organization,
            primary_deadline_at=primary_deadline_at,
            amount_text=amount_text,
            amount_value=amount_value,
            priority_score=priority_score,
            notice_tag=notice_tag,
            source_url=source_url,
            raw_payload_json=payload_json,
            status=status,
            owner_name=owner_name,
            selected_at=now,
            deselected_at=None,
            updated_at=now,
            selected_by=selected_by,
            is_active=True,
            memo=memo,
            origin_type="imported",
            deadline_confidence=deadline_confidence,
            legacy_year=legacy_year,
            import_batch_id=import_batch_id,
        )
        session.add(saved)
        session.commit()
        session.refresh(saved)
        return saved

    saved.primary_deadline_at = primary_deadline_at
    saved.site_code = site_code
    saved.site_name = site_name
    saved.amount_text = amount_text
    saved.amount_value = amount_value
    saved.priority_score = priority_score
    saved.notice_tag = notice_tag
    saved.source_url = source_url
    saved.raw_payload_json = payload_json
    saved.status = status
    saved.owner_name = owner_name
    saved.updated_at = now
    saved.selected_by = selected_by
    saved.is_active = True
    saved.memo = memo
    saved.deadline_confidence = deadline_confidence
    saved.import_batch_id = import_batch_id
    session.commit()
    return saved


def deactivate_saved_notice_by_source_notice_id(
    session: Session,
    source_notice_id: int,
    selected_by: str,
) -> CalendarSavedNotice | None:
    saved = get_saved_notice_by_source_notice_id(session, source_notice_id)
    if saved is None:
        return None
    now = datetime.utcnow()
    saved.is_active = False
    saved.status = "inactive"
    saved.selected_by = selected_by
    saved.deselected_at = now
    saved.updated_at = now
    session.commit()
    return saved


def deactivate_saved_notice_by_id(
    session: Session,
    saved_notice_id: int,
    selected_by: str,
) -> CalendarSavedNotice | None:
    saved = get_saved_notice(session, saved_notice_id)
    if saved is None:
        return None
    now = datetime.utcnow()
    saved.is_active = False
    saved.status = "inactive"
    saved.selected_by = selected_by
    saved.deselected_at = now
    saved.updated_at = now
    session.commit()
    return saved


def list_saved_notices_for_month(
    session: Session,
    month_start: datetime,
    month_end: datetime,
) -> list[CalendarSavedNotice]:
    stmt = (
        select(CalendarSavedNotice)
        .where(CalendarSavedNotice.is_active.is_(True))
        .where(CalendarSavedNotice.priority_score > 0)
        .where(CalendarSavedNotice.primary_deadline_at.is_not(None))
        .where(CalendarSavedNotice.primary_deadline_at >= month_start)
        .where(CalendarSavedNotice.primary_deadline_at <= month_end)
        .order_by(
            CalendarSavedNotice.primary_deadline_at.asc(),
            CalendarSavedNotice.priority_score.desc(),
            CalendarSavedNotice.title.asc(),
        )
    )
    return list(session.execute(stmt).scalars().all())


def list_saved_notices_without_source_for_range(
    session: Session,
    range_start: datetime,
    range_end: datetime,
    q: str | None = None,
) -> list[CalendarSavedNotice]:
    stmt = (
        select(CalendarSavedNotice)
        .where(CalendarSavedNotice.source_notice_id.is_(None))
        .where(CalendarSavedNotice.is_active.is_(True))
        .where(CalendarSavedNotice.priority_score > 0)
        .where(
            or_(
                CalendarSavedNotice.primary_deadline_at.is_(None),
                and_(
                    CalendarSavedNotice.primary_deadline_at >= range_start,
                    CalendarSavedNotice.primary_deadline_at <= range_end,
                ),
            )
        )
    )
    if q:
        like_term = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                CalendarSavedNotice.title.ilike(like_term),
                CalendarSavedNotice.organization.ilike(like_term),
            )
        )
    stmt = stmt.order_by(
        CalendarSavedNotice.site_code.asc(),
        CalendarSavedNotice.title.asc(),
    )
    return list(session.execute(stmt).scalars().all())


def list_notice_rows_with_selection(
    session: Session,
    range_start: datetime,
    range_end: datetime,
    site_code: str | None = None,
    selected_only: bool = False,
    q: str | None = None,
):
    fallback_posted_start = range_start - timedelta(days=14)
    fallback_posted_end = range_end - timedelta(days=14)
    posted_at = Notice.start_at
    stmt = (
        select(Notice, CalendarSavedNotice)
        .outerjoin(
            CalendarSavedNotice,
            and_(
                CalendarSavedNotice.source_notice_id == Notice.id,
                CalendarSavedNotice.is_active.is_(True),
            ),
        )
        .where(
            or_(
                and_(
                    Notice.deadline_at.is_not(None),
                    Notice.deadline_at >= range_start,
                    Notice.deadline_at <= range_end,
                ),
                and_(
                    Notice.deadline_at.is_(None),
                    posted_at.is_not(None),
                    posted_at >= fallback_posted_start,
                    posted_at <= fallback_posted_end,
                ),
                CalendarSavedNotice.id.is_not(None),
            )
        )
    )

    if site_code:
        stmt = stmt.where(Notice.site_code == site_code)
    if selected_only:
        stmt = stmt.where(CalendarSavedNotice.id.is_not(None))
    if q:
        like_term = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Notice.title.ilike(like_term), Notice.organization.ilike(like_term))
        )

    stmt = stmt.order_by(Notice.site_code.asc(), Notice.deadline_at.asc(), Notice.title.asc())
    return list(session.execute(stmt).all())


def list_saved_notices_eligible_for_cleanup(
    session: Session,
    now: datetime,
) -> list[CalendarSavedNotice]:
    stmt = (
        select(CalendarSavedNotice)
        .where(CalendarSavedNotice.is_active.is_(False))
        .where(CalendarSavedNotice.primary_deadline_at.is_not(None))
        .where(CalendarSavedNotice.primary_deadline_at < now)
        .where(CalendarSavedNotice.deselected_at.is_not(None))
        .where(CalendarSavedNotice.deselected_at < now)
    )
    return list(session.execute(stmt).scalars().all())


def delete_saved_notices(session: Session, saved_notice_ids: list[int]) -> int:
    if not saved_notice_ids:
        return 0
    stmt = select(CalendarSavedNotice).where(CalendarSavedNotice.id.in_(saved_notice_ids))
    rows = list(session.execute(stmt).scalars().all())
    for row in rows:
        session.delete(row)
    session.commit()
    return len(rows)
