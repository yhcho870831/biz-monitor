from __future__ import annotations

from app.utils import normalize_text

# 단독 검색 시 공고 본문에 도메인 키워드가 함께 있어야 통과하는 광역어
BROAD_COMPOUND_ONLY_TERMS = (
    "유지보수",
    "유지관리",
    "통합관리",
    "양식",
)


def is_exact_broad_compound_search_term(search_term: str | None) -> bool:
    normalized = normalize_text(search_term or "").lower()
    if not normalized:
        return False
    return normalized in {
        normalize_text(term).lower() for term in BROAD_COMPOUND_ONLY_TERMS
    }


def is_broad_compound_token(keyword: str) -> bool:
    normalized = normalize_text(keyword).lower()
    if not normalized:
        return False
    return normalized in {
        normalize_text(term).lower() for term in BROAD_COMPOUND_ONLY_TERMS
    }


def filter_supporting_keyword_matches(keyword_matches: set[str]) -> set[str]:
    return {
        keyword
        for keyword in keyword_matches
        if not is_broad_compound_token(keyword)
    }
