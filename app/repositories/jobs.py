from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Job


def create_job(
    session: Session,
    job_type: str,
    site_code: str,
    status: str,
    run_after: datetime,
    keyword: str = None,
    search_term: str = None,
    channel_id: str = None,
    requested_by: str = None,
    attempt: int = 0,
) -> Job:
    job = Job(
        job_type=job_type,
        site_code=site_code,
        keyword=keyword,
        search_term=search_term,
        channel_id=channel_id,
        requested_by=requested_by,
        status=status,
        attempt=attempt,
        run_after=run_after,
        created_at=datetime.utcnow(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _normalize_site_codes(site_codes: Iterable[str] | None) -> tuple[str, ...]:
    if not site_codes:
        return ()
    return tuple(sorted({str(site_code).strip().lower() for site_code in site_codes if str(site_code).strip()}))


def claim_next_pending_job(
    session: Session,
    now: datetime,
    site_codes: Iterable[str] | None = None,
    worker_id: str | None = None,
) -> Optional[Job]:
    normalized_site_codes = _normalize_site_codes(site_codes)
    while True:
        stmt = (
            select(Job.id)
            .where(Job.status == "pending")
            .where(Job.run_after <= now)
        )
        if normalized_site_codes:
            stmt = stmt.where(Job.site_code.in_(normalized_site_codes))
        stmt = stmt.order_by(Job.run_after.asc(), Job.id.asc()).limit(1)
        job_id = session.execute(stmt).scalar_one_or_none()
        if job_id is None:
            return None

        claimed_at = datetime.utcnow()
        result = session.execute(
            update(Job)
            .where(Job.id == job_id)
            .where(Job.status == "pending")
            .values(
                status="running",
                started_at=claimed_at,
                heartbeat_at=claimed_at,
                worker_id=worker_id,
            )
        )
        session.commit()
        if result.rowcount == 1:
            return session.get(Job, job_id)


def heartbeat_running_jobs(
    session: Session,
    worker_id: str,
    now: datetime | None = None,
) -> int:
    """Refresh the liveness heartbeat for jobs this worker is currently running."""
    if not worker_id:
        return 0
    result = session.execute(
        update(Job)
        .where(Job.status == "running")
        .where(Job.worker_id == worker_id)
        .values(heartbeat_at=now or datetime.utcnow())
    )
    session.commit()
    return int(result.rowcount or 0)


def peek_next_pending_site_code(
    session: Session,
    now: datetime,
    site_codes: Iterable[str] | None = None,
) -> Optional[str]:
    normalized_site_codes = _normalize_site_codes(site_codes)
    stmt = (
        select(Job.site_code)
        .where(Job.status == "pending")
        .where(Job.run_after <= now)
    )
    if normalized_site_codes:
        stmt = stmt.where(Job.site_code.in_(normalized_site_codes))
    stmt = stmt.order_by(Job.run_after.asc(), Job.id.asc()).limit(1)
    return session.execute(stmt).scalar_one_or_none()


def requeue_stale_running_jobs(session: Session, stale_before: datetime) -> int:
    """Requeue running jobs whose worker has stopped sending heartbeats.

    Liveness is measured by ``heartbeat_at`` (falling back to ``started_at`` for
    jobs claimed before heartbeats existed). A long-running job from a *live*
    worker keeps its heartbeat fresh and is therefore never requeued; only jobs
    abandoned by a crashed/restarted worker cross the stale threshold. Ownership
    (``worker_id``) is cleared so the job can be re-claimed cleanly.
    """
    liveness = func.coalesce(Job.heartbeat_at, Job.started_at)
    result = session.execute(
        update(Job)
        .where(Job.status == "running")
        .where(liveness.is_not(None))
        .where(liveness < stale_before)
        .values(
            status="pending",
            started_at=None,
            finished_at=None,
            heartbeat_at=None,
            worker_id=None,
            error_message="stale_running_job_requeued",
        )
    )
    session.commit()
    return int(result.rowcount or 0)


def summarize_job_counts(
    session: Session,
    site_codes: Iterable[str] | None = None,
) -> dict[str, int]:
    """Return a mapping of job status -> count, optionally scoped to site codes."""
    stmt = select(Job.status, func.count()).group_by(Job.status)
    normalized_site_codes = _normalize_site_codes(site_codes)
    if normalized_site_codes:
        stmt = stmt.where(Job.site_code.in_(normalized_site_codes))
    return {
        str(status): int(count)
        for status, count in session.execute(stmt).all()
    }


def has_retry_job(session: Session, failed_job: Job) -> bool:
    stmt = (
        select(Job)
        .where(Job.job_type == "retry")
        .where(Job.site_code == failed_job.site_code)
        .where(Job.search_term == failed_job.search_term)
        .where(Job.attempt == failed_job.attempt + 1)
        .where(Job.created_at >= failed_job.created_at)
        .limit(1)
    )
    return session.execute(stmt).scalars().first() is not None


def mark_job_success(session: Session, job: Job) -> None:
    job.status = "success"
    job.finished_at = datetime.utcnow()
    session.commit()


def mark_job_failed(session: Session, job: Job, error_message: str) -> None:
    job.status = "failed"
    job.finished_at = datetime.utcnow()
    job.error_message = error_message
    session.commit()
