from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False)

    keywords = relationship("SiteKeyword", back_populates="site")


class SiteKeyword(Base):
    __tablename__ = "site_keywords"
    __table_args__ = (UniqueConstraint("site_id", "keyword", name="uq_site_keyword"),)

    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey("sites.id", ondelete="CASCADE"), nullable=False)
    keyword = Column(String(255), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False)

    site = relationship("Site", back_populates="keywords")


class CompanyProject(Base):
    __tablename__ = "company_projects"

    id = Column(Integer, primary_key=True)
    project_name = Column(String(500), nullable=False)
    organization = Column(String(255), nullable=True)
    category = Column(String(255), nullable=True)
    keywords_json = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    job_type = Column(String(50), nullable=False)
    site_code = Column(String(50), nullable=False)
    keyword = Column(String(255), nullable=True)
    search_term = Column(String(500), nullable=True)
    channel_id = Column(String(255), nullable=True)
    requested_by = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False)
    attempt = Column(Integer, nullable=False, default=0)
    run_after = Column(DateTime, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False)
    # Worker that currently owns a running job, and its last liveness heartbeat.
    # Stale recovery uses heartbeat_at (not started_at) so a long-but-alive job
    # is never requeued, while a job from a crashed worker still gets reclaimed.
    worker_id = Column(String(255), nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)


