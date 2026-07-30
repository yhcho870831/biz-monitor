from __future__ import annotations

import os
import time
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.collectors.g2b import PRE_SPECIFICATION_LIST_URL
from app.services.deadline import G2B_PRE_SPECIFICATION_STAGES
from app.services.notice_meta import format_notice_tag, format_priority_stars
from app.types import NoticeCandidate
from app.utils import extract_datetimes, normalize_text

SITE_DISPLAY_NAMES = {
    "g2b": "\ub098\ub77c\uc7a5\ud130",
    "kimst": "KIMST",
    "nia": "NIA",
    "d2b": "D2B",
    "kmiti": "\uae30\uc0c1\uc0b0\uc5c5\uae30\uc220\uc6d0",
    "iris": "IRIS",
}

TABLE_ROWS_PER_MESSAGE = 8
_POSTING_DATE_MARKERS = ("작성일", "등록일", "게시일")


class SlackNotifier:
    def __init__(self, token: str, retry_count: int = 1):
        timeout_seconds = float((os.getenv("SLACK_API_TIMEOUT_SECONDS", "20") or "20").strip())
        self.client = WebClient(token=token, timeout=timeout_seconds)
        self.retry_count = retry_count

    def _retry_after_seconds(self, exc: SlackApiError) -> float:
        headers = getattr(exc.response, "headers", {}) or {}
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        try:
            return max(1.0, float(retry_after))
        except (TypeError, ValueError):
            return 30.0

    def _should_backoff(self, exc: SlackApiError) -> bool:
        try:
            return exc.response.get("error") == "ratelimited"
        except Exception:
            return False

    def _is_optional_file_error(self, exc: SlackApiError) -> bool:
        try:
            return exc.response.get("error") in {"missing_scope", "not_allowed_token_type"}
        except Exception:
            return False

    def send_text(self, channel_id: str, text: str, thread_ts: str = "") -> str:
        last_error = None
        for _ in range(max(self.retry_count, 3) + 1):
            try:
                payload = {
                    "channel": channel_id,
                    "text": text,
                    "unfurl_links": False,
                    "unfurl_media": False,
                }
                if thread_ts:
                    payload["thread_ts"] = thread_ts
                response = self.client.chat_postMessage(**payload)
                return response["ts"]
            except SlackApiError as exc:  # pragma: no cover
                last_error = exc
                if self._should_backoff(exc):
                    time.sleep(self._retry_after_seconds(exc) + 1.0)
                    continue
                if self._is_optional_file_error(exc):
                    return ""
                break
            except Exception as exc:  # pragma: no cover
                last_error = exc
        raise last_error

    def send_file(
        self,
        channel_id: str,
        file_path: str,
        title: str = "",
        initial_comment: str = "",
        thread_ts: str = "",
    ) -> str:
        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        last_error = None
        for _ in range(max(self.retry_count, 3) + 1):
            try:
                payload = {
                    "channel": channel_id,
                    "file": file_path,
                }
                if title:
                    payload["title"] = title
                if initial_comment:
                    payload["initial_comment"] = initial_comment
                if thread_ts:
                    payload["thread_ts"] = thread_ts
                response = self.client.files_upload_v2(**payload)
                files = response.get("files", [])
                if files:
                    return files[0].get("id", "")
                return ""
            except SlackApiError as exc:  # pragma: no cover
                last_error = exc
                if self._should_backoff(exc):
                    time.sleep(self._retry_after_seconds(exc) + 1.0)
                    continue
                break
            except Exception as exc:  # pragma: no cover
                last_error = exc
        raise last_error

    def delete_file(self, file_id: str) -> None:
        if not file_id:
            return
        last_error = None
        for _ in range(max(self.retry_count, 3) + 1):
            try:
                self.client.files_delete(file=file_id)
                return
            except SlackApiError as exc:  # pragma: no cover
                last_error = exc
                if self._should_backoff(exc):
                    time.sleep(self._retry_after_seconds(exc) + 1.0)
                    continue
                break
            except Exception as exc:  # pragma: no cover
                last_error = exc
        raise last_error


def _truncate(value: str, width: int) -> str:
    value = (value or "").replace("\n", " ").strip()
    if len(value) <= width:
        return value.ljust(width)
    return (value[: max(1, width - 1)] + "\u2026").ljust(width)


def _format_amount_short(amount_value: int | None) -> str:
    if amount_value is None:
        return "\ubbf8\uae30\uc7ac"
    if amount_value >= 100_000_000:
        amount_in_eok = amount_value / 100_000_000
        if amount_value % 100_000_000 == 0:
            return f"{int(amount_in_eok)}\uc5b5"
        return f"{amount_in_eok:.1f}\uc5b5"
    if amount_value >= 10_000_000:
        return f"{amount_value / 10_000_000:.0f}\ucc9c\ub9cc"
    return f"{amount_value:,}\uc6d0"


