from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from zipfile import ZipFile

import requests
from bs4 import BeautifulSoup

from app.models import Notice
from app.repositories.attachments import (
    list_attachments_for_notice_id,
    set_summary_source_attachment,
)
from app.repositories.summaries import get_notice_summary, upsert_notice_summary
from app.types import NoticeCandidate
from app.utils import normalize_text

logger = logging.getLogger(__name__)

SECTION_STOP_PATTERN = re.compile(
    r"^\s*(?:[ⅠⅡⅢⅣⅤ]|[IVX]+\.)|^\s*\d+\.\s|^\s*[가-힣]\.\s|^\s*【",
    re.MULTILINE,
)

SUMMARY_LABELS = {
    "purpose": ("사업목적", "미확인"),
    "core_tasks": ("핵심수행업무", "미확인"),
    "required_performance": ("요구성능", "미확인"),
    "quantitative_targets": ("정량 목표", "미확인"),
    "period_text": ("기간", "미확인"),
}


def should_summarize_notice(site_code: str, priority_score: int) -> bool:
    return priority_score >= 1 and site_code in {"g2b", "d2b", "iris"}


def _notice_key_slug(notice: Notice) -> str:
    raw = notice.site_notice_key or notice.title or f"notice-{notice.id}"
    return re.sub(r"[^0-9A-Za-z._-가-힣]+", "_", raw).strip("._")[:120] or f"notice-{notice.id}"


def _summary_output_dir(settings, notice: Notice) -> Path:
    target = Path(settings.download_dir) / "extracted" / notice.site_code / _notice_key_slug(notice)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _read_hwpx_text(path: Path) -> str:
    def _best_decode(raw: bytes) -> str:
        keyword_pattern = re.compile(
            "|".join(
                re.escape(token)
                for token in (
                    "사업",
                    "과업",
                    "제안",
                    "공고",
                    "기간",
                    "용역",
                    "연구",
                    "목적",
                    "수행",
                    "개발",
                    "목표",
                    "성능",
                    "평가",
                    "검증",
                )
            )
        )
        candidates: list[tuple[int, str]] = []
        for enc in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp949"):
            try:
                decoded = raw.decode(enc)
            except Exception:
                continue
            text = decoded.replace("\x00", "")
            if not text.strip():
                continue
            printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
            score = printable + sum("가" <= ch <= "힣" for ch in text) * 4 + sum(
                ch.isascii() and (ch.isalnum() or ch in " .,:/%()-_[]") for ch in text
            )
            score += len(keyword_pattern.findall(text)) * 120
            score -= sum("\u4e00" <= ch <= "\u9fff" for ch in text) * 10
            score -= text.count("\ufffd") * 80
            score -= sum(ord(ch) < 32 and ch not in "\r\n\t" for ch in text) * 120
            if any(token in text for token in ("사업목적", "과업", "제안요청서", "공고문")):
                score += 250
            candidates.append((score, text))
        return max(candidates, key=lambda item: item[0])[1] if candidates else ""

    with ZipFile(path) as archive:
        if "Preview/PrvText.txt" in archive.namelist():
            raw = archive.read("Preview/PrvText.txt")
            text = _best_decode(raw)
            if text:
                return text
        sections = []
        for name in archive.namelist():
            if name.startswith("Contents/section") and name.endswith(".xml"):
                try:
                    sections.append(archive.read(name).decode("utf-8", errors="ignore"))
                except Exception:
                    continue
        return "\n".join(sections)


def _read_pdf_text(path: Path) -> str:
    for module_name in ("pypdf", "PyPDF2"):
        try:
            if module_name == "pypdf":
                from pypdf import PdfReader  # type: ignore
            else:
                from PyPDF2 import PdfReader  # type: ignore
            reader = PdfReader(str(path))
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            return "\n".join(pages)
        except Exception:
            continue
    return ""


def _extract_attachment_text(stored_path: str) -> str:
    path = Path(stored_path)
    suffix = path.suffix.lower()
    if suffix == ".hwpx":
        return _read_hwpx_text(path)
    if suffix == ".pdf":
        return _read_pdf_text(path)
    return ""


