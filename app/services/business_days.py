from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

try:
    from kr_holidays import get_holiday_name as _get_kr_holiday_name
    from kr_holidays import is_holiday as _is_kr_holiday
except ImportError:  # pragma: no cover
    _get_kr_holiday_name = None
    _is_kr_holiday = None


def _to_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def is_weekend(value: date | datetime) -> bool:
    target = _to_date(value)
    return target.weekday() >= 5


def is_public_holiday(value: date | datetime) -> bool:
    target = _to_date(value)
    if _is_kr_holiday is None:
        logger.warning("kr_holidays is not installed; public holiday checks are disabled")
        return False
    return bool(_is_kr_holiday(target))


def get_public_holiday_name(value: date | datetime) -> str:
    target = _to_date(value)
    if _get_kr_holiday_name is None:
        return ""
    return str(_get_kr_holiday_name(target) or "")


def is_scheduled_run_day(
    value: date | datetime,
    *,
    skip_weekends: bool = True,
    skip_holidays: bool = True,
) -> bool:
    if skip_weekends and is_weekend(value):
        return False
    if skip_holidays and is_public_holiday(value):
        return False
    return True


def get_non_business_day_reason(
    value: date | datetime,
    *,
    skip_weekends: bool = True,
    skip_holidays: bool = True,
) -> str:
    if skip_weekends and is_weekend(value):
        return "weekend"
    if skip_holidays and is_public_holiday(value):
        holiday_name = get_public_holiday_name(value)
        return holiday_name or "public holiday"
    return ""
