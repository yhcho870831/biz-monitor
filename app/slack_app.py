from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.config import Settings
from app.repositories.shares import list_shared_notices
from app.repositories.shares import record_file_share
from app.repositories.sites import add_site_keyword, disable_site_keyword, list_enabled_site_keywords
from app.services.commands import parse_manual_command
from app.services.deadline import is_active_notice
from app.services.notice_meta import enrich_notice_candidate
from app.services.notifier import format_notice, get_notice_screenshot_path
from app.types import NoticeCandidate

logger = logging.getLogger(__name__)

SITE_DISPLAY_NAMES = {
    "g2b": "\ub098\ub77c\uc7a5\ud130",
    "kimst": "KIMST",
    "nia": "NIA",
    "d2b": "D2B",
    "kmiti": "\uae30\uc0c1\uc0b0\uc5c5\uae30\uc220\uc6d0",
    "iris": "IRIS",
}

HELP_MESSAGE = (
    "지원 명령어를 안내했습니다.\n"
    "1. 검색어 추가\n"
    "- @biz-monitor 공고:{사이트명}/{검색어} 추가\n"
    "- 예: @biz-monitor 공고:KIMST/국제사회IUU 전자모니터링 시스템 추가\n\n"
    "2. 검색어 삭제\n"
    "- @biz-monitor 공고:{사이트명}/{검색어} 삭제\n"
    "- 예: @biz-monitor 공고:KIMST/국제사회IUU 전자모니터링 시스템 삭제\n\n"
    "3. 검색어 목록 조회\n"
    "- @biz-monitor 검색어 보여줘\n\n"
    "4. 중요 공고 조회\n"
    "- @biz-monitor 공고리스트\n"
    "- 현재 공유된 공고 중 ★★★ 공고를 보여줍니다.\n\n"
    "5. 일정표 링크 조회\n"
    "- @biz-monitor 일정표\n\n"
    "6. 도움말\n"
    "- @biz-monitor 도움\n"
    "- @biz-monitor 명령어\n\n"
    "사용 가능한 사이트명 예시\n"
    "- 나라장터, NIA, KIMST, IRIS, 기상산업기술원, D2B"
)