def _sort_candidate_key(candidate: NoticeCandidate) -> tuple:
    deadline = (
        candidate.deadline_at
        or _period_text_deadline(candidate)
        or candidate.open_at
        or candidate.start_at
    )
    deadline_key = deadline.isoformat() if deadline else "9999-12-31T23:59:59"
    return (-candidate.priority_score, deadline_key, candidate.title)


def _period_text_deadline(candidate: NoticeCandidate):
    text = candidate.period_text or ""
    if any(marker in text for marker in _POSTING_DATE_MARKERS):
        return None
    dates = extract_datetimes(text)
    return dates[-1] if dates else None


def _display_deadline(candidate: NoticeCandidate) -> str:
    deadline = (
        candidate.deadline_at
        or _period_text_deadline(candidate)
        or candidate.open_at
        or candidate.start_at
    )
    if deadline is None:
        return "\ubbf8\uae30\uc7ac"
    return deadline.strftime("%Y-%m-%d %H:%M")


def _announcement_stage(candidate: NoticeCandidate) -> str:
    return str((candidate.raw_payload or {}).get("announcement_stage") or "")


def _display_schedule(candidate: NoticeCandidate) -> str:
    """Use a field name that matches the lifecycle represented by the row."""
    stage = _announcement_stage(candidate)
    if stage in G2B_PRE_SPECIFICATION_STAGES:
        # 의견등록마감일시 only exists on the detail screen, which the list route
        # does not carry, so fall back to the disclosure date and status.
        if candidate.deadline_at is not None:
            return "\uc758\uacac\ub4f1\ub85d \ub9c8\uac10: %s" % _display_deadline(candidate)
        opened_at = candidate.start_at or _period_text_deadline(candidate)
        value = opened_at.strftime("%Y-%m-%d %H:%M") if opened_at else "\ubbf8\uae30\uc7ac"
        status = normalize_text(
            str((candidate.raw_payload or {}).get("oderPlanPgstNm") or "")
        )
        if status:
            value = "%s | \uc0c1\ud0dc: %s" % (value, status)
        return "\uacf5\uac1c\uc77c: %s" % value
    return "\uc785\ucc30\ub9c8\uac10: %s" % _display_deadline(candidate)


def _pre_specification_reg_no(candidate: NoticeCandidate) -> str:
    if _announcement_stage(candidate) not in G2B_PRE_SPECIFICATION_STAGES:
        return ""
    raw_payload = candidate.raw_payload or {}
    for key in ("bfSpecRegNo", "oderPlanNo"):
        value = normalize_text(str(raw_payload.get(key) or ""))
        if value:
            return value
    return normalize_text(candidate.notice_no or "")


def _preannouncement_label(candidate: NoticeCandidate) -> str:
    raw_payload = candidate.raw_payload or {}
    if raw_payload.get("iris_result_type") == "schedule":
        return "[\uc0ac\uc804\uacf5\uace0] "
    if raw_payload.get("announcement_stage") in G2B_PRE_SPECIFICATION_STAGES:
        return "[\uc0ac\uc804\uaddc\uaca9] "
    return ""


def _display_source_url(candidate: NoticeCandidate) -> str:
    """Rows from the 발주목록 route stored the portal home as their source URL.

    Point them at the 사전규격공개 list instead, so the registration number can
    be looked up without re-collecting every stored row.
    """
    if _announcement_stage(candidate) in G2B_PRE_SPECIFICATION_STAGES:
        return PRE_SPECIFICATION_LIST_URL
    return candidate.source_url or ""


def _title_link_for_candidate(
    candidate: NoticeCandidate,
    title_link_override: str | None = None,
) -> str | None:
    return (title_link_override or _display_source_url(candidate)).strip() or None


def _format_notice_title(
    candidate: NoticeCandidate,
    title_link_override: str | None = None,
) -> str:
    ai_label = " [AI 추천]" if (candidate.raw_payload or {}).get("ai_recommended") == "true" else ""
    title = f"{_preannouncement_label(candidate)}{candidate.title}{ai_label}"
    title_link = _title_link_for_candidate(candidate, title_link_override)
    if title_link:
        return f"<{title_link}|{title}>"
    return title


