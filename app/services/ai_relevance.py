from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.models import Notice, NoticeAiEvaluation
from app.repositories.ai_evaluations import (
    get_ai_evaluation_by_input,
    get_latest_ai_evaluation,
    upsert_ai_evaluation,
)
from app.repositories.projects import list_active_projects, parse_project_keywords
from app.services.summaries import get_notice_summary_payload
from app.types import NoticeCandidate
from app.utils import normalize_text

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "openclaw"
DEFAULT_MODEL = "cron-google"


def ai_relevance_enabled(settings) -> bool:
    return bool(getattr(settings, "ai_relevance_enabled", False)) and bool(
        getattr(settings, "ai_relevance_gateway_url", "")
    )


def should_share_ai_evaluation(payload: dict | None, settings) -> bool:
    if not payload or payload.get("status") != "done":
        return False
    threshold = int(getattr(settings, "ai_relevance_share_threshold", 70))
    score = int(payload.get("fit_score") or 0)
    action = str(payload.get("recommended_action") or "").strip().lower()
    return score >= threshold or action in {"review", "bid", "participate"}


def ai_evaluation_payload(evaluation: NoticeAiEvaluation | None) -> dict | None:
    if evaluation is None:
        return None
    return {
        "notice_id": evaluation.notice_id,
        "provider": evaluation.provider,
        "model": evaluation.model,
        "prompt_version": evaluation.prompt_version,
        "status": evaluation.status,
        "fit_score": evaluation.fit_score,
        "fit_level": evaluation.fit_level,
        "confidence": evaluation.confidence,
        "recommended_action": evaluation.recommended_action,
        "reason": evaluation.reason,
        "summary_for_slack": evaluation.summary_for_slack,
        "matched_capabilities": _loads_json_list(evaluation.matched_capabilities_json),
        "risks": _loads_json_list(evaluation.risks_json),
        "failure_reason": evaluation.failure_reason,
    }


def get_ai_evaluation_payload(session: Session, notice_id: int) -> dict | None:
    return ai_evaluation_payload(get_latest_ai_evaluation(session, notice_id))


def format_ai_evaluation_lines(payload: dict | None, *, prefix: str = "") -> list[str]:
    if not payload or payload.get("status") != "done":
        return []
    title_prefix = f"{prefix} " if prefix else ""
    score = payload.get("fit_score")
    fit_level = _fit_level_label(str(payload.get("fit_level") or ""))
    action = _action_label(str(payload.get("recommended_action") or ""))
    reason = normalize_text(str(payload.get("summary_for_slack") or payload.get("reason") or ""))
    capabilities = [normalize_text(str(item)) for item in payload.get("matched_capabilities") or []]
    risks = [normalize_text(str(item)) for item in payload.get("risks") or []]

    lines = [f"{title_prefix}AI 적합성"]
    lines.append(f"  - 적합도: {score if score is not None else '미확인'}점 / {fit_level} / {action}")
    if reason:
        lines.append(f"  - 판단 이유: {_clip(reason)}")
    if capabilities:
        lines.append(f"  - 매칭 역량: {_clip(', '.join(capabilities[:4]))}")
    if risks:
        lines.append(f"  - 검토 리스크: {_clip(', '.join(risks[:3]))}")
    return lines