def _format_keyword_list(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return "\uac80\uc0c9\uc5b4\ub97c \uc870\ud68c\ud588\uc2b5\ub2c8\ub2e4.\n\ub4f1\ub85d\ub41c \uac80\uc0c9\uc5b4\uac00 \uc5c6\uc2b5\ub2c8\ub2e4."

    grouped: dict[str, list[str]] = defaultdict(list)
    for site_code, keyword in rows:
        grouped[site_code].append(keyword)

    site_order = ["g2b", "nia", "kimst", "iris", "kmiti", "d2b"]
    ordered_site_codes = [code for code in site_order if code in grouped] + [
        code for code in sorted(grouped.keys()) if code not in site_order
    ]

    total = sum(len(keywords) for keywords in grouped.values())
    lines = [
        "\uac80\uc0c9\uc5b4\ub97c \uc870\ud68c\ud588\uc2b5\ub2c8\ub2e4.",
        f"\ucd1d {total}\uac1c",
        "",
    ]
    for site_code in ordered_site_codes:
        keywords = sorted(grouped[site_code], key=lambda value: value.lower())
        lines.append(
            "*%s* (%s)"
            % (SITE_DISPLAY_NAMES.get(site_code, site_code.upper()), len(keywords))
        )
        for keyword in keywords:
            lines.append("\u2022 %s" % keyword)
        lines.append("")
    return "\n".join(lines).strip()


def _notice_from_row(notice) -> NoticeCandidate:
    raw_payload = {}
    if notice.raw_payload_json:
        try:
            raw_payload = json.loads(notice.raw_payload_json)
        except json.JSONDecodeError:
            raw_payload = {}

    return NoticeCandidate(
        site_code=notice.site_code,
        site_notice_key=notice.site_notice_key,
        title=notice.title,
        source_url=notice.source_url,
        organization=notice.organization,
        notice_no=notice.notice_no,
        reference_no=notice.reference_no,
        start_at=notice.start_at,
        deadline_at=notice.deadline_at,
        open_at=notice.open_at,
        period_text=notice.period_text,
        raw_payload=raw_payload,
    )


def _three_star_notice_messages(session, channel_id: str) -> list[str]:
    messages = []
    for notice, _share in list_shared_notices(session, channel_id):
        candidate = _notice_from_row(notice)
        if not is_active_notice(candidate, datetime.utcnow()):
            continue
        enrich_notice_candidate(session, candidate)
        if candidate.priority_score == 3:
            messages.append(format_notice(candidate))
    return messages


def build_slack_app(settings: Settings, session_factory, collector_registry, notifier):
    app = App(token=settings.slack_bot_token)

    def reply(channel_id: str, thread_ts: str, text: str) -> None:
        notifier.send_text(channel_id, text, thread_ts=thread_ts)

    @app.event("app_mention")
    def handle_app_mention(body, logger):  # pragma: no cover - external event loop
        event = body.get("event", {})
        channel_id = event.get("channel", "")
        user_id = event.get("user", "")
        text = event.get("text", "")
        event_ts = event.get("ts", "")

        if channel_id != settings.slack_command_channel_id:
            reply(channel_id, event_ts, "\uc774 \ucc44\ub110\uc5d0\uc11c\ub294 \uba85\ub839\uc744 \ubc1b\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")
            return

        command = parse_manual_command(text, channel_id, user_id, settings)
        if command is None:
            reply(
                channel_id,
                event_ts,
                "\uba85\ub839\uc744 \ud655\uc778\ud588\uc9c0\ub9cc \ud615\uc2dd\uc774 \uc62c\ubc14\ub974\uc9c0 \uc54a\uc544 \uc801\uc6a9\ud558\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.\n"
                "\uc608: @biz-monitor \uacf5\uace0:KIMST/\uad6d\uc81c\uc0ac\ud68cIUU \uc804\uc790\ubaa8\ub2c8\ud130\ub9c1 \uc2dc\uc2a4\ud15c \ucd94\uac00\n"
                "@biz-monitor \uacf5\uace0:KIMST/\uad6d\uc81c\uc0ac\ud68cIUU \uc804\uc790\ubaa8\ub2c8\ud130\ub9c1 \uc2dc\uc2a4\ud15c \uc0ad\uc81c\n"
                "@biz-monitor \uac80\uc0c9\uc5b4 \ubcf4\uc5ec\uc918\n"
                "@biz-monitor \uacf5\uace0\ub9ac\uc2a4\ud2b8\n"
                "@biz-monitor \uc77c\uc815\ud45c",
            )
            return

        if command.action not in {"list_keywords", "list_notices", "calendar_link", "help"} and command.site_code not in collector_registry:
            reply(
                channel_id,
                event_ts,
                "\uba85\ub839\uc744 \ud655\uc778\ud588\uc9c0\ub9cc \uc9c0\uc6d0\ud558\uc9c0 \uc54a\ub294 \uc0ac\uc774\ud2b8\uc785\ub2c8\ub2e4: %s"
                % command.site_code,
            )
            return

        with session_factory() as session:
            try:
                if command.action == "list_keywords":
                    reply(channel_id, event_ts, _format_keyword_list(list_enabled_site_keywords(session)))
                elif command.action == "list_notices":
                    messages = _three_star_notice_messages(session, channel_id)
                    if not messages:
                        reply(
                            channel_id,
                            event_ts,
                            "\uacf5\uace0\ub9ac\uc2a4\ud2b8\ub97c \uc870\ud68c\ud588\uc2b5\ub2c8\ub2e4.\n\ud604\uc7ac \uacf5\uc720\ub41c \uacf5\uace0 \uc911 \u2605\u2605\u2605 \uacf5\uace0\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.",
                        )
                    else:
                        reply(
                            channel_id,
                            event_ts,
                            "\uacf5\uace0\ub9ac\uc2a4\ud2b8\ub97c \uc870\ud68c\ud588\uc2b5\ub2c8\ub2e4.\n\ud604\uc7ac \uacf5\uc720\ub41c \uacf5\uace0 \uc911 \u2605\u2605\u2605 \uacf5\uace0 \ubaa9\ub85d\uc785\ub2c8\ub2e4.",
                        )
                        for message in messages:
                            reply(channel_id, event_ts, message)
                        for notice, _share in list_shared_notices(session, channel_id):
                            candidate = _notice_from_row(notice)
                            if not is_active_notice(candidate, datetime.utcnow()):
                                continue
                            enrich_notice_candidate(session, candidate)
                            if candidate.priority_score < 3:
                                continue
                            screenshot_path = get_notice_screenshot_path(candidate)
                            if candidate.site_code in {"d2b", "g2b"} and screenshot_path:
                                site_label = SITE_DISPLAY_NAMES.get(
                                    candidate.site_code,
                                    candidate.site_code.upper(),
                                )
                                file_id = notifier.send_file(
                                    channel_id,
                                    screenshot_path,
                                    title=f"{site_label} 상세 캡처 - {candidate.title}",
                                    initial_comment=f"{site_label} 상세 캡처",
                                    thread_ts=event_ts,
                                )
                                record_file_share(
                                    session,
                                    notice.id,
                                    channel_id,
                                    file_id,
                                    thread_ts=event_ts,
                                )
                elif command.action == "calendar_link":
                    reply(
                        channel_id,
                        event_ts,
                        "\uc77c\uc815\ud45c \ub9c1\ud06c\ub97c \uc548\ub0b4\ud588\uc2b5\ub2c8\ub2e4.\n%s"
                        % settings.calendar_web_url,
                    )
                elif command.action == "help":
                    reply(channel_id, event_ts, HELP_MESSAGE)
                elif command.action == "add":
                    created, reenabled = add_site_keyword(
                        session=session,
                        site_code=command.site_code,
                        keyword=command.search_term,
                    )
                    if created:
                        reply(
                            channel_id,
                            event_ts,
                            "\uac80\uc0c9\uc5b4\ub97c \uc801\uc6a9\ud588\uc2b5\ub2c8\ub2e4.\n%s / %s"
                            % (SITE_DISPLAY_NAMES.get(command.site_code, command.site_code.upper()), command.search_term),
                        )
                    elif reenabled:
                        reply(
                            channel_id,
                            event_ts,
                            "\ube44\ud65c\uc131\ud654\ub410\ub358 \uac80\uc0c9\uc5b4\ub97c \ub2e4\uc2dc \uc801\uc6a9\ud588\uc2b5\ub2c8\ub2e4.\n%s / %s"
                            % (SITE_DISPLAY_NAMES.get(command.site_code, command.site_code.upper()), command.search_term),
                        )
                    else:
                        reply(
                            channel_id,
                            event_ts,
                            "\uc774\ubbf8 \ub4f1\ub85d\ub41c \uac80\uc0c9\uc5b4\uc785\ub2c8\ub2e4.\n%s / %s"
                            % (SITE_DISPLAY_NAMES.get(command.site_code, command.site_code.upper()), command.search_term),
                        )
                elif command.action == "delete":
                    found, changed = disable_site_keyword(
                        session=session,
                        site_code=command.site_code,
                        keyword=command.search_term,
                    )
                    if not found:
                        reply(
                            channel_id,
                            event_ts,
                            "\uc0ad\uc81c\ud560 \uac80\uc0c9\uc5b4\ub97c \ucc3e\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.\n%s / %s"
                            % (SITE_DISPLAY_NAMES.get(command.site_code, command.site_code.upper()), command.search_term),
                        )
                    elif changed:
                        reply(
                            channel_id,
                            event_ts,
                            "\uac80\uc0c9\uc5b4 \uc0ad\uc81c\ub97c \uc801\uc6a9\ud588\uc2b5\ub2c8\ub2e4.\n%s / %s"
                            % (SITE_DISPLAY_NAMES.get(command.site_code, command.site_code.upper()), command.search_term),
                        )
                    else:
                        reply(
                            channel_id,
                            event_ts,
                            "\uc774\ubbf8 \uc0ad\uc81c\ub41c \uac80\uc0c9\uc5b4\uc785\ub2c8\ub2e4.\n%s / %s"
                            % (SITE_DISPLAY_NAMES.get(command.site_code, command.site_code.upper()), command.search_term),
                        )
                logger.info(
                    "manual command completed action=%s site=%s term=%s",
                    command.action,
                    command.site_code,
                    command.search_term,
                )
            except Exception as exc:
                logger.exception("manual command failed")
                reply(
                    channel_id,
                    event_ts,
                    "\uba85\ub839\uc744 \ud655\uc778\ud588\uc9c0\ub9cc \ucc98\ub9ac \uc911 \uc624\ub958\uac00 \ubc1c\uc0dd\ud588\uc2b5\ub2c8\ub2e4: %s"
                    % exc,
                )

    return app


def start_socket_mode(settings: Settings, session_factory, collector_registry, notifier):
    if not settings.slack_app_token:
        raise ValueError("SLACK_APP_TOKEN is required for Socket Mode.")
    app = build_slack_app(settings, session_factory, collector_registry, notifier)
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()
