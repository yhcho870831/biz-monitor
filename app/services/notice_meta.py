from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy.orm import Session

from app.repositories.projects import list_active_projects
from app.types import NoticeCandidate
from app.utils import normalize_text


RESEARCH_KEYWORDS = (
    "\uc5f0\uad6c\uc6a9\uc5ed",
    "\uc704\ud0c1\uc5f0\uad6c",
    "\uae30\ud68d\uc5f0\uad6c",
    "\uc815\ucc45\uc5f0\uad6c",
    "\ud559\uc220\uc5f0\uad6c",
    "\uc5f0\uad6c\uac1c\ubc1c",
    "r&d",
)
GOODS_KEYWORDS = (
    "\ubb3c\ud488",
    "\uad6c\ub9e4",
    "\uad6c\uc785",
    "\ub0a9\ud488",
    "\ub9e4\uc785",
    "\ub3c4\uc785",
)
PRODUCTION_KEYWORDS = (
    "\uc81c\uc791\uc6a9\uc5ed",
    "\uc81c\uc791",
    "\uad6c\ucd95",
    "\uac1c\ubc1c",
    "\uc124\uce58",
    "\uace0\ub3c4\ud654",
    "\uac1c\uc120",
    "\uc870\uc131",
)
STRATEGIC_SIGNAL_KEYWORDS = (
    "\ud14c\uc2a4\ud2b8\ubca0\ub4dc",
    "\uc2e4\uc99d",
    "\ud30c\uc77c\ub7ff",
    "\uc2dc\ubc94",
)
STRATEGIC_SUPPORT_KEYWORDS = (
    "\uc5f0\uad6c",
    "\uc5f0\uad6c\uc6a9\uc5ed",
    "\uc6a9\uc5ed",
    "\uad6c\ucd95",
)

TAG_META = {
    "research_service": ("\U0001F7E6", "\uc5f0\uad6c\uc6a9\uc5ed"),
    "goods_purchase": ("\U0001F7E9", "\ubb3c\ud488\uad6c\ub9e4"),
    "production_service": ("\U0001F7E7", "\uc81c\uc791\uc6a9\uc5ed"),
    "general_service": ("\U0001F7EA", "\uc77c\ubc18\uc6a9\uc5ed"),
    "other": ("\u2B1C", "\uae30\ud0c0"),
}


def _preferred_organization(candidate: NoticeCandidate) -> str:
    raw = candidate.raw_payload or {}
    # 나라장터는 공고기관보다 수요기관이 실제 발주처에 가까워 우선 사용한다.
    for key in ("\uc218\uc694\uae30\uad00", "\uacf5\uace0\uae30\uad00"):
        value = normalize_text(raw.get(key))
        if value:
            return value
    return normalize_text(candidate.organization)


def _iter_candidate_texts(candidate: NoticeCandidate) -> Iterable[str]:
    yield candidate.title or ""
    yield candidate.organization or ""
    yield candidate.period_text or ""
    for value in (candidate.raw_payload or {}).values():
        if value is None:
            continue
        yield str(value)


def _combined_text(candidate: NoticeCandidate) -> str:
    return "\n".join(normalize_text(value) for value in _iter_candidate_texts(candidate) if value)


def extract_amount_value(candidate: NoticeCandidate) -> int | None:
    raw = candidate.raw_payload or {}
    raw_amount_value = raw.get("amount_value")
    if isinstance(raw_amount_value, bool):
        raw_amount_value = None
    if isinstance(raw_amount_value, int):
        return raw_amount_value
    if isinstance(raw_amount_value, float):
        return int(raw_amount_value)
    if isinstance(raw_amount_value, str):
        digits = re.sub(r"[^\d]", "", raw_amount_value)
        if digits:
            return int(digits)

    text = _combined_text(candidate)
    if not text:
        return None

    amounts = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*\uc5b5", text):
        amounts.append(int(float(match.group(1)) * 100_000_000))
    for match in re.finditer(r"(\d[\d,]*)\s*\ubc31\ub9cc\uc6d0", text):
        amounts.append(int(match.group(1).replace(",", "")) * 1_000_000)
    for match in re.finditer(r"(\d[\d,]*)\s*\ucc9c\uc6d0", text):
        amounts.append(int(match.group(1).replace(",", "")) * 1_000)
    for match in re.finditer(r"(\d[\d,]{6,})\s*\uc6d0", text):
        amounts.append(int(match.group(1).replace(",", "")))

    return max(amounts) if amounts else None


def classify_notice_tag(candidate: NoticeCandidate) -> str:
    text = _combined_text(candidate).lower()
    if any(keyword in text for keyword in RESEARCH_KEYWORDS):
        return "research_service"
    if any(keyword in text for keyword in GOODS_KEYWORDS):
        return "goods_purchase"
    if any(keyword in text for keyword in PRODUCTION_KEYWORDS):
        return "production_service"
    if "\uc6a9\uc5ed" in text:
        return "general_service"
    return "other"


def has_strategic_review_signal(candidate: NoticeCandidate) -> bool:
    text = _combined_text(candidate).lower()
    if not any(keyword in text for keyword in STRATEGIC_SIGNAL_KEYWORDS):
        return False
    if classify_notice_tag(candidate) == "research_service":
        return True
    return any(keyword in text for keyword in STRATEGIC_SUPPORT_KEYWORDS)


def _organization_match_score(session: Session, candidate: NoticeCandidate) -> int:
    preferred_org = _preferred_organization(candidate)
    if not preferred_org:
        return 0
    candidate_orgs = [preferred_org.lower()]
    fallback_org = normalize_text(candidate.organization).lower()
    if fallback_org and fallback_org not in candidate_orgs:
        candidate_orgs.append(fallback_org)

    for project in list_active_projects(session):
        project_org = normalize_text(project.organization).lower()
        if not project_org:
            continue
        for candidate_org in candidate_orgs:
            if project_org == candidate_org:
                return 1
            if len(project_org) >= 3 and (project_org in candidate_org or candidate_org in project_org):
                return 1
    return 0


def calculate_priority_score(session: Session, candidate: NoticeCandidate) -> int:
    score = 0
    score += _organization_match_score(session, candidate)

    amount_value = extract_amount_value(candidate)
    if amount_value is not None and amount_value >= 100_000_000:
        score += 1

    if classify_notice_tag(candidate) == "research_service":
        score += 1
    if has_strategic_review_signal(candidate):
        score += 1

    return score


def enrich_notice_candidate(session: Session, candidate: NoticeCandidate) -> NoticeCandidate:
    preferred_org = _preferred_organization(candidate)
    if preferred_org:
        candidate.organization = preferred_org
    candidate.amount_value = extract_amount_value(candidate)
    candidate.notice_tag = classify_notice_tag(candidate)
    candidate.priority_score = calculate_priority_score(session, candidate)
    return candidate


def format_priority_stars(score: int) -> str:
    bounded = max(0, min(score, 3))
    return ("\u2605" * bounded) + ("\u2606" * (3 - bounded))


def format_notice_tag(tag: str | None) -> str:
    emoji, label = TAG_META.get(tag or "other", TAG_META["other"])
    return "%s %s" % (emoji, label)