def evaluate_notice_relevance(
    session: Session,
    settings,
    notice: Notice,
    candidate: NoticeCandidate,
) -> dict | None:
    if not ai_relevance_enabled(settings):
        return None

    payload = _build_input_payload(session, settings, notice, candidate)
    if not payload.get("company_projects"):
        return _record_failure(
            session,
            settings,
            notice.id,
            payload,
            "company_projects_not_configured",
        )

    prompt_version = str(getattr(settings, "ai_relevance_prompt_version", "v1") or "v1")
    input_hash = _hash_payload(payload)
    cached = get_ai_evaluation_by_input(session, notice.id, prompt_version, input_hash)
    if cached is not None and cached.status == "done":
        return ai_evaluation_payload(cached)

    prompt = _build_prompt(payload)
    try:
        gateway_response = _call_openclaw_gateway(settings, prompt)
        parsed = _parse_gateway_response(gateway_response)
        evaluation = upsert_ai_evaluation(
            session,
            notice_id=notice.id,
            provider=_gateway_provider(gateway_response),
            model=_gateway_model(gateway_response, str(getattr(settings, "ai_relevance_agent", DEFAULT_MODEL))),
            prompt_version=prompt_version,
            input_hash=input_hash,
            status="done",
            fit_score=parsed["fit_score"],
            fit_level=parsed["fit_level"],
            confidence=parsed["confidence"],
            recommended_action=parsed["recommended_action"],
            reason=parsed["reason"],
            summary_for_slack=parsed["summary_for_slack"],
            matched_capabilities=parsed["matched_capabilities"],
            risks=parsed["risks"],
            raw_response=gateway_response,
            failure_reason=None,
            commit=True,
        )
        return ai_evaluation_payload(evaluation)
    except Exception as exc:
        logger.exception("AI relevance evaluation failed notice_id=%s", notice.id)
        return _record_failure(session, settings, notice.id, payload, str(exc))


def _record_failure(
    session: Session,
    settings,
    notice_id: int,
    payload: dict,
    failure_reason: str,
) -> dict | None:
    prompt_version = str(getattr(settings, "ai_relevance_prompt_version", "v1") or "v1")
    input_hash = _hash_payload(payload)
    evaluation = upsert_ai_evaluation(
        session,
        notice_id=notice_id,
        provider=DEFAULT_PROVIDER,
        model=str(getattr(settings, "ai_relevance_agent", DEFAULT_MODEL)),
        prompt_version=prompt_version,
        input_hash=input_hash,
        status="failed",
        failure_reason=failure_reason[:1000],
        commit=True,
    )
    return ai_evaluation_payload(evaluation)


def _build_input_payload(
    session: Session,
    settings,
    notice: Notice,
    candidate: NoticeCandidate,
) -> dict:
    summary = get_notice_summary_payload(session, notice.id) or {}
    raw_payload = candidate.raw_payload or {}
    extracted_excerpt = _read_extracted_excerpt(summary.get("raw_extracted_text_path"))
    company_projects = []
    for project in list_active_projects(session)[:50]:
        company_projects.append(
            {
                "project_name": project.project_name,
                "organization": project.organization,
                "category": project.category,
                "keywords": parse_project_keywords(project)[:20],
            }
        )

    notice_text = {
        "site_code": candidate.site_code,
        "title": candidate.title,
        "organization": candidate.organization,
        "notice_no": candidate.notice_no,
        "reference_no": candidate.reference_no,
        "period_text": candidate.period_text,
        "amount_value": candidate.amount_value,
        "notice_tag": candidate.notice_tag,
        "priority_score": candidate.priority_score,
        "summary": {
            "purpose": summary.get("purpose"),
            "core_tasks": summary.get("core_tasks"),
            "required_performance": summary.get("required_performance"),
            "quantitative_targets": summary.get("quantitative_targets"),
            "period_text": summary.get("period_text"),
        },
        "raw_payload": _compact_raw_payload(raw_payload),
        "extracted_excerpt": extracted_excerpt,
    }
    return {
        "company_projects": company_projects,
        "notice": notice_text,
        "policy": {
            "share_threshold": int(getattr(settings, "ai_relevance_share_threshold", 70)),
            "meaning": "검색어로 수집된 공고 중 우리 회사가 검토할 가치가 있는지 의미 기반으로 판단",
        },
    }


def _compact_raw_payload(raw_payload: dict) -> dict:
    result = {}
    for key, value in raw_payload.items():
        if value is None:
            continue
        text = normalize_text(str(value))
        if not text:
            continue
        result[str(key)[:80]] = text[:500]
    return result


