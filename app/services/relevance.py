from __future__ import annotations

from typing import Iterable, Tuple

from sqlalchemy.orm import Session

from app.repositories.projects import list_active_projects, parse_project_keywords
from app.services.broad_search_terms import (
    filter_supporting_keyword_matches,
    is_exact_broad_compound_search_term,
)
from app.services.notice_meta import has_strategic_review_signal
from app.types import NoticeCandidate
from app.utils import normalize_text


def _candidate_match_text(candidate: NoticeCandidate) -> str:
    values = [
        candidate.title,
        candidate.organization,
        candidate.period_text,
        candidate.source_url,
    ]
    raw_payload = candidate.raw_payload or {}
    for value in raw_payload.values():
        if isinstance(value, (str, int, float)):
            values.append(str(value))
    return normalize_text(" ".join(value for value in values if value)).lower()


def _matching_keywords(text: str, keywords: Iterable[str]) -> set[str]:
    matches: set[str] = set()
    for keyword in keywords:
        normalized = normalize_text(keyword).lower()
        if normalized and normalized in text:
            matches.add(normalized)
    return matches


def is_relevant(session: Session, candidate: NoticeCandidate, search_term: str | None = None) -> Tuple[bool, str]:
    candidate_text = _candidate_match_text(candidate)
    if has_strategic_review_signal(candidate):
        return True, "strategic_testbed_validation_signal"

    projects = list_active_projects(session)
    if not projects:
        return False, "no_company_projects_configured"

    keyword_matches: set[str] = set()
    for project in projects:
        keywords = parse_project_keywords(project)
        keyword_matches.update(_matching_keywords(candidate_text, keywords))
        if project.organization and candidate.organization:
            if project.organization.strip().lower() == candidate.organization.strip().lower():
                return True, "matched_project_organization"

    if is_exact_broad_compound_search_term(search_term):
        supporting_matches = filter_supporting_keyword_matches(keyword_matches)
        if supporting_matches:
            return True, "matched_broad_compound_with_supporting_keyword"
        return False, "broad_compound_without_supporting_keyword"

    if len(keyword_matches) >= 2:
        return True, "matched_project_keywords_2plus"

    return False, "keyword_matches_%s" % len(keyword_matches)
