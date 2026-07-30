from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Iterable, List, Optional


MOJIBAKE_MARKERS = (
    "�",
    "횁",
    "횂",
    "횄",
    "횆",
    "횇",
    "횈",
    "횉",
    "횊",
    "횋",
    "횎",
    "횏",
    "횑",
    "횒",
    "횓",
    "횕",
    "횖",
    "횗",
    "횘",
    "횙",
    "횚",
    "횛",
    "횜",
    "횞",
    "횠",
    "횢",
    "횣",
    "횤",
    "횥",
    "횦",
    "횧",
    "횩",
    "째",
    "짹",
    "짼",
    "쨀",
    "쨈",
    "쨉",
    "쨋",
    "쨌",
    "쨍",
    "쨔",
    "쨘",
    "쨩",
    "쩌",
    "쩍",
    "쩐",
)


def repair_mojibake(value: str) -> str:
    if not value:
        return ""
    if not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value

    for source_encoding in ("latin1", "cp1252"):
        try:
            repaired = value.encode(source_encoding).decode("cp949")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        original_hangul = sum("가" <= ch <= "힣" for ch in value)
        repaired_hangul = sum("가" <= ch <= "힣" for ch in repaired)
        if repaired and repaired_hangul >= original_hangul:
            return repaired
    return value


def normalize_text(value: str) -> str:
    if value is None:
        return ""
    value = repair_mojibake(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def now_kst() -> datetime:
    """Return naive Korea Standard Time for source-site timestamps.

    Collectors store Korean dates without timezone data. Korea has no daylight
    saving time, so UTC+9 is intentional and does not depend on zoneinfo/tzdata.
    """
    return datetime.utcnow() + timedelta(hours=9)


def extract_datetimes(value: str) -> List[datetime]:
    text = normalize_text(value)
    if not text or text in {"해당없음", "-"}:
        return []

    matches = []
    patterns = [
        r"(\d{4}[./-]\d{2}[./-]\d{2}\s+\d{2}:\d{2})",
        r"(\d{4}[./-]\d{2}[./-]\d{2})",
        r"(?<!\d)(\d{4}\d{2}\d{2})(?!\d)",
    ]
    for pattern in patterns:
        for raw in re.findall(pattern, text):
            normalized = raw.replace(".", "-").replace("/", "-")
            if re.fullmatch(r"\d{8}", normalized):
                fmt = "%Y%m%d"
            else:
                fmt = "%Y-%m-%d %H:%M" if ":" in normalized else "%Y-%m-%d"
            try:
                matches.append(datetime.strptime(normalized, fmt))
            except ValueError:
                continue
    return matches


def parse_datetime(value: str) -> Optional[datetime]:
    matches = extract_datetimes(value)
    return matches[0] if matches else None


_TITLE_DEADLINE_RE = re.compile(
    r"~\s*"
    r"(?:(?P<year>\d{4})\s*[.\-/]\s*)?"          # optional 4-digit year
    r"(?P<month>\d{1,2})\s*[.\-/]\s*(?P<day>\d{1,2})\.?"  # M.D (sep . - /)
    r"(?:\s*\([^)]*\))?"                          # optional (요일)
    r"(?:\s*(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"  # optional HH:MM
)


def parse_title_deadline(
    title: str, reference: Optional[datetime] = None
) -> Optional[datetime]:
    """Parse a trailing ``~M.D.(요일) HH:MM`` style deadline out of a notice title.

    ``extract_datetimes`` only recognises full ``YYYY.MM.DD`` dates, so titles like
    ``「…공모전」(~4.24.(금) 18:00)`` (no year) are missed. The year is taken from the
    match when present, otherwise inferred from ``reference`` (e.g. the posting
    date): same year, rolling forward only when the parsed month/day falls clearly
    before the reference (a December→January wrap). Returns ``None`` when no usable
    deadline is found.
    """
    text = normalize_text(title or "")
    if "~" not in text:
        return None
    match = _TITLE_DEADLINE_RE.search(text)
    if not match:
        return None
    month = int(match.group("month"))
    day = int(match.group("day"))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    hour = int(match.group("hour")) if match.group("hour") else 23
    minute = int(match.group("minute")) if match.group("minute") else 59
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        hour, minute = 23, 59
    if match.group("year"):
        year = int(match.group("year"))
    else:
        ref = reference or datetime.utcnow()
        year = ref.year
        # Roll to next year only on a clear calendar wrap (e.g. posted Dec, due Jan).
        if (month, day) < (ref.month, ref.day) and (ref.month - month) > 6:
            year += 1
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


def make_period_text(
    start_at: Optional[datetime], end_at: Optional[datetime], raw: str = ""
) -> str:
    if raw:
        return normalize_text(raw)
    if start_at and end_at:
        return "%s ~ %s" % (
            start_at.strftime("%Y-%m-%d %H:%M"),
            end_at.strftime("%Y-%m-%d %H:%M"),
        )
    if end_at:
        return end_at.strftime("%Y-%m-%d %H:%M")
    if start_at:
        return start_at.strftime("%Y-%m-%d %H:%M")
    return "기간 미기재"


def dumps_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def contains_any_keyword(text: str, keywords: Iterable[str]) -> bool:
    haystack = normalize_text(text).lower()
    for keyword in keywords:
        normalized = normalize_text(keyword).lower()
        if normalized and normalized in haystack:
            return True
    return False