def _read_extracted_excerpt(path_value: str | None, limit: int = 8000) -> str:
    if not path_value:
        return ""
    try:
        path = Path(path_value)
        if not path.exists() or not path.is_file():
            return ""
        return normalize_text(path.read_text(encoding="utf-8", errors="ignore"))[:limit]
    except Exception:
        return ""


def _build_prompt(payload: dict) -> str:
    return (
        "당신은 공공 입찰/정부 R&D 공고를 우리 회사가 검토할 가치가 있는지 판단하는 내부 분석가입니다.\n"
        "아래 JSON의 company_projects는 우리 회사 과거 수행/관심 사업이고 notice는 새 공고입니다.\n"
        "단순 키워드 포함 여부가 아니라 실제 수행업무, 기관, 분야, 기술역량 유사성을 기준으로 판단하세요.\n"
        "검색어에 걸려 수집된 공고만 평가하는 단계이므로, 관련성이 낮으면 낮다고 판단하세요.\n"
        "반드시 JSON 객체 하나만 출력하세요. 마크다운 코드블록은 쓰지 마세요.\n"
        "스키마:\n"
        "{\n"
        '  "fit_score": 0-100,\n'
        '  "fit_level": "low|medium|high",\n'
        '  "confidence": "low|medium|high",\n'
        '  "recommended_action": "ignore|watch|review|bid",\n'
        '  "reason": "판단 근거 1문장",\n'
        '  "matched_capabilities": ["매칭된 회사 역량"],\n'
        '  "risks": ["검토 리스크"],\n'
        '  "summary_for_slack": "슬랙에 표시할 1문장"\n'
        "}\n\n"
        f"입력 JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _call_openclaw_gateway(settings, prompt: str) -> dict:
    url = str(getattr(settings, "ai_relevance_gateway_url", "")).strip()
    if not url:
        raise RuntimeError("AI_RELEVANCE_GATEWAY_URL is not configured")
    headers = {"Content-Type": "application/json"}
    token = str(getattr(settings, "ai_relevance_gateway_token", "")).strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout_seconds = int(getattr(settings, "ai_relevance_timeout_seconds", 90))
    response = requests.post(
        url,
        headers=headers,
        json={
            "agent": str(getattr(settings, "ai_relevance_agent", "cron-google")),
            "prompt": prompt,
            "timeout_seconds": timeout_seconds,
        },
        timeout=timeout_seconds + 10,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text[:1000]
        raise RuntimeError(
            f"gateway_http_{response.status_code}: {body}"
        ) from exc
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("OpenClaw gateway returned non-object response")
    if data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "OpenClaw gateway returned ok=false"))
    return data


def _parse_gateway_response(gateway_response: dict) -> dict:
    raw_text = _gateway_text(gateway_response)
    if not raw_text:
        raise RuntimeError("OpenClaw gateway response is empty")
    try:
        parsed = _extract_json_object(raw_text)
    except Exception:
        parsed = _extract_structured_fields_from_text(raw_text)
    fit_score = max(0, min(100, int(parsed.get("fit_score") or 0)))
    fit_level = str(parsed.get("fit_level") or _fit_level_from_score(fit_score)).lower()
    if fit_level not in {"low", "medium", "high"}:
        fit_level = _fit_level_from_score(fit_score)
    confidence = str(parsed.get("confidence") or "medium").lower()
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"
    action = str(parsed.get("recommended_action") or "").lower()
    if action not in {"ignore", "watch", "review", "bid"}:
        action = _action_from_score(fit_score)
    return {
        "fit_score": fit_score,
        "fit_level": fit_level,
        "confidence": confidence,
        "recommended_action": action,
        "reason": normalize_text(str(parsed.get("reason") or ""))[:1000],
        "summary_for_slack": normalize_text(str(parsed.get("summary_for_slack") or ""))[:500],
        "matched_capabilities": _coerce_str_list(parsed.get("matched_capabilities"))[:10],
        "risks": _coerce_str_list(parsed.get("risks"))[:10],
    }