class Notice(Base):
    __tablename__ = "notices"
    __table_args__ = (
        UniqueConstraint("site_code", "site_notice_key", name="uq_site_notice_key"),
    )

    id = Column(Integer, primary_key=True)
    site_code = Column(String(50), nullable=False)
    site_notice_key = Column(String(255), nullable=False)
    title = Column(String(1000), nullable=False)
    organization = Column(String(255), nullable=True)
    notice_no = Column(String(255), nullable=True)
    reference_no = Column(String(255), nullable=True)
    start_at = Column(DateTime, nullable=True)
    deadline_at = Column(DateTime, nullable=True)
    open_at = Column(DateTime, nullable=True)
    period_text = Column(String(255), nullable=True)
    source_url = Column(String(2000), nullable=False)
    raw_payload_json = Column(Text, nullable=True)
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class CalendarSavedNotice(Base):
    __tablename__ = "calendar_saved_notices"

    id = Column(Integer, primary_key=True)
    source_notice_id = Column(
        Integer,
        ForeignKey("notices.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    site_id = Column(Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    site_code = Column(String(50), nullable=False)
    site_name = Column(String(255), nullable=False)
    title = Column(String(1000), nullable=False)
    organization = Column(String(255), nullable=True)
    primary_deadline_at = Column(DateTime, nullable=True)
    amount_text = Column(String(255), nullable=True)
    amount_value = Column(Integer, nullable=True)
    priority_score = Column(Integer, nullable=False, default=0)
    notice_tag = Column(String(100), nullable=True)
    source_url = Column(String(2000), nullable=False)
    raw_payload_json = Column(Text, nullable=False, default="{}")
    status = Column(String(50), nullable=False, default="participating")
    owner_name = Column(String(255), nullable=True)
    selected_at = Column(DateTime, nullable=False)
    deselected_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False)
    selected_by = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    memo = Column(Text, nullable=True)
    origin_type = Column(String(50), nullable=False, default="notice")
    deadline_confidence = Column(String(50), nullable=False, default="exact")
    legacy_year = Column(Integer, nullable=True)
    import_batch_id = Column(String(255), nullable=True)


class SlackShare(Base):
    __tablename__ = "slack_shares"
    __table_args__ = (
        UniqueConstraint("notice_id", "channel_id", name="uq_notice_channel_share"),
    )

    id = Column(Integer, primary_key=True)
    notice_id = Column(Integer, ForeignKey("notices.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(String(255), nullable=False)
    message_ts = Column(String(255), nullable=True)
    share_type = Column(String(50), nullable=False)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    shared_at = Column(DateTime, nullable=False)
    # Retain ineligible queued shares for audit/deduplication without publishing.
    suppressed_at = Column(DateTime, nullable=True)
    suppressed_reason = Column(String(255), nullable=True)


class NoticeShareGuard(Base):
    """Durable re-post guard keyed on the source notice identity.

    ``slack_shares`` rows are deleted together with their notice by retention
    cleanup, so they cannot answer "was this ever shared?" for a notice that
    aged out and then reappeared in a later collection. This table is keyed on
    ``(site_code, site_notice_key)`` instead of ``notice_id`` and is never
    subject to retention, so the answer survives the notice row.
    """

    __tablename__ = "notice_share_guards"
    __table_args__ = (
        UniqueConstraint(
            "site_code",
            "site_notice_key",
            "channel_id",
            name="uq_notice_share_guard",
        ),
    )

    id = Column(Integer, primary_key=True)
    site_code = Column(String(50), nullable=False)
    site_notice_key = Column(String(255), nullable=False)
    channel_id = Column(String(255), nullable=False)
    first_shared_at = Column(DateTime, nullable=False)
    last_message_ts = Column(String(255), nullable=True)
    suppressed_at = Column(DateTime, nullable=True)
    suppressed_reason = Column(String(255), nullable=True)
    updated_at = Column(DateTime, nullable=False)


class SlackFileShare(Base):
    __tablename__ = "slack_file_shares"
    __table_args__ = (
        UniqueConstraint("file_id", name="uq_slack_file_id"),
    )

    id = Column(Integer, primary_key=True)
    notice_id = Column(Integer, ForeignKey("notices.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(String(255), nullable=False)
    file_id = Column(String(255), nullable=False)
    thread_ts = Column(String(255), nullable=True)
    shared_at = Column(DateTime, nullable=False)


class NoticeAttachment(Base):
    __tablename__ = "notice_attachments"
    __table_args__ = (
        UniqueConstraint("notice_id", "attachment_name", name="uq_notice_attachment_name"),
    )

    id = Column(Integer, primary_key=True)
    notice_id = Column(Integer, ForeignKey("notices.id", ondelete="CASCADE"), nullable=False)
    site_code = Column(String(50), nullable=False)
    attachment_name = Column(String(500), nullable=False)
    attachment_category = Column(String(100), nullable=False)
    priority_rank = Column(Integer, nullable=False)
    stored_path = Column(String(2000), nullable=False)
    source_url = Column(String(2000), nullable=True)
    mime_type = Column(String(255), nullable=True)
    file_size = Column(Integer, nullable=True)
    is_summary_source = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class NoticeSummary(Base):
    __tablename__ = "notice_summaries"
    __table_args__ = (
        UniqueConstraint("notice_id", name="uq_notice_summary_notice_id"),
    )

    id = Column(Integer, primary_key=True)
    notice_id = Column(Integer, ForeignKey("notices.id", ondelete="CASCADE"), nullable=False)
    attachment_id = Column(
        Integer,
        ForeignKey("notice_attachments.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_type = Column(String(50), nullable=False)
    summary_status = Column(String(50), nullable=False, default="pending")
    failure_reason = Column(Text, nullable=True)
    purpose = Column(Text, nullable=True)
    core_tasks = Column(Text, nullable=True)
    required_performance = Column(Text, nullable=True)
    quantitative_targets = Column(Text, nullable=True)
    period_text = Column(Text, nullable=True)
    raw_extracted_text_path = Column(String(2000), nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class NoticeAiEvaluation(Base):
    __tablename__ = "notice_ai_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "notice_id",
            "prompt_version",
            "input_hash",
            name="uq_notice_ai_eval_input",
        ),
    )

    id = Column(Integer, primary_key=True)
    notice_id = Column(Integer, ForeignKey("notices.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    prompt_version = Column(String(50), nullable=False)
    input_hash = Column(String(128), nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    fit_score = Column(Integer, nullable=True)
    fit_level = Column(String(30), nullable=True)
    confidence = Column(String(30), nullable=True)
    recommended_action = Column(String(50), nullable=True)
    reason = Column(Text, nullable=True)
    summary_for_slack = Column(Text, nullable=True)
    matched_capabilities_json = Column(Text, nullable=True)
    risks_json = Column(Text, nullable=True)
    raw_response_json = Column(Text, nullable=True)
    failure_reason = Column(Text, nullable=True)
    ai_recommendation_posted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
