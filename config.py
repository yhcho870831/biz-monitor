from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv


def _parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(value: str) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    app_env: str
    app_timezone: str
    log_level: str
    base_dir: str
    log_dir: str
    download_dir: str
    temp_dir: str
    database_url: str
    scheduler_enabled: bool
    schedule_times: List[str]
    retry_delay_minutes: int
    max_retry_count: int
    slack_use_socket_mode: bool
    slack_bot_token: str
    slack_app_token: str
    slack_signing_secret: str
    slack_briefing_channel_id: str
    slack_command_channel_id: str
    calendar_web_url: str
    slack_command_prefix: str
    slack_command_suffix: str
    slack_send_retry_count: int
    slack_resend_on_next_run_if_send_failed: bool
    enable_manual_commands: bool
    enable_relevance_filter: bool
    enable_deadline_filter_d2b: bool
    site_enabled: Dict[str, bool]
    site_keywords: Dict[str, List[str]]
    company_global_keywords: List[str]
    company_global_organizations: List[str]
    d2b_include_rebid: bool
    d2b_include_correction: bool
    d2b_exclude_canceled: bool
    d2b_exclude_delayed: bool
    runner_poll_interval_seconds: int
    playwright_headless: bool


def load_settings() -> Settings:
    load_dotenv()
    override_path = Path.cwd() / ".env.override"
    if override_path.exists():
        load_dotenv(override_path, override=True)

    site_enabled = {
        "g2b": _parse_bool(os.getenv("SITE_G2B_ENABLED"), True),
        "kimst": _parse_bool(os.getenv("SITE_KIMST_ENABLED"), True),
        "nia": _parse_bool(os.getenv("SITE_NIA_ENABLED"), True),
        "d2b": _parse_bool(os.getenv("SITE_D2B_ENABLED"), True),
        "kmiti": _parse_bool(os.getenv("SITE_KMITI_ENABLED"), True),
        "iris": _parse_bool(os.getenv("SITE_IRIS_ENABLED"), True),
    }
    site_keywords = {
        "g2b": _parse_csv(os.getenv("G2B_KEYWORDS", "")),
        "kimst": _parse_csv(os.getenv("KIMST_KEYWORDS", "")),
        "nia": _parse_csv(os.getenv("NIA_KEYWORDS", "")),
        "d2b": _parse_csv(os.getenv("D2B_KEYWORDS", "")),
        "kmiti": _parse_csv(os.getenv("KMITI_KEYWORDS", "")),
        "iris": _parse_csv(os.getenv("IRIS_KEYWORDS", "")),
    }

    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        app_timezone=os.getenv("APP_TIMEZONE", "Asia/Seoul"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        base_dir=os.getenv("BASE_DIR", os.getcwd()),
        log_dir=os.getenv("LOG_DIR", os.path.join(os.getcwd(), "output", "logs")),
        download_dir=os.getenv(
            "DOWNLOAD_DIR", os.path.join(os.getcwd(), "output", "downloads")
        ),
        temp_dir=os.getenv("TEMP_DIR", os.path.join(os.getcwd(), "output", "tmp")),
        database_url=os.getenv(
            "DATABASE_URL", "sqlite:///" + os.path.join(os.getcwd(), "data", "app.db")
        ),
        scheduler_enabled=_parse_bool(os.getenv("SCHEDULER_ENABLED"), True),
        schedule_times=[
            os.getenv("SCHEDULE_TIME_1", "09:00"),
            os.getenv("SCHEDULE_TIME_2", "15:00"),
        ],
        retry_delay_minutes=int(os.getenv("RETRY_DELAY_MINUTES", "10")),
        max_retry_count=int(os.getenv("MAX_RETRY_COUNT", "1")),
        slack_use_socket_mode=_parse_bool(os.getenv("SLACK_USE_SOCKET_MODE"), True),
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
        slack_app_token=os.getenv("SLACK_APP_TOKEN", ""),
        slack_signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
        slack_briefing_channel_id=os.getenv("SLACK_BRIEFING_CHANNEL_ID", ""),
        slack_command_channel_id=os.getenv("SLACK_COMMAND_CHANNEL_ID", ""),
        calendar_web_url=os.getenv("CALENDAR_WEB_URL", "http://127.0.0.1:8080/calendar"),
        slack_command_prefix=os.getenv("SLACK_COMMAND_PREFIX", "\uacf5\uace0:"),
        slack_command_suffix=os.getenv("SLACK_COMMAND_SUFFIX", "\ucd94\uac00"),
        slack_send_retry_count=int(os.getenv("SLACK_SEND_RETRY_COUNT", "1")),
        slack_resend_on_next_run_if_send_failed=_parse_bool(
            os.getenv("SLACK_RESEND_ON_NEXT_RUN_IF_SEND_FAILED"), True
        ),
        enable_manual_commands=_parse_bool(os.getenv("ENABLE_MANUAL_COMMANDS"), True),
        enable_relevance_filter=_parse_bool(os.getenv("ENABLE_RELEVANCE_FILTER"), True),
        enable_deadline_filter_d2b=_parse_bool(
            os.getenv("ENABLE_DEADLINE_FILTER_D2B"), True
        ),
        site_enabled=site_enabled,
        site_keywords=site_keywords,
        company_global_keywords=_parse_csv(os.getenv("COMPANY_GLOBAL_KEYWORDS", "")),
        company_global_organizations=_parse_csv(
            os.getenv("COMPANY_GLOBAL_ORGANIZATIONS", "")
        ),
        d2b_include_rebid=_parse_bool(os.getenv("D2B_INCLUDE_REBID"), True),
        d2b_include_correction=_parse_bool(os.getenv("D2B_INCLUDE_CORRECTION"), True),
        d2b_exclude_canceled=_parse_bool(os.getenv("D2B_EXCLUDE_CANCELED"), True),
        d2b_exclude_delayed=_parse_bool(os.getenv("D2B_EXCLUDE_DELAYED"), True),
        runner_poll_interval_seconds=int(
            os.getenv("RUNNER_POLL_INTERVAL_SECONDS", "10")
        ),
        playwright_headless=_parse_bool(os.getenv("PLAYWRIGHT_HEADLESS"), True),
    )
