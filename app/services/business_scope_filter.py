from __future__ import annotations

import os

from app.types import NoticeCandidate
from app.utils import normalize_text


DEFAULT_EXCLUDED_SCOPE_KEYWORDS = (
    "\uc870\uacbd",
    "\uc218\ubaa9",
    "\ub3c4\ub85c\ud3ec\uc7a5",
    "\ub3c4\ub85c \ud3ec\uc7a5",
    "\ud3d0\uae30\ubb3c",
    "\uc7ac\ub09c\ub300\ube44",
    "\uc7ac\ub09c \ub300\ube44",
    "\uacf5\uc6d0",
    "\ucc28\ub7c9",
    "\uac74\ucd95",
    "\uc774\uc628",
    "\ubb3c\ub958\ubc18\uc1a1",
    "\ubc18\uc1a1\uc124\ube44",
    "\ubb3c\ub958 \uc6b4\ubc18",
    "\ubb3c\ub958\uc6b4\ubc18",
    "\uc218\uacbd\uc2dc\uc124",
    "\uc218\uacbd\uc2dc\uc124 \uc6b4\uc601",
    "\ubb3c\ub180\uc774\ud615 \uc218\uacbd\uc2dc\uc124",
    "\uc218\uc0b0\uc885\uc790",
    "\ub9e4\uc785\ubc29\ub958",
    "\uaf43\uac8c",
    "\ub0b4\uc218\uba74",
    "ssr\uae30\ubc18",
    "\ud2b9\uc774 \ub9c8\ucee4",
    "\uc778\uc591\uae30",
    "\uc5b4\uc120\uc778\uc591\uae30",
    "\ud654\uc7ac\uc548\uc804\uc2dc\ud5d8\ub3d9",
    "\ubb38\uc81c\uc740\ud589",
    "\uc120\ubc15\uc548\uc804\uad00\ub9ac\uc0ac",
    "\uccad\uc18c",
)


def excluded_scope_keywords() -> tuple[str, ...]:
    custom_terms = tuple(
        normalized
        for normalized in (
            normalize_text(value)
            for value in os.getenv("EXCLUDED_NOTICE_TERMS", "").split(",")
        )
        if normalized
    )
    ordered_terms: list[str] = []
    seen: set[str] = set()
    for term in (*DEFAULT_EXCLUDED_SCOPE_KEYWORDS, *custom_terms):
        if term in seen:
            continue
        seen.add(term)
        ordered_terms.append(term)
    return tuple(ordered_terms)


def _candidate_scope_text(candidate: NoticeCandidate) -> str:
    values = [candidate.title or ""]
    for value in (candidate.raw_payload or {}).values():
        if isinstance(value, (str, int, float)):
            values.append(str(value))
    return normalize_text(" ".join(values)).lower()


def excluded_scope_reason(candidate: NoticeCandidate, settings=None) -> str | None:
    # Keep the optional second argument for compatibility with older scheduler builds
    # that still pass settings into the scope-filter call.
    _ = settings
    text = _candidate_scope_text(candidate)
    if not text:
        return None
    for keyword in excluded_scope_keywords():
        if keyword in text:
            return keyword
    return None


def is_excluded_business_notice(candidate: NoticeCandidate, settings=None) -> bool:
    return excluded_scope_reason(candidate, settings=settings) is not None
