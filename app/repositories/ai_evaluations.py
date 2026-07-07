from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NoticeAiEvaluation


def get_ai_evaluation_by_input(
    session: Session,
    notice_id: int,
    prompt_version: str,
    input_hash: str,
) -> NoticeAiEvaluation | None:
    stmt = select(NoticeAiEvaluation).where(
        NoticeAiEvaluation.notice_id == notice_id,
        NoticeAiEvaluation.prompt_version == prompt_version,
        NoticeAiEvaluation.input_hash == input_hash,
    )
    return session.execute(stmt).scalars().one_or_none()


def get_latest_ai_evaluation(session: Session, notice_id: int) -> NoticeAiEvaluation | None:
    stmt = (
        select(NoticeAiEvaluation)
        .where(NoticeAiEvaluation.notice_id == notice_id)
        .order_by(NoticeAiEvaluation.updated_at.desc(), NoticeAiEvaluation.id.desc())
    )
    return session.execute(stmt).scalars().first()


def list_latest_ai_evaluations_for_notice_ids(
    session: Session,
    notice_ids: list[int],
) -> dict[int, NoticeAiEvaluation]:
    if not notice_ids:
        return {}
    rows = list(
        session.execute(
            select(NoticeAiEvaluation)
            .where(NoticeAiEvaluation.notice_id.in_(notice_ids))
            .order_by(NoticeAiEvaluation.notice_id, NoticeAiEvaluation.updated_at.desc())
        )
        .scalars()
        .all()
    )
    result: dict[int, NoticeAiEvaluation] = {}
    for row in rows:
        if row.notice_id not in result:
            result[row.notice_id] = row
    return result


def delete_ai_evaluations_for_notice_ids(
    session: Session,
    notice_ids: list[int],
    *,
    commit: bool = True,
) -> int:
    if not notice_ids:
        return 0
    rows = list(
        session.execute(
            select(NoticeAiEvaluation).where(NoticeAiEvaluation.notice_id.in_(notice_ids))
        )
        .scalars()
        .all()
    )
    for row in rows:
        session.delete(row)
    session.flush()
    if commit:
        session.commit()
    return len(rows)


def mark_ai_recommendations_posted(
    session: Session,
    evaluation_ids: list[int],
    *,
    posted_at: datetime | None = None,
    commit: bool = True,
) -> int:
    if not evaluation_ids:
        return 0
    now = posted_at or datetime.utcnow()
    rows = list(
        session.execute(
            select(NoticeAiEvaluation).where(NoticeAiEvaluation.id.in_(evaluation_ids))
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.ai_recommendation_posted_at = now
        row.updated_at = now
    session.flush()
    if commit:
        session.commit()
    return len(rows)


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def upsert_ai_evaluation(
    session: Session,
    *,
    notice_id: int,
    provider: str,
    model: str,
    prompt_version: str,
    input_hash: str,
    status: str,
    fit_score: int | None = None,
    fit_level: str | None = None,
    confidence: str | None = None,
    recommended_action: str | None = None,
    reason: str | None = None,
    summary_for_slack: str | None = None,
    matched_capabilities: Any = None,
    risks: Any = None,
    raw_response: Any = None,
    failure_reason: str | None = None,
    commit: bool = True,
) -> NoticeAiEvaluation:
    now = datetime.utcnow()
    existing = get_ai_evaluation_by_input(session, notice_id, prompt_version, input_hash)
    if existing is None:
        existing = NoticeAiEvaluation(
            notice_id=notice_id,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            input_hash=input_hash,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)

    existing.provider = provider
    existing.model = model
    existing.status = status
    existing.fit_score = fit_score
    existing.fit_level = fit_level
    existing.confidence = confidence
    existing.recommended_action = recommended_action
    existing.reason = reason
    existing.summary_for_slack = summary_for_slack
    existing.matched_capabilities_json = _json_or_none(matched_capabilities)
    existing.risks_json = _json_or_none(risks)
    existing.raw_response_json = _json_or_none(raw_response)
    existing.failure_reason = failure_reason
    existing.updated_at = now
    session.flush()
    if commit:
        session.commit()
    return existing
