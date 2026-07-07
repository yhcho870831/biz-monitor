from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

from app.services.business_scope_filter import DEFAULT_EXCLUDED_SCOPE_KEYWORDS


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
    scheduler_skip_weekends: bool
    scheduler_skip_public_holidays: bool
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
    slack_site_send_interval_seconds: float
    slack_max_notices_per_site: int
    slack_max_notices_g2b: int
    slack_g2b_early_flush_count: int
    slack_max_zero_star_per_site: int
    slack_resend_on_next_run_if_send_failed: bool
    slack_deferred_publish_enabled: bool
    slack_publish_times: List[str]
    slack_deferred_publish_since: str
    enable_manual_commands: bool
    enable_relevance_filter: bool
    ai_relevance_enabled: bool
    ai_relevance_gateway_url: str
    ai_relevance_gateway_token: str
    ai_relevance_agent: str
    ai_relevance_prompt_version: str
    ai_relevance_timeout_seconds: int
    ai_relevance_max_per_run: int
    ai_relevance_share_threshold: int
    ai_relevance_min_rule_score: int
    ai_relevance_evaluate_rule_failed: bool
    ai_relevance_prepare_minutes_before_publish: int
    enable_deadline_filter_d2b: bool
    site_enabled: Dict[str, bool]
    site_keywords: Dict[str, List[str]]
    company_global_keywords: List[str]
    company_global_organizations: List[str]
    excluded_scope_keywords: List[str]
    d2b_include_rebid: bool
    d2b_include_correction: bool
    d2b_exclude_canceled: bool
    d2b_exclude_delayed: bool
    job_running_stale_minutes: int
    worker_heartbeat_interval_seconds: int
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
        scheduler_skip_weekends=_parse_bool(
            os.getenv("SCHEDULER_SKIP_WEEKENDS"), True
        ),
        scheduler_skip_public_holidays=_parse_bool(
            os.getenv("SCHEDULER_SKIP_PUBLIC_HOLIDAYS"), True
        ),
        schedule_times=[
            os.getenv("SCHEDULE_TIME_1", "08:30"),
            os.getenv("SCHEDULE_TIME_2", "15:30"),
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
        slack_site_send_interval_seconds=float(
            os.getenv("SLACK_SITE_SEND_INTERVAL_SECONDS", "1.2")
        ),
        slack_max_notices_per_site=int(os.getenv("SLACK_MAX_NOTICES_PER_SITE", "40")),
        slack_max_notices_g2b=int(os.getenv("SLACK_MAX_NOTICES_G2B", "30")),
        slack_g2b_early_flush_count=int(
            os.getenv("SLACK_G2B_EARLY_FLUSH_COUNT", "8")
        ),
        slack_max_zero_star_per_site=int(
            os.getenv("SLACK_MAX_ZERO_STAR_PER_SITE", "10")
        ),
        slack_resend_on_next_run_if_send_failed=_parse_bool(
            os.getenv("SLACK_RESEND_ON_NEXT_RUN_IF_SEND_FAILED"), True
        ),
        slack_deferred_publish_enabled=_parse_bool(
            os.getenv("SLACK_DEFERRED_PUBLISH_ENABLED"), False
        ),
        slack_publish_times=[
            os.getenv("SLACK_PUBLISH_TIME_1", "09:00"),
            os.getenv("SLACK_PUBLISH_TIME_2", "16:00"),
        ],
        slack_deferred_publish_since=os.getenv("SLACK_DEFERRED_PUBLISH_SINCE", "").strip(),
        enable_manual_commands=_parse_bool(os.getenv("ENABLE_MANUAL_COMMANDS"), True),
        enable_relevance_filter=_parse_bool(os.getenv("ENABLE_RELEVANCE_FILTER"), True),
        ai_relevance_enabled=_parse_bool(os.getenv("AI_RELEVANCE_ENABLED"), False),
        ai_relevance_gateway_url=os.getenv("AI_RELEVANCE_GATEWAY_URL", "").strip(),
        ai_relevance_gateway_token=os.getenv("AI_RELEVANCE_GATEWAY_TOKEN", "").strip(),
        ai_relevance_agent=os.getenv("AI_RELEVANCE_AGENT", "cron-google").strip(),
        ai_relevance_prompt_version=os.getenv("AI_RELEVANCE_PROMPT_VERSION", "v1").strip(),
        ai_relevance_timeout_seconds=int(os.getenv("AI_RELEVANCE_TIMEOUT_SECONDS", "300")),
        ai_relevance_max_per_run=int(os.getenv("AI_RELEVANCE_MAX_PER_RUN", "30")),
        ai_relevance_share_threshold=int(os.getenv("AI_RELEVANCE_SHARE_THRESHOLD", "70")),
        ai_relevance_min_rule_score=int(os.getenv("AI_RELEVANCE_MIN_RULE_SCORE", "1")),
        ai_relevance_evaluate_rule_failed=_parse_bool(
            os.getenv("AI_RELEVANCE_EVALUATE_RULE_FAILED"), True
        ),
        ai_relevance_prepare_minutes_before_publish=int(
            os.getenv("AI_RELEVANCE_PREPARE_MINUTES_BEFORE_PUBLISH", "10")
        ),
        enable_deadline_filter_d2b=_parse_bool(
            os.getenv("ENABLE_DEADLINE_FILTER_D2B"), True
        ),
        site_enabled=site_enabled,
        site_keywords=site_keywords,
        company_global_keywords=_parse_csv(os.getenv("COMPANY_GLOBAL_KEYWORDS", "")),
        company_global_organizations=_parse_csv(
            os.getenv("COMPANY_GLOBAL_ORGANIZATIONS", "")
        ),
        excluded_scope_keywords=_parse_csv(
            os.getenv(
                "EXCLUDED_SCOPE_KEYWORDS",
                ",".join(DEFAULT_EXCLUDED_SCOPE_KEYWORDS),
            )
        ),
        d2b_include_rebid=_parse_bool(os.getenv("D2B_INCLUDE_REBID"), True),
        d2b_include_correction=_parse_bool(os.getenv("D2B_INCLUDE_CORRECTION"), True),
        d2b_exclude_canceled=_parse_bool(os.getenv("D2B_EXCLUDE_CANCELED"), True),
        d2b_exclude_delayed=_parse_bool(os.getenv("D2B_EXCLUDE_DELAYED"), True),
        job_running_stale_minutes=int(
            os.getenv("JOB_RUNNING_STALE_MINUTES", "30")
        ),
        worker_heartbeat_interval_seconds=int(
            os.getenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "30")
        ),
        runner_poll_interval_seconds=int(
            os.getenv("RUNNER_POLL_INTERVAL_SECONDS", "10")
        ),
        playwright_headless=_parse_bool(os.getenv("PLAYWRIGHT_HEADLESS"), True),
    )
