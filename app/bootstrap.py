from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .config import Settings
from .db import Base
from .models import Site, SiteKeyword


SITE_NAMES = {
    "g2b": "\ub098\ub77c\uc7a5\ud130",
    "kimst": "KIMST",
    "nia": "NIA",
    "d2b": "D2B",
    "kmiti": "\uae30\uc0c1\uc0b0\uc5c5\uae30\uc220\uc6d0",
    "iris": "IRIS",
    "manual": "\uc9c1\uc811\ub4f1\ub85d",
    "imported": "\uacfc\uac70\uc774\uad00",
}


def ensure_directories(settings: Settings) -> None:
    for path in [
        settings.base_dir,
        settings.log_dir,
        settings.download_dir,
        settings.temp_dir,
    ]:
        os.makedirs(path, exist_ok=True)


def create_schema(engine) -> None:
    try:
        Base.metadata.create_all(engine)
    except OperationalError as exc:
        if "already exists" not in str(exc).lower():
            raise
    _upgrade_schema(engine)


def _upgrade_schema(engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as connection:
        if inspector.has_table("slack_shares"):
            slack_share_columns = {
                column["name"] for column in inspector.get_columns("slack_shares")
            }
            if "suppressed_at" not in slack_share_columns:
                connection.execute(
                    text("ALTER TABLE slack_shares ADD COLUMN suppressed_at DATETIME")
                )
            if "suppressed_reason" not in slack_share_columns:
                connection.execute(
                    text("ALTER TABLE slack_shares ADD COLUMN suppressed_reason VARCHAR(255)")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_slack_shares_publish_state "
                    "ON slack_shares(channel_id, suppressed_at, shared_at)"
                )
            )

            # Seed the durable re-post guard from the share rows that still
            # exist. Best effort: retention has already removed the rows for
            # notices older than the retention window, so those keys can only be
            # recovered from a backup. Re-running is safe.
            if inspector.has_table("notice_share_guards"):
                connection.execute(
                    text(
                        """
                        INSERT OR IGNORE INTO notice_share_guards
                            (site_code, site_notice_key, channel_id,
                             first_shared_at, last_message_ts,
                             suppressed_at, suppressed_reason, updated_at)
                        SELECT n.site_code,
                               n.site_notice_key,
                               s.channel_id,
                               min(s.shared_at),
                               max(s.message_ts),
                               max(s.suppressed_at),
                               max(s.suppressed_reason),
                               CURRENT_TIMESTAMP
                        FROM slack_shares s
                        JOIN notices n ON n.id = s.notice_id
                        GROUP BY n.site_code, n.site_notice_key, s.channel_id
                        """
                    )
                )

        if not inspector.has_table("notice_attachments"):
            connection.execute(
                text(
                    """
                    CREATE TABLE notice_attachments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        notice_id INTEGER NOT NULL,
                        site_code VARCHAR(50) NOT NULL,
                        attachment_name VARCHAR(500) NOT NULL,
                        attachment_category VARCHAR(100) NOT NULL,
                        priority_rank INTEGER NOT NULL,
                        stored_path VARCHAR(2000) NOT NULL,
                        source_url VARCHAR(2000),
                        mime_type VARCHAR(255),
                        file_size INTEGER,
                        is_summary_source BOOLEAN NOT NULL DEFAULT 0,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        CONSTRAINT uq_notice_attachment_name UNIQUE (notice_id, attachment_name),
                        FOREIGN KEY(notice_id) REFERENCES notices (id) ON DELETE CASCADE
                    )
                    """
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_notice_attachments_notice_id "
                "ON notice_attachments(notice_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_notice_attachments_site_priority "
                "ON notice_attachments(site_code, priority_rank)"
            )
        )
        notice_attachment_columns = {
            column["name"] for column in inspector.get_columns("notice_attachments")
        }
        if "is_summary_source" not in notice_attachment_columns:
            connection.execute(
                text(
                    "ALTER TABLE notice_attachments "
                    "ADD COLUMN is_summary_source BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_notice_attachments_summary_source "
                "ON notice_attachments(notice_id, is_summary_source)"
            )
        )

        if not inspector.has_table("notice_summaries"):
            connection.execute(
                text(
                    """
                    CREATE TABLE notice_summaries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        notice_id INTEGER NOT NULL,
                        attachment_id INTEGER,
                        source_type VARCHAR(50) NOT NULL,
                        summary_status VARCHAR(50) NOT NULL DEFAULT 'pending',
                        failure_reason TEXT,
                        purpose TEXT,
                        core_tasks TEXT,
                        required_performance TEXT,
                        quantitative_targets TEXT,
                        period_text TEXT,
                        raw_extracted_text_path VARCHAR(2000),
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        CONSTRAINT uq_notice_summary_notice_id UNIQUE (notice_id),
                        FOREIGN KEY(notice_id) REFERENCES notices (id) ON DELETE CASCADE,
                        FOREIGN KEY(attachment_id) REFERENCES notice_attachments (id) ON DELETE SET NULL
                    )
                    """
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_notice_summaries_status "
                "ON notice_summaries(summary_status)"
            )
        )

        if not inspector.has_table("notice_ai_evaluations"):
            connection.execute(
                text(
                    """
                    CREATE TABLE notice_ai_evaluations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        notice_id INTEGER NOT NULL,
                        provider VARCHAR(50) NOT NULL,
                        model VARCHAR(100) NOT NULL,
                        prompt_version VARCHAR(50) NOT NULL,
                        input_hash VARCHAR(128) NOT NULL,
                        status VARCHAR(50) NOT NULL DEFAULT 'pending',
                        fit_score INTEGER,
                        fit_level VARCHAR(30),
                        confidence VARCHAR(30),
                        recommended_action VARCHAR(50),
                        reason TEXT,
                        summary_for_slack TEXT,
                        matched_capabilities_json TEXT,
                        risks_json TEXT,
                        raw_response_json TEXT,
                        failure_reason TEXT,
                        ai_recommendation_posted_at DATETIME,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        CONSTRAINT uq_notice_ai_eval_input UNIQUE (
                            notice_id,
                            prompt_version,
                            input_hash
                        ),
                        FOREIGN KEY(notice_id) REFERENCES notices (id) ON DELETE CASCADE
                    )
                    """
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_notice_ai_evaluations_notice "
                "ON notice_ai_evaluations(notice_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_notice_ai_evaluations_status "
                "ON notice_ai_evaluations(status)"
            )
        )
        notice_ai_columns = {
            column["name"] for column in inspector.get_columns("notice_ai_evaluations")
        }
        if "ai_recommendation_posted_at" not in notice_ai_columns:
            connection.execute(
                text(
                    "ALTER TABLE notice_ai_evaluations "
                    "ADD COLUMN ai_recommendation_posted_at DATETIME"
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_notice_ai_evaluations_recommendation_posted "
                "ON notice_ai_evaluations(ai_recommendation_posted_at)"
            )
        )

    if not inspector.has_table("calendar_saved_notices"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("calendar_saved_notices")}
    add_columns = {
        "origin_type": "ALTER TABLE calendar_saved_notices ADD COLUMN origin_type VARCHAR(50) NOT NULL DEFAULT 'notice'",
        "deadline_confidence": "ALTER TABLE calendar_saved_notices ADD COLUMN deadline_confidence VARCHAR(50) NOT NULL DEFAULT 'exact'",
        "legacy_year": "ALTER TABLE calendar_saved_notices ADD COLUMN legacy_year INTEGER",
        "import_batch_id": "ALTER TABLE calendar_saved_notices ADD COLUMN import_batch_id VARCHAR(255)",
    }

    with engine.begin() as connection:
        for column_name, ddl in add_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(ddl))

        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_calendar_saved_active_deadline "
                "ON calendar_saved_notices(is_active, primary_deadline_at)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_calendar_saved_origin_type "
                "ON calendar_saved_notices(origin_type)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_calendar_saved_import_batch "
                "ON calendar_saved_notices(import_batch_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_calendar_saved_updated "
                "ON calendar_saved_notices(updated_at)"
            )
        )

        # Worker ownership + heartbeat for safe stale-job recovery.
        job_columns = {column["name"] for column in inspector.get_columns("jobs")}
        if "worker_id" not in job_columns:
            connection.execute(
                text("ALTER TABLE jobs ADD COLUMN worker_id VARCHAR(255)")
            )
        if "heartbeat_at" not in job_columns:
            connection.execute(
                text("ALTER TABLE jobs ADD COLUMN heartbeat_at DATETIME")
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_heartbeat "
                "ON jobs(status, heartbeat_at)"
            )
        )


def seed_sites_and_keywords(session: Session, settings: Settings) -> None:
    now = datetime.utcnow()
    for code, name in SITE_NAMES.items():
        site = session.query(Site).filter(Site.code == code).one_or_none()
        if site is None:
            try:
                with session.begin_nested():
                    site = Site(
                        code=code,
                        name=name,
                        enabled=settings.site_enabled.get(code, False),
                        created_at=now,
                    )
                    session.add(site)
                    session.flush()
            except IntegrityError:
                site = session.query(Site).filter(Site.code == code).one()
        else:
            site.enabled = settings.site_enabled.get(code, False)
            site.name = name

        existing_keywords = {
            row.keyword: row
            for row in session.query(SiteKeyword).filter(SiteKeyword.site_id == site.id)
        }
        for keyword in settings.site_keywords.get(code, []):
            if keyword not in existing_keywords:
                session.add(
                    SiteKeyword(
                        site_id=site.id,
                        keyword=keyword,
                        enabled=True,
                        created_at=now,
                    )
                )
    session.commit()