def format_notice(candidate: NoticeCandidate, title_link_override: str | None = None) -> str:
    lines = [
        "[%s] [%s] [%s] %s"
        % (
            SITE_DISPLAY_NAMES.get(candidate.site_code, candidate.site_code.upper()),
            format_notice_tag(candidate.notice_tag),
            format_priority_stars(candidate.priority_score),
            _format_notice_title(candidate, title_link_override),
        ),
        "\ubc1c\uc8fc\ucc98: %s" % (candidate.organization or "\ubbf8\uae30\uc7ac"),
        "\ud0dc\uadf8: %s" % format_notice_tag(candidate.notice_tag),
        _display_schedule(candidate),
        "\uc911\uc694\ub3c4: %s" % format_priority_stars(candidate.priority_score),
        "\ub9c1\ud06c: %s" % _display_source_url(candidate),
    ]
    if candidate.amount_value is not None:
        lines.insert(4, "\uae08\uc561: %s" % f"{candidate.amount_value:,}\uc6d0")
    # The list route has no per-row detail URL, so the registration number is the
    # only way for a reader to locate the record on the linked screen.
    reg_no = _pre_specification_reg_no(candidate)
    if reg_no:
        lines.insert(3, "\uc0ac\uc804\uaddc\uaca9\ub4f1\ub85d\ubc88\ud638: %s" % reg_no)
    return "\n".join(lines)


def format_manual_header(site_code: str, term: str, count: int) -> str:
    return "\uc218\ub3d9 \uac80\uc0c9 \uacb0\uacfc - %s / %s (%s\uac74)" % (
        SITE_DISPLAY_NAMES.get(site_code, site_code.upper()),
        term,
        count,
    )


def format_empty_manual(site_code: str, term: str) -> str:
    return "[%s] `%s` \uac80\uc0c9\uacb0\uacfc 0\uac74\uc785\ub2c8\ub2e4." % (
        SITE_DISPLAY_NAMES.get(site_code, site_code.upper()),
        term,
    )


def format_empty_scheduled(run_at: datetime, site_counts: list[tuple[str, int]]) -> str:
    header = "%s\uc6d4 %s\uc77c %s\uc2dc \uae30\uc900 \uac80\uc0c9\uacb0\uacfc 0\uac74\uc785\ub2c8\ub2e4." % (
        run_at.month,
        run_at.day,
        run_at.hour,
    )
    lines = [header]
    for site_name, count in site_counts:
        lines.append("%s : %s\uac74" % (site_name, count))
    return "\n".join(lines)


def format_no_share_scheduled(run_at: datetime, site_counts: list[tuple[str, int]]) -> str:
    header = "%s\uc6d4 %s\uc77c %s\uc2dc \uae30\uc900 \uacf5\uc720\ud560 \uacf5\uace0\uac00 \uc5c6\uc2b5\ub2c8\ub2e4." % (
        run_at.month,
        run_at.day,
        run_at.hour,
    )
    lines = [header]
    for site_name, count in site_counts:
        lines.append("%s : %s\uac74" % (site_name, count))
    return "\n".join(lines)


def format_site_notice_tables(
    site_code: str,
    candidates: list[NoticeCandidate],
    rows_per_message: int = TABLE_ROWS_PER_MESSAGE,
    title_link_overrides: dict[str, str] | None = None,
    header_note: str = "",
) -> list[str]:
    if not candidates:
        return []

    sorted_candidates = sorted(candidates, key=_sort_candidate_key)
    chunks = [
        sorted_candidates[index : index + rows_per_message]
        for index in range(0, len(sorted_candidates), rows_per_message)
    ]
    site_name = SITE_DISPLAY_NAMES.get(site_code, site_code.upper())
    messages: list[str] = []

    for chunk_index, chunk in enumerate(chunks, start=1):
        title = f"*{site_name}*"
        if len(chunks) > 1:
            title = f"*{site_name} ({chunk_index}/{len(chunks)})*"

        lines = [title]
        if chunk_index == 1 and header_note:
            lines.append(header_note)

        for row_index, candidate in enumerate(chunk, start=1 + (chunk_index - 1) * rows_per_message):
            title_link = None
            if title_link_overrides:
                title_link = title_link_overrides.get(candidate.site_notice_key or "")
            title_text = _format_notice_title(candidate, title_link)
            lines.append(
                "%s. %s %s %s"
                % (
                    row_index,
                    format_priority_stars(candidate.priority_score),
                    format_notice_tag(candidate.notice_tag).split(" ", 1)[0],
                    title_text,
                )
            )
            lines.append(
                "   %s | \ubc1c\uc8fc\ucc98: %s | \uae08\uc561: %s"
                % (
                    _display_schedule(candidate),
                    candidate.organization or "\ubbf8\uae30\uc7ac",
                    _format_amount_short(candidate.amount_value),
                )
            )

        messages.append("\n".join(lines))

    return messages


def get_notice_screenshot_path(candidate: NoticeCandidate) -> str:
    return str((candidate.raw_payload or {}).get("screenshot_path", "")).strip()
