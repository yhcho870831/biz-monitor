from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bootstrap import SITE_NAMES
from app.models import CalendarSavedNotice, Notice
from app.repositories.attachments import list_attachments_for_notice_id
from app.repositories.calendar_saved_notices import (
    create_manual_saved_notice,
    deactivate_saved_notice_by_id,
    deactivate_saved_notice_by_source_notice_id,
    delete_saved_notices,
    get_saved_notice,
    list_notice_rows_with_selection,
    list_saved_notices_eligible_for_cleanup,
    list_saved_notices_for_month,
    list_saved_notices_without_source_for_range,
    upsert_imported_saved_notice,
    upsert_saved_notice_from_notice,
)
from app.services.summaries import get_notice_summary_payload
from app.services.ai_relevance import get_ai_evaluation_payload
from app.services.attachments import attachment_category_label
from app.services.notice_meta import enrich_notice_candidate
from app.types import NoticeCandidate

TAG_LABELS = {
    "research_service": "연구용역",
    "goods_purchase": "물품구매",
    "production_service": "제작용역",
    "general_service": "일반용역",
    "other": "기타",
}

ORIGIN_TYPE_LABELS = {
    "notice": "원본공고",
    "manual": "직접등록",
    "imported": "과거이관",
}

DEADLINE_CONFIDENCE_LABELS = {
    "exact": "확정",
    "estimated": "추정",
    "unknown": "확인필요",
}

STATUS_LABELS = {
    "participating": "참여 중",
    "inactive": "비활성",
    "closed": "종료",
}

ALLOWED_STATUSES = {"participating", "inactive", "closed"}
ALLOWED_DEADLINE_CONFIDENCES = {"exact", "estimated", "unknown"}
ALLOWED_NOTICE_TAGS = {
    "research_service",
    "goods_purchase",
    "production_service",
    "general_service",
    "other",
}


def now_in_timezone(timezone_name: str) -> datetime:
    try:
        return (
            datetime.now(ZoneInfo(timezone_name))
            if ZoneInfo is not None
            else datetime.now()
        )
    except Exception:  # pragma: no cover
        if timezone_name == "Asia/Seoul":
            return datetime.now(timezone(timedelta(hours=9), name="KST"))
        return datetime.now()


def get_notice_list_range(now: datetime) -> tuple[datetime, datetime]:
    now = now.replace(tzinfo=None)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=0)
    return start, end


def get_calendar_range(now: datetime) -> tuple[datetime, datetime]:
    now = now.replace(tzinfo=None)
    start = now.replace(
        year=now.year - 3,
        month=1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = now.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=0)
    return start, end


def month_bounds(month: str) -> tuple[datetime, datetime]:
    month_start = datetime.strptime(month, "%Y-%m")
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    month_end = next_month - timedelta(seconds=1)
    return month_start, month_end


def validate_calendar_month(month: str, now: datetime) -> tuple[datetime, datetime]:
    now = now.replace(tzinfo=None)
    month_start, month_end = month_bounds(month)
    allowed_start, allowed_end = get_calendar_range(now)
    if month_start < allowed_start or month_start > allowed_end:
        raise ValueError("month out of allowed range")
    return month_start, month_end


def priority_label(score: int) -> str:
    bounded = max(0, min(score or 0, 3))
    return ("★" * bounded) + ("☆" * (3 - bounded))


def tag_label(tag: str | None) -> str:
    return TAG_LABELS.get(tag or "other", TAG_LABELS["other"])


def origin_type_label(origin_type: str | None) -> str:
    return ORIGIN_TYPE_LABELS.get(origin_type or "notice", ORIGIN_TYPE_LABELS["notice"])


def deadline_confidence_label(deadline_confidence: str | None) -> str:
    return DEADLINE_CONFIDENCE_LABELS.get(
        deadline_confidence or "unknown",
        DEADLINE_CONFIDENCE_LABELS["unknown"],
    )


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "inactive", STATUS_LABELS["inactive"])


def amount_text(amount_value: int | None) -> str | None:
    if amount_value is None:
        return None
    return f"{amount_value:,}원"


def _canonical_amount(
    amount_value: int | None,
) -> tuple[int | None, str | None]:
    if amount_value is None:
        return None, None
    return int(amount_value), amount_text(int(amount_value))


