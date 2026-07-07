from __future__ import annotations

import os
from contextlib import contextmanager

from playwright.sync_api import sync_playwright


def _headless() -> bool:
    value = os.getenv("PLAYWRIGHT_HEADLESS", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _timeout_ms(name: str, default: int) -> int:
    raw_value = (os.getenv(name, str(default)) or "").strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@contextmanager
def browser_page(accept_downloads: bool = False):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=_headless())
        page = browser.new_page(accept_downloads=accept_downloads)
        page.set_default_timeout(_timeout_ms("PLAYWRIGHT_DEFAULT_TIMEOUT_MS", 45000))
        page.set_default_navigation_timeout(
            _timeout_ms("PLAYWRIGHT_NAVIGATION_TIMEOUT_MS", 60000)
        )
        try:
            yield page
        finally:
            browser.close()
