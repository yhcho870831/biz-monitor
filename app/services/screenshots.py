from __future__ import annotations

import os
import re
from pathlib import Path

from app.collectors.playwright_utils import browser_page
from app.types import NoticeCandidate
from app.utils import normalize_text


def _env_flag(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_timeout_ms(name: str, default: int) -> int:
    raw_value = (os.getenv(name, str(default)) or "").strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def ensure_notice_screenshot(candidate: NoticeCandidate) -> str:
    existing_path = str((candidate.raw_payload or {}).get("screenshot_path", "")).strip()
    if existing_path:
        return existing_path

    if (candidate.raw_payload or {}).get("announcement_stage") in {
        "pre_announcement",
        "procurement_plan",
        "pre_specification",
    }:
        return ""

    if candidate.site_code != "g2b" or not candidate.source_url:
        return ""

    if not _env_flag("G2B_SCREENSHOT_ENABLED", True):
        return ""

    base_dir = Path(os.getenv("TEMP_DIR", os.path.join(os.getcwd(), "output", "tmp")))
    screenshot_dir = base_dir / "g2b_screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    raw_name = "-".join(
        part
        for part in [
            normalize_text(candidate.notice_no or ""),
            normalize_text(candidate.title or ""),
        ]
        if part
    ) or "g2b-notice"
    safe_name = re.sub(r"[^0-9A-Za-z._-]+", "_", raw_name)[:120].strip("._-") or "g2b-notice"
    path = screenshot_dir / f"{safe_name}.png"

    with browser_page() as page:
        page.goto(
            candidate.source_url,
            wait_until="domcontentloaded",
            timeout=_env_timeout_ms("G2B_SCREENSHOT_GOTO_TIMEOUT_MS", 12000),
        )
        try:
            page.wait_for_load_state(
                "networkidle",
                timeout=_env_timeout_ms("G2B_SCREENSHOT_READY_TIMEOUT_MS", 3000),
            )
        except Exception:
            pass
        page.wait_for_timeout(_env_timeout_ms("G2B_SCREENSHOT_EXTRA_WAIT_MS", 500))
        page.screenshot(
            path=str(path),
            full_page=True,
            timeout=_env_timeout_ms("G2B_SCREENSHOT_CAPTURE_TIMEOUT_MS", 8000),
        )

    candidate.raw_payload["screenshot_path"] = str(path)
    return str(path)
