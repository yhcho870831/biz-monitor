from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.collectors.d2b import D2BCollector
from app.types import NoticeCandidate
from app.utils import extract_datetimes, normalize_text, parse_datetime


NO_DEADLINE_MAX_AGE_DAYS = 30
PRE_ANNOUNCEMENT_MAX_AGE_DAYS = 30
G2B_ACTIVE_PRE_ANNOUNCEMENT_STATUSES = {"\uac8c\uc2dc\uc911"}


def _is_active_iris_schedule(candidate: NoticeCandidate, now: datetime) -> bool:
    raw_payload = candidate.raw_payload or {}
    if raw_payload.get("iris_result_type") != "schedule":
        return True

    try:
        business_year = int(str(raw_payload.get("bsns_yy") or "").strip())
    except ValueError:
        return True

    if business_year < now.year:
        return False
    if business_year > now.year:
        return True

    months = [
        int(month)
        for month in re.findall(r"(\d{1,2})\s*" + "\uc6d4", candidate.period_text or "")
        if 1 <= int(month) <= 12
    ]
    if not months:
        return True
    return max(months) >= now.month


def _candidate_posted_at(candidate: NoticeCandidate) -> datetime | None:
    if candidate.start_at is not None:
        return candidate.start_at

    raw_payload = candidate.raw_payload or {}
    for key in ("posted_at", "prcsYmd"):
        value = str(raw_payload.get(key) or "").strip()
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed

    dates = extract_datetimes(candidate.period_text or "")
    return dates[0] if dates else None


def _is_stale_notice_without_deadline(candidate: NoticeCandidate, now: datetime) -> bool:
    if candidate.deadline_at is not None:
        return False

    posted_at = _candidate_posted_at(candidate)
    if posted_at is None:
        return False

    raw_payload = candidate.raw_payload or {}
    max_age_days = (
        PRE_ANNOUNCEMENT_MAX_AGE_DAYS
        if raw_payload.get("announcement_stage") == "pre_announcement"
        else NO_DEADLINE_MAX_AGE_DAYS
    )
    return posted_at < (now - timedelta(days=max_age_days))


def _is_active_g2b_pre_announcement(candidate: NoticeCandidate, now: datetime) -> bool:
    """Only keep currently published G2B pre-announcements for monitoring."""
    raw_payload = candidate.raw_payload or {}
    status = normalize_text(str(raw_payload.get("oderPlanPgstNm") or ""))
    if status not in G2B_ACTIVE_PRE_ANNOUNCEMENT_STATUSES:
        return False
    return not _is_stale_notice_without_deadline(candidate, now)


def is_active_notice(candidate: NoticeCandidate, now: datetime) -> bool:
    if candidate.deadline_at is not None and candidate.deadline_at < now:
        return False
    if candidate.site_code == "g2b":
        raw_payload = candidate.raw_payload or {}
        if raw_payload.get("announcement_stage") == "pre_announcement":
            return _is_active_g2b_pre_announcement(candidate, now)
        # A bid notice without a submission deadline cannot be acted on safely.
        return candidate.deadline_at is not None
    if candidate.site_code == "iris":
        return _is_active_iris_schedule(candidate, now)
    if candidate.site_code == "d2b":
        return D2BCollector.is_active(candidate, now)
    if _is_stale_notice_without_deadline(candidate, now):
        return False
    return True