def _extract_structured_fields_from_text(text: str) -> dict:
    normalized = normalize_text(text)
    if not normalized:
        raise RuntimeError("AI relevance response is empty")

    fit_score_match = re.search(r"fit[_ ]?score[^0-9]{0,20}(\d{1,3})", normalized, flags=re.IGNORECASE)
    fit_score = int(fit_score_match.group(1)) if fit_score_match else 0

    fit_level_match = re.search(r"\b(high|medium|low)\b", normalized, flags=re.IGNORECASE)
    fit_level = fit_level_match.group(1).lower() if fit_level_match else _fit_level_from_score(fit_score)

    confidence_match = re.search(r"confidence[^a-z]{0,20}(high|medium|low)", normalized, flags=re.IGNORECASE)
    confidence = confidence_match.group(1).lower() if confidence_match else "medium"

    action_match = re.search(r"\b(ignore|watch|review|bid|participate)\b", normalized, flags=re.IGNORECASE)
    action = action_match.group(1).lower() if action_match else _action_from_score(fit_score)
    if action == "participate":
        action = "bid"

    first_sentence = normalized.split(". ")[0].strip()
    reason = first_sentence or normalized[:300]

    return {
        "fit_score": fit_score,
        "fit_level": fit_level,
        "confidence": confidence,
        "recommended_action": action,
        "reason": reason,
        "summary_for_slack": reason,
        "matched_capabilities": [],
        "risks": [],
    }


def _gateway_text(gateway_response: dict) -> str:
    direct = (
        gateway_response.get("response")
        or gateway_response.get("text")
        or gateway_response.get("message")
    )
    if direct:
        return str(direct).strip()
    result = gateway_response.get("result")
    if isinstance(result, dict):
        payloads = result.get("payloads")
        if isinstance(payloads, list) and payloads:
            first = payloads[0]
            if isinstance(first, dict) and first.get("text"):
                return str(first["text"]).strip()
    return ""


def _gateway_provider(gateway_response: dict) -> str:
    result = gateway_response.get("result")
    if isinstance(result, dict):
        meta = result.get("meta")
        if isinstance(meta, dict):
            agent_meta = meta.get("agentMeta")
            if isinstance(agent_meta, dict) and agent_meta.get("provider"):
                return str(agent_meta["provider"])
    return str(gateway_response.get("provider") or DEFAULT_PROVIDER)


def _gateway_model(gateway_response: dict, fallback: str) -> str:
    result = gateway_response.get("result")
    if isinstance(result, dict):
        meta = result.get("meta")
        if isinstance(meta, dict):
            agent_meta = meta.get("agentMeta")
            if isinstance(agent_meta, dict) and agent_meta.get("model"):
                return str(agent_meta["model"])
    return str(gateway_response.get("model") or fallback)


def _extract_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise RuntimeError("AI relevance response is not a JSON object")
    return parsed


def _hash_payload(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalize_text(str(item)) for item in value if normalize_text(str(item))]
    if isinstance(value, str) and value.strip():
        return [normalize_text(value)]
    return []


def _loads_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return _coerce_str_list(parsed)


def _fit_level_from_score(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _action_from_score(score: int) -> str:
    if score >= 85:
        return "bid"
    if score >= 70:
        return "review"
    if score >= 45:
        return "watch"
    return "ignore"


def _fit_level_label(value: str) -> str:
    return {"high": "높음", "medium": "보통", "low": "낮음"}.get(value, value or "미확인")


def _action_label(value: str) -> str:
    return {
        "bid": "참여 검토",
        "review": "검토 권장",
        "watch": "관찰",
        "ignore": "제외 권장",
    }.get(value, value or "미확인")


def _clip(value: str, limit: int = 180) -> str:
    value = normalize_text(value)
    return value if len(value) <= limit else value[: limit - 1] + "…"
