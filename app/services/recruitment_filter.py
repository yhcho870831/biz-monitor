from __future__ import annotations

from app.types import NoticeCandidate
from app.utils import normalize_text


RECRUITMENT_KEYWORDS = (
    "채용",
    "채용공고",
    "인재채용",
    "인재양성",
    "직원채용",
    "인력채용",
    "채용 계획",
    "채용계획",
    "서류전형",
    "면접전형",
    "임용",
    "공무직",
    "기간제근로자",
    "기간제 근로자",
    "계약직 채용",
    "청년인턴",
    "인턴 채용",
    "신입 채용",
    "경력 채용",
    "채용형",
)


def is_recruitment_notice(candidate: NoticeCandidate) -> bool:
    title = normalize_text(candidate.title or "").lower()
    if not title:
        return False
    return any(keyword in title for keyword in RECRUITMENT_KEYWORDS)
