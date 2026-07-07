from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from .config import Settings


def _safe_service_name() -> str:
    value = os.getenv("SERVICE_NAME", "app").strip() or "app"
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def configure_logging(settings: Settings) -> None:
    os.makedirs(settings.log_dir, exist_ok=True)
    service_name = _safe_service_name()
    log_path = os.path.join(settings.log_dir, f"{service_name}.log")
    backup_days = int(os.getenv("LOG_BACKUP_DAYS", "14") or "14")

    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(process)d %(name)s - %(message)s"
    )

    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=max(1, backup_days),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