def _load_payload(raw_payload_json: str | None) -> dict:
    if not raw_payload_json:
        return {}
    try:
        data = json.loads(raw_payload_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _validate_status(status: str) -> str:
    if status not in ALLOWED_STATUSES:
        raise ValueError("invalid status")
    return status


def _validate_deadline_confidence(deadline_confidence: str) -> str:
    if deadline_confidence not in ALLOWED_DEADLINE_CONFIDENCES:
        raise ValueError("invalid deadline_confidence")
    return deadline_confidence


def _normalize_notice_tag(notice_tag: str | None) -> str | None:
    if notice_tag is None or notice_tag == "":
        return None
    if notice_tag not in ALLOWED_NOTICE_TAGS:
        raise ValueError("invalid notice_tag")
    return notice_tag


def _notice_to_candidate(notice: Notice, session: Session) -> NoticeCandidate:
    candidate = NoticeCandidate(
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
        raw_payload=_load_payload(notice.raw_payload_json),
    )
    enrich_notice_candidate(session, candidate)
    return candidate


def _notice_primary_deadline(notice: Notice) -> tuple[datetime | None, str]:
    if notice.deadline_at is not None:
        return notice.deadline_at, "exact"

    posted_at = notice.start_at
    if posted_at is None:
        return None, "unknown"

    return posted_at + timedelta(days=14), "unknown"


def _naive_now(now: datetime) -> datetime:
    return now.replace(tzinfo=None)


def _saved_notice_effective_status(
    saved_notice: CalendarSavedNotice,
    now: datetime,
) -> str:
    if not saved_notice.is_active:
        return "inactive"
    if (
        saved_notice.primary_deadline_at is not None
        and saved_notice.primary_deadline_at < _naive_now(now)
    ):
        return "closed"
    if saved_notice.status == "inactive":
        return "inactive"
    if saved_notice.status == "closed":
        return "closed"
    return "participating"


def _saved_notice_is_visible(saved_notice: CalendarSavedNotice, now: datetime) -> bool:
    return (
        saved_notice.priority_score > 0
        and _saved_notice_effective_status(saved_notice, now) == "participating"
    )


def _notice_list_item(
    notice: Notice,
    selected: bool,
    saved_notice_id: int | None,
    session: Session,
) -> dict:
    candidate = _notice_to_candidate(notice, session)
    primary_deadline_at, deadline_confidence = _notice_primary_deadline(notice)
    return {
        "notice_id": notice.id,
        "saved_notice_id": saved_notice_id,
        "title": notice.title,
        "organization": notice.organization,
        "primary_deadline_at": primary_deadline_at.isoformat() if primary_deadline_at else None,
        "amount_text": amount_text(candidate.amount_value),
        "amount_value": candidate.amount_value,
        "priority_score": candidate.priority_score,
        "priority_label": priority_label(candidate.priority_score),
        "notice_tag": candidate.notice_tag,
        "notice_tag_label": tag_label(candidate.notice_tag),
        "deadline_confidence": deadline_confidence,
        "deadline_confidence_label": deadline_confidence_label(deadline_confidence),
        "selected": selected,
        "source_url": notice.source_url,
    }


def _saved_notice_item(saved_notice: CalendarSavedNotice, now: datetime) -> dict:
    canonical_amount_value, canonical_amount_text = _canonical_amount(saved_notice.amount_value)
    effective_status = _saved_notice_effective_status(saved_notice, now)
    return {
        "saved_notice_id": saved_notice.id,
        "title": saved_notice.title,
        "organization": saved_notice.organization,
        "primary_deadline_at": (
            saved_notice.primary_deadline_at.isoformat()
            if saved_notice.primary_deadline_at
            else None
        ),
        "amount_text": canonical_amount_text,
        "amount_value": canonical_amount_value,
        "priority_score": saved_notice.priority_score,
        "priority_label": priority_label(saved_notice.priority_score),
        "notice_tag": saved_notice.notice_tag,
        "notice_tag_label": tag_label(saved_notice.notice_tag),
        "origin_type": saved_notice.origin_type,
        "origin_type_label": origin_type_label(saved_notice.origin_type),
        "deadline_confidence": saved_notice.deadline_confidence,
        "deadline_confidence_label": deadline_confidence_label(
            saved_notice.deadline_confidence
        ),
        "status": effective_status,
        "status_label": status_label(effective_status),
        "source_url": saved_notice.source_url,
    }


def get_calendar_notice_list(
    session: Session,
    now: datetime,
    site_code: str | None = None,
    selected_only: bool = False,
    q: str | None = None,
) -> dict:
    now = _naive_now(now)
    range_start, range_end = get_notice_list_range(now)
    saved_range_start, saved_range_end = get_calendar_range(now)
    rows = list_notice_rows_with_selection(
        session=session,
        range_start=range_start,
        range_end=range_end,
        site_code=site_code,
        selected_only=selected_only,
        q=q,
    )

    grouped: dict[str, list[dict]] = {}
    for notice, saved_notice in rows:
        saved_notice_id = saved_notice.id if saved_notice is not None else None
        selected = saved_notice is not None and _saved_notice_is_visible(saved_notice, now)
        item = _notice_list_item(notice, selected, saved_notice_id, session)
        deadline_value = item["primary_deadline_at"]
        if deadline_value:
            deadline_dt = datetime.fromisoformat(deadline_value)
            if deadline_dt < now or deadline_dt > range_end:
                continue
        if item["priority_score"] <= 0:
            continue
        grouped.setdefault(notice.site_code, []).append(item)

    saved_grouped: dict[str, list[dict]] = {}
    for saved_notice in list_saved_notices_without_source_for_range(
        session=session,
        range_start=saved_range_start,
        range_end=saved_range_end,
        q=q,
    ):
        if not _saved_notice_is_visible(saved_notice, now):
            continue
        saved_grouped.setdefault(saved_notice.site_code, []).append(
            _saved_notice_item(saved_notice, now)
        )

    sites = []
    for code, items in grouped.items():
        items.sort(
            key=lambda item: (
                0 if item["selected"] else 1,
                -item["priority_score"],
                item["primary_deadline_at"] or "9999-12-31T23:59:59",
                item["title"],
            )
        )
        sites.append(
            {
                "site_code": code,
                "site_name": SITE_NAMES.get(code, code.upper()),
                "items": items,
            }
        )
    sites.sort(key=lambda site: site["site_name"])

    saved_sites = []
    for code, items in saved_grouped.items():
        items.sort(
            key=lambda item: (
                item["primary_deadline_at"] is None,
                item["primary_deadline_at"] or "9999-12-31T23:59:59",
                -item["priority_score"],
                item["title"],
            )
        )
        saved_sites.append(
            {
                "site_code": code,
                "site_name": SITE_NAMES.get(code, code.upper()),
                "items": items,
            }
        )
    saved_sites.sort(key=lambda site: site["site_name"])

    return {
        "range": {
            "from": range_start.isoformat(),
            "to": range_end.isoformat(),
        },
        "sites": sites,
        "saved_sites": saved_sites,
    }


def save_calendar_selection(
    session: Session,
    notice_id: int,
    selected: bool,
    selected_by: str,
) -> dict:
    selected_by = (selected_by or "").strip()
    if not selected_by:
        raise ValueError("selected_by is required")

    notice = session.execute(select(Notice).where(Notice.id == notice_id)).scalars().one_or_none()
    if notice is None:
        raise LookupError("notice not found")

    candidate = _notice_to_candidate(notice, session)
    primary_deadline_at, deadline_confidence = _notice_primary_deadline(notice)
    canonical_amount_value, canonical_amount_text = _canonical_amount(candidate.amount_value)

    if selected:
        saved = upsert_saved_notice_from_notice(
            session=session,
            notice=notice,
            site_name=SITE_NAMES.get(notice.site_code, notice.site_code.upper()),
            selected_by=selected_by,
            primary_deadline_at=primary_deadline_at,
            deadline_confidence=deadline_confidence,
            priority_score=candidate.priority_score,
            notice_tag=candidate.notice_tag,
            amount_text=canonical_amount_text,
            amount_value=canonical_amount_value,
            raw_payload={
                "title": notice.title,
                "organization": notice.organization,
                "primary_deadline_at": (
                    primary_deadline_at.isoformat() if primary_deadline_at else None
                ),
                "amount_value": canonical_amount_value,
                "priority_score": candidate.priority_score,
                "notice_tag": candidate.notice_tag,
                "source_url": notice.source_url,
                "deadline_confidence": deadline_confidence,
                "deadline_basis": (
                    "notice_deadline"
                    if notice.deadline_at is not None
                    else "posted_at_plus_14_days"
                ),
                **candidate.raw_payload,
            },
        )
    else:
        saved = deactivate_saved_notice_by_source_notice_id(
            session=session,
            source_notice_id=notice_id,
            selected_by=selected_by,
        )
        if saved is None:
            raise LookupError("saved notice not found")

    return {
        "notice_id": notice_id,
        "selected": bool(selected),
        "saved_notice_id": saved.id,
        "is_active": bool(saved.is_active),
        "status": saved.status,
        "updated_at": saved.updated_at.isoformat(),
    }


def create_manual_calendar_notice(
    session: Session,
    *,
    title: str,
    organization: str | None,
    primary_deadline_at: datetime | None,
    amount_value: int | None,
    priority_score: int,
    notice_tag: str | None,
    source_url: str | None,
    status: str,
    owner_name: str | None,
    memo: str | None,
    selected_by: str,
    deadline_confidence: str,
) -> dict:
    title = (title or "").strip()
    selected_by = (selected_by or "").strip()
    source_url = (source_url or "").strip()
    if not title:
        raise ValueError("title is required")
    if not selected_by:
        raise ValueError("selected_by is required")

    status = _validate_status(status)
    deadline_confidence = _validate_deadline_confidence(deadline_confidence)
    notice_tag = _normalize_notice_tag(notice_tag)
    priority_score = max(0, min(int(priority_score or 0), 3))
    canonical_amount_value, canonical_amount_text = _canonical_amount(amount_value)

    saved = create_manual_saved_notice(
        session=session,
        title=title,
        organization=(organization or "").strip() or None,
        primary_deadline_at=primary_deadline_at,
        amount_text=canonical_amount_text,
        amount_value=canonical_amount_value,
        priority_score=priority_score,
        notice_tag=notice_tag,
        source_url=source_url,
        status=status,
        owner_name=(owner_name or "").strip() or None,
        memo=memo,
        selected_by=selected_by,
        deadline_confidence=deadline_confidence,
        raw_payload={
            "title": title,
            "organization": (organization or "").strip() or None,
            "primary_deadline_at": (
                primary_deadline_at.isoformat() if primary_deadline_at else None
            ),
            "amount_value": canonical_amount_value,
            "priority_score": priority_score,
            "notice_tag": notice_tag,
            "source_url": source_url,
            "origin_type": "manual",
            "deadline_confidence": deadline_confidence,
        },
    )
    return get_saved_notice_detail(session, saved.id)


def import_calendar_saved_notice(
    session: Session,
    *,
    title: str,
    organization: str | None,
    site_code: str = "imported",
    site_name: str = "과거이관",
    primary_deadline_at: datetime | None,
    amount_text_value: str | None,
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
) -> dict:
    title = (title or "").strip()
    selected_by = (selected_by or "").strip()
    if not title:
        raise ValueError("title is required")
    if not selected_by:
        raise ValueError("selected_by is required")

    status = _validate_status(status)
    deadline_confidence = _validate_deadline_confidence(deadline_confidence)
    notice_tag = _normalize_notice_tag(notice_tag)
    priority_score = max(0, min(int(priority_score or 0), 3))
    canonical_amount_value, canonical_amount_text = _canonical_amount(
        amount_value if amount_value is not None else None
    )
    if canonical_amount_text is None:
        canonical_amount_text = amount_text_value

    saved = upsert_imported_saved_notice(
        session=session,
        title=title,
        organization=(organization or "").strip() or None,
        site_code=site_code,
        site_name=site_name,
        primary_deadline_at=primary_deadline_at,
        amount_text=canonical_amount_text,
        amount_value=canonical_amount_value,
        priority_score=priority_score,
        notice_tag=notice_tag,
        source_url=source_url or "",
        status=status,
        owner_name=(owner_name or "").strip() or None,
        memo=memo,
        selected_by=selected_by,
        deadline_confidence=deadline_confidence,
        raw_payload=raw_payload,
        legacy_year=legacy_year,
        import_batch_id=import_batch_id,
    )
    return get_saved_notice_detail(session, saved.id)


def get_calendar_events(session: Session, month: str, now: datetime) -> dict:
    month_start, month_end = validate_calendar_month(month, now)
    allowed_start, allowed_end = get_calendar_range(now)
    rows = list_saved_notices_for_month(session, month_start, month_end)
    now = _naive_now(now)

    events = []
    for row in rows:
        if not _saved_notice_is_visible(row, now):
            continue
        _, canonical_amount_text = _canonical_amount(row.amount_value)
        effective_status = _saved_notice_effective_status(row, now)
        events.append(
            {
                "saved_notice_id": row.id,
                "source_notice_id": row.source_notice_id,
                "title": row.title,
                "site_code": row.site_code,
                "site_name": row.site_name,
                "organization": row.organization,
                "primary_deadline_at": (
                    row.primary_deadline_at.isoformat()
                    if row.primary_deadline_at
                    else None
                ),
                "amount_text": canonical_amount_text,
                "amount_value": row.amount_value,
                "priority_score": row.priority_score,
                "priority_label": priority_label(row.priority_score),
                "notice_tag": row.notice_tag,
                "notice_tag_label": tag_label(row.notice_tag),
                "status": effective_status,
                "status_label": status_label(effective_status),
                "owner_name": row.owner_name,
                "source_url": row.source_url,
                "origin_type": row.origin_type,
                "origin_type_label": origin_type_label(row.origin_type),
                "deadline_confidence": row.deadline_confidence,
                "deadline_confidence_label": deadline_confidence_label(
                    row.deadline_confidence
                ),
            }
        )

    return {
        "month": month,
        "range": {
            "from": allowed_start.isoformat(),
            "to": allowed_end.isoformat(),
        },
        "events": events,
    }


def get_saved_notice_detail(session: Session, saved_notice_id: int) -> dict | None:
    row = get_saved_notice(session, saved_notice_id)
    if row is None:
        return None
    effective_status = _saved_notice_effective_status(row, datetime.utcnow())
    payload = _load_payload(row.raw_payload_json)
    _, canonical_amount_text = _canonical_amount(row.amount_value)
    attachments = []
    if row.source_notice_id is not None:
        for attachment in list_attachments_for_notice_id(session, row.source_notice_id):
            attachments.append(
                {
                    "id": attachment.id,
                    "attachment_name": attachment.attachment_name,
                    "attachment_category": attachment.attachment_category,
                    "attachment_category_label": attachment_category_label(
                        attachment.attachment_category
                    ),
                    "priority_rank": attachment.priority_rank,
                    "download_url": f"/downloads/attachments/{attachment.id}",
                    "is_summary_source": bool(attachment.is_summary_source),
                }
            )
    summary = (
        get_notice_summary_payload(session, row.source_notice_id)
        if row.source_notice_id is not None
        else None
    )
    ai_evaluation = (
        get_ai_evaluation_payload(session, row.source_notice_id)
        if row.source_notice_id is not None
        else None
    )
    return {
        "id": row.id,
        "source_notice_id": row.source_notice_id,
        "site_code": row.site_code,
        "site_name": row.site_name,
        "title": row.title,
        "organization": row.organization,
        "primary_deadline_at": (
            row.primary_deadline_at.isoformat() if row.primary_deadline_at else None
        ),
        "amount_text": canonical_amount_text,
        "amount_value": row.amount_value,
        "priority_score": row.priority_score,
        "priority_label": priority_label(row.priority_score),
        "notice_tag": row.notice_tag,
        "notice_tag_label": tag_label(row.notice_tag),
        "source_url": row.source_url,
        "status": effective_status,
        "status_label": status_label(effective_status),
        "owner_name": row.owner_name,
        "selected_at": row.selected_at.isoformat(),
        "deselected_at": row.deselected_at.isoformat() if row.deselected_at else None,
        "updated_at": row.updated_at.isoformat(),
        "selected_by": row.selected_by,
        "is_active": bool(row.is_active),
        "memo": row.memo,
        "origin_type": row.origin_type,
        "origin_type_label": origin_type_label(row.origin_type),
        "deadline_confidence": row.deadline_confidence,
        "deadline_confidence_label": deadline_confidence_label(
            row.deadline_confidence
        ),
        "legacy_year": row.legacy_year,
        "import_batch_id": row.import_batch_id,
        "raw_payload": payload,
        "attachments": attachments,
        "summary": summary,
        "ai_evaluation": ai_evaluation,
    }


def update_saved_notice_fields(
    session: Session,
    saved_notice_id: int,
    status: str | None = None,
    owner_name: str | None = None,
    memo: str | None = None,
    primary_deadline_at: datetime | None = None,
    amount_value: int | None = None,
    priority_score: int | None = None,
    notice_tag: str | None = None,
    source_url: str | None = None,
    deadline_confidence: str | None = None,
) -> dict | None:
    row = get_saved_notice(session, saved_notice_id)
    if row is None:
        return None

    if status is not None:
        row.status = _validate_status(status)
    if owner_name is not None:
        row.owner_name = owner_name.strip() or None
    if memo is not None:
        row.memo = memo
    if primary_deadline_at is not None:
        row.primary_deadline_at = primary_deadline_at
    if amount_value is not None:
        canonical_amount_value, canonical_amount_text = _canonical_amount(amount_value)
        row.amount_value = canonical_amount_value
        row.amount_text = canonical_amount_text
    if priority_score is not None:
        row.priority_score = max(0, min(int(priority_score), 3))
    if notice_tag is not None:
        row.notice_tag = _normalize_notice_tag(notice_tag)
    if source_url is not None:
        row.source_url = source_url.strip()
    if deadline_confidence is not None:
        row.deadline_confidence = _validate_deadline_confidence(deadline_confidence)

    payload = _load_payload(row.raw_payload_json)
    payload.update(
        {
            "title": row.title,
            "organization": row.organization,
            "primary_deadline_at": (
                row.primary_deadline_at.isoformat() if row.primary_deadline_at else None
            ),
            "amount_value": row.amount_value,
            "priority_score": row.priority_score,
            "notice_tag": row.notice_tag,
            "source_url": row.source_url,
            "origin_type": row.origin_type,
            "deadline_confidence": row.deadline_confidence,
            "legacy_year": row.legacy_year,
            "import_batch_id": row.import_batch_id,
        }
    )
    row.raw_payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    row.updated_at = datetime.utcnow()
    session.commit()
    return get_saved_notice_detail(session, saved_notice_id)


def deactivate_saved_notice(
    session: Session,
    saved_notice_id: int,
    selected_by: str,
) -> dict | None:
    selected_by = (selected_by or "").strip()
    if not selected_by:
        raise ValueError("selected_by is required")
    row = deactivate_saved_notice_by_id(session, saved_notice_id, selected_by)
    if row is None:
        return None
    return get_saved_notice_detail(session, saved_notice_id)


def cleanup_inactive_saved_notices(session: Session, now: datetime) -> int:
    cutoff = now - timedelta(days=3)
    rows = [
        row
        for row in list_saved_notices_eligible_for_cleanup(session, now)
        if row.deselected_at is not None and row.deselected_at <= cutoff
    ]
    return delete_saved_notices(session, [row.id for row in rows])


def backfill_saved_notice_deadlines_from_notices(session: Session) -> int:
    stmt = (
        select(Notice, CalendarSavedNotice)
        .join(CalendarSavedNotice, CalendarSavedNotice.source_notice_id == Notice.id)
        .where(CalendarSavedNotice.origin_type == "notice")
        .where(CalendarSavedNotice.is_active.is_(True))
        .where(CalendarSavedNotice.primary_deadline_at.is_(None))
    )
    rows = session.execute(stmt).all()
    updated = 0
    for notice, saved in rows:
        primary_deadline_at, deadline_confidence = _notice_primary_deadline(notice)
        if primary_deadline_at is None:
            continue
        payload = _load_payload(saved.raw_payload_json)
        payload.update(
            {
                "primary_deadline_at": primary_deadline_at.isoformat(),
                "deadline_confidence": deadline_confidence,
                "deadline_basis": "posted_at_plus_14_days",
            }
        )
        saved.primary_deadline_at = primary_deadline_at
        saved.deadline_confidence = deadline_confidence
        saved.raw_payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        saved.updated_at = datetime.utcnow()
        updated += 1
    if updated:
        session.commit()
    return updated
