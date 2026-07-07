from __future__ import annotations

import re
from typing import Optional

from app.config import Settings
from app.types import ManualCommand


MENTION_PATTERN = re.compile(r"^<@[^>]+>\s*")
SITE_ALIASES = {
    "kimst": "kimst",
    "nia": "nia",
    "g2b": "g2b",
    "\ub098\ub77c\uc7a5\ud130": "g2b",
    "\uc870\ub2ec\uccad": "g2b",
    "d2b": "d2b",
    "\uad6d\ubc29\uc804\uc790\uc870\ub2ec": "d2b",
    "\uad6d\ubc29\uc804\uc790\uc870\ub2ec\uc2dc\uc2a4\ud15c": "d2b",
    "kmiti": "kmiti",
    "\uae30\uc0c1\uc0b0\uc5c5\uae30\uc220\uc6d0": "kmiti",
    "\ud55c\uad6d\uae30\uc0c1\uc0b0\uc5c5\uae30\uc220\uc6d0": "kmiti",
    "\uae30\uc0c1\uc0b0\uc5c5\uc9c4\ud765\uc6d0": "kmiti",
    "iris": "iris",
    "\uc544\uc774\ub9ac\uc2a4": "iris",
    "\ubc94\ubd80\ucc98\ud1b5\ud569\uc5f0\uad6c\uc9c0\uc6d0\uc2dc\uc2a4\ud15c": "iris",
}
KEYWORD_LIST_COMMANDS = {
    "\uac80\uc0c9\uc5b4 \ubcf4\uc5ec\uc918",
    "\uac80\uc11d\uc5b4 \ubcf4\uc5ec\uc918",
}
NOTICE_LIST_COMMANDS = {
    "\uacf5\uace0\ub9ac\uc2a4\ud2b8",
}
CALENDAR_LINK_COMMANDS = {
    "\uc77c\uc815\ud45c",
}


def strip_bot_mention(text: str) -> str:
    return MENTION_PATTERN.sub("", text or "").strip()


def parse_manual_command(
    text: str, channel_id: str, requested_by: str, settings: Settings
) -> Optional[ManualCommand]:
    cleaned = strip_bot_mention(text)
    if cleaned in KEYWORD_LIST_COMMANDS:
        return ManualCommand(
            action="list_keywords",
            channel_id=channel_id,
            requested_by=requested_by,
        )
    if cleaned in NOTICE_LIST_COMMANDS:
        return ManualCommand(
            action="list_notices",
            channel_id=channel_id,
            requested_by=requested_by,
        )
    if cleaned in CALENDAR_LINK_COMMANDS:
        return ManualCommand(
            action="calendar_link",
            channel_id=channel_id,
            requested_by=requested_by,
        )

    pattern = r"^%s(?P<site>[^/]+)/(?P<term>.+?)\s+(?P<action>\S+)$" % (
        re.escape(settings.slack_command_prefix),
    )
    match = re.match(pattern, cleaned)
    if not match:
        return None

    action_text = match.group("action").strip()
    if action_text not in {settings.slack_command_suffix, "\uc0ad\uc81c"}:
        return None

    raw_site = match.group("site").strip()
    site_code = SITE_ALIASES.get(raw_site.lower(), SITE_ALIASES.get(raw_site, raw_site.lower()))
    return ManualCommand(
        action="add" if action_text == settings.slack_command_suffix else "delete",
        site_code=site_code,
        search_term=match.group("term").strip(),
        channel_id=channel_id,
        requested_by=requested_by,
    )