def _select_summary_attachment(session, notice_id: int):
    attachments = list_attachments_for_notice_id(session, notice_id)
    if not attachments:
        return None

    def sort_key(attachment):
        suffix = Path(attachment.attachment_name).suffix.lower()
        extension_rank = {".hwpx": 0, ".pdf": 1, ".hwp": 2}.get(suffix, 9)
        return (attachment.priority_rank, extension_rank, attachment.attachment_name)

    selected = sorted(attachments, key=sort_key)[0]
    set_summary_source_attachment(session, notice_id, selected.id, commit=False)
    return selected


def _extract_visible_iris_text(source_url: str) -> str:
    response = requests.get(source_url, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    pieces: list[str] = []

    for selector in [
        "table td",
        "table th",
        ".conts",
        ".contents",
        ".view_cont",
        ".board-view",
        ".tbl_view",
        "article",
        "section",
        "p",
        "li",
    ]:
        for node in soup.select(selector):
            text = normalize_text(node.get_text(" ", strip=True))
            if len(text) >= 8:
                pieces.append(text)

    if not pieces:
        pieces.append(normalize_text(soup.get_text(" ", strip=True)))

    seen: set[str] = set()
    ordered: list[str] = []
    for piece in pieces:
        if not piece or piece in seen:
            continue
        seen.add(piece)
        ordered.append(piece)
    return "\n".join(ordered)


def _build_iris_fallback_text(notice: Notice) -> str:
    payload = json.loads(notice.raw_payload_json or "{}") if notice.raw_payload_json else {}
    lines = [
        f"사업명: {notice.title}",
        f"전문기관: {notice.organization or '미기재'}",
        f"기간: {notice.period_text or '미기재'}",
    ]
    if payload.get("iris_result_type") == "schedule":
        lines.append("구분: 공모예고(사업일정)")
    return "\n".join(lines)


def _extract_section(text: str, headings: list[str], fallback_lines: int = 2) -> str:
    normalized_lines = [
        normalize_text(line)
        for line in text.replace("\r", "\n").splitlines()
        if normalize_text(line)
    ]
    stop_pattern = re.compile(r"^(?:[가-힣]\.\s+|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+\.\s+|【[^】]+】)")
    section_marker_pattern = re.compile(r"^\d+\)\s+")
    for heading in headings:
        for index, line in enumerate(normalized_lines):
            lower_line = line.lower()
            pos = lower_line.find(heading.lower())
            if pos < 0:
                continue
            collected: list[str] = []
            inline = normalize_text(line[pos + len(heading) :].lstrip(" :：-"))
            if inline:
                collected.append(inline)
            for following in normalized_lines[index + 1 :]:
                if not collected and (stop_pattern.match(following) or section_marker_pattern.match(following)):
                    continue
                if collected and stop_pattern.match(following):
                    break
                if section_marker_pattern.match(following):
                    continue
                collected.append(following)
                if len(collected) >= max(4, fallback_lines * 3):
                    break
            collected = [item for item in collected if len(item) >= 4]
            if collected:
                return " / ".join(collected[:fallback_lines])
    return ""


def _extract_quantitative_targets(text: str) -> str:
    metric_patterns = [
        re.compile(r"(정확도|식별률|재현율|정밀도|f1|mAP|인식률)\s*[:=]?\s*\d", re.IGNORECASE),
        re.compile(r"\d+\s*%\s*(이상|이하|목표|달성)?"),
        re.compile(r"\d+\s*(종|건|개|시간|회|명|식)\s*(이상|이하|목표|구축|확보|달성)?"),
    ]
    reject_tokens = (
        "추진배경",
        "필요",
        "활용",
        "선진국",
        "연구기관",
        "축적",
        "구축하고",
        "분야에서",
        "가능",
        "기대효과",
    )
    matches = []
    for line in text.splitlines():
        normalized = normalize_text(line)
        if not normalized:
            continue
        if "과업기간" in normalized or "계약일로부터" in normalized:
            continue
        if any(token in normalized for token in reject_tokens):
            continue
        if not any(pattern.search(normalized) for pattern in metric_patterns):
            continue
        matches.append(normalized)
    return " / ".join(matches[:3])


def _extract_period_text(text: str, notice: Notice) -> str:
    extracted = _extract_section(
        text,
        ["과업기간", "연구기간", "사업기간", "기간"],
        fallback_lines=1,
    )
    if extracted:
        return extracted
    return notice.period_text or "미확인"


def _trim_toc_prefix(text: str) -> str:
    for marker in ("Ⅰ. 과업에 대한 사항", "I. 과업에 대한 사항"):
        first = text.find(marker)
        if first < 0:
            continue
        second = text.find(marker, first + len(marker))
        if second > first:
            return text[second:]
    return text


def _summarize_notice_text(text: str, notice: Notice) -> dict[str, str]:
    text = _trim_toc_prefix(text)
    cleaned = normalize_text(text)
    cleaned_lines = [
        normalize_text(line)
        for line in text.replace("\r", "\n").splitlines()
        if normalize_text(line)
    ]
    compact_text = "\n".join(cleaned_lines)

    purpose = _extract_section(
        compact_text,
        ["사업목적", "추진배경 및 필요성", "배경 및 필요성", "목적", "과업 개요"],
        fallback_lines=3,
    )
    if not purpose:
        purpose = f"{notice.title} 관련 사업"

    core_tasks = _extract_section(
        compact_text,
        ["과업 수행 방안", "주요내용", "핵심 내용", "과업내용", "연구내용", "지원내용"],
        fallback_lines=4,
    )
    if not core_tasks:
        core_tasks = "상세 공고문 확인 필요"

    required_performance = _extract_section(
        compact_text,
        ["요구성능", "성능 평가 및 검증", "성과목표", "목표"],
        fallback_lines=3,
    )
    if not required_performance:
        performance_lines = [
            line
            for line in cleaned_lines
            if any(token in line for token in ("정확도", "식별률", "성능", "평가", "검증", "구현"))
        ]
        required_performance = " / ".join(performance_lines[:3]) or "미확인"

    quantitative_targets = _extract_quantitative_targets(compact_text) or "미확인"
    period_text = _extract_period_text(compact_text, notice)

    return {
        "purpose": purpose,
        "core_tasks": core_tasks,
        "required_performance": required_performance,
        "quantitative_targets": quantitative_targets,
        "period_text": period_text,
    }


def generate_notice_summary(
    session,
    settings,
    notice: Notice,
    candidate: NoticeCandidate | None = None,
) -> dict | None:
    payload = json.loads(notice.raw_payload_json or "{}") if notice.raw_payload_json else {}
    priority_score = int(
        candidate.priority_score if candidate is not None else payload.get("priority_score", 0) or 0
    )
    if priority_score <= 0 and notice.site_code in {"g2b", "d2b"}:
        if list_attachments_for_notice_id(session, notice.id):
            priority_score = 1
    if not should_summarize_notice(notice.site_code, priority_score):
        return None

    source_type = "notice_body"
    attachment_id = None
    extracted_text = ""

    if notice.site_code in {"g2b", "d2b"}:
        attachment = _select_summary_attachment(session, notice.id)
        if attachment is None:
            summary = upsert_notice_summary(
                session,
                notice_id=notice.id,
                attachment_id=None,
                source_type="attachment",
                summary_status="failed",
                failure_reason="summary_source_attachment_not_found",
                purpose=None,
                core_tasks=None,
                required_performance=None,
                quantitative_targets=None,
                period_text=None,
                raw_extracted_text_path=None,
                commit=True,
            )
            return {
                "notice_id": notice.id,
                "summary_status": summary.summary_status,
                "failure_reason": summary.failure_reason,
            }
        source_type = "attachment"
        attachment_id = attachment.id
        extracted_text = _extract_attachment_text(attachment.stored_path)
    elif notice.site_code == "iris":
        try:
            extracted_text = _extract_visible_iris_text(notice.source_url)
        except Exception:
            logger.exception("iris summary text fetch failed notice_id=%s", notice.id)
            extracted_text = _build_iris_fallback_text(notice)
        source_type = "notice_body"

    if not normalize_text(extracted_text):
        summary = upsert_notice_summary(
            session,
            notice_id=notice.id,
            attachment_id=attachment_id,
            source_type=source_type,
            summary_status="failed",
            failure_reason="extracted_text_empty",
            purpose=None,
            core_tasks=None,
            required_performance=None,
            quantitative_targets=None,
            period_text=None,
            raw_extracted_text_path=None,
            commit=True,
        )
        return {
            "notice_id": notice.id,
            "summary_status": summary.summary_status,
            "failure_reason": summary.failure_reason,
        }

    output_dir = _summary_output_dir(settings, notice)
    source_name = "attachment" if source_type == "attachment" else "notice_body"
    extracted_path = output_dir / f"{source_name}.txt"
    extracted_path.write_text(extracted_text, encoding="utf-8")

    summary_fields = _summarize_notice_text(extracted_text, notice)
    summary = upsert_notice_summary(
        session,
        notice_id=notice.id,
        attachment_id=attachment_id,
        source_type=source_type,
        summary_status="done",
        failure_reason=None,
        purpose=summary_fields["purpose"],
        core_tasks=summary_fields["core_tasks"],
        required_performance=summary_fields["required_performance"],
        quantitative_targets=summary_fields["quantitative_targets"],
        period_text=summary_fields["period_text"],
        raw_extracted_text_path=str(extracted_path),
        commit=True,
    )
    return {
        "notice_id": notice.id,
        "summary_status": summary.summary_status,
        "purpose": summary.purpose,
        "core_tasks": summary.core_tasks,
        "required_performance": summary.required_performance,
        "quantitative_targets": summary.quantitative_targets,
        "period_text": summary.period_text,
    }


def get_notice_summary_payload(session, notice_id: int) -> dict | None:
    summary = get_notice_summary(session, notice_id)
    if summary is None:
        return None
    return {
        "notice_id": summary.notice_id,
        "attachment_id": summary.attachment_id,
        "source_type": summary.source_type,
        "summary_status": summary.summary_status,
        "failure_reason": summary.failure_reason,
        "purpose": summary.purpose or SUMMARY_LABELS["purpose"][1],
        "core_tasks": summary.core_tasks or SUMMARY_LABELS["core_tasks"][1],
        "required_performance": summary.required_performance or SUMMARY_LABELS["required_performance"][1],
        "quantitative_targets": summary.quantitative_targets or SUMMARY_LABELS["quantitative_targets"][1],
        "period_text": summary.period_text or SUMMARY_LABELS["period_text"][1],
        "raw_extracted_text_path": summary.raw_extracted_text_path,
    }


def format_summary_lines(summary_payload: dict, *, prefix: str = "") -> list[str]:
    if not summary_payload or summary_payload.get("summary_status") != "done":
        return []
    title_prefix = f"{prefix} " if prefix else ""

    def _normalize_summary_value(value: str) -> str:
        parts = []
        for raw_part in normalize_text(value).split("/"):
            part = normalize_text(raw_part).lstrip("◦-· ").strip()
            if part:
                parts.append(part)
        return " / ".join(parts)

    def _clip(value: str) -> str:
        text = _normalize_summary_value(value)
        return text if len(text) <= 220 else text[:219] + "…"
    return [
        f"{title_prefix}사업목적: {_clip(summary_payload['purpose'])}",
        f"{title_prefix}핵심수행업무: {_clip(summary_payload['core_tasks'])}",
        f"{title_prefix}요구성능: {_clip(summary_payload['required_performance'])}",
        f"{title_prefix}정량 목표: {_clip(summary_payload['quantitative_targets'])}",
        f"{title_prefix}기간: {_clip(summary_payload['period_text'])}",
    ]
