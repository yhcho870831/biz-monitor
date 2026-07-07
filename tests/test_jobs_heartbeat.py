from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from app.bootstrap import create_schema
from app.db import create_db_engine, create_session_factory
from app.models import Job
from app.repositories.jobs import (
    claim_next_pending_job,
    create_job,
    heartbeat_running_jobs,
    requeue_stale_running_jobs,
)


class JobsHeartbeatTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        engine = create_db_engine(f"sqlite:///{self._tmp.name}")
        create_schema(engine)
        self.session_factory = create_session_factory(engine)

    def tearDown(self) -> None:
        os.unlink(self._tmp.name)

    def _make_running_job(self, session, worker_id, heartbeat_age_minutes):
        job = create_job(
            session=session,
            job_type="scheduled",
            site_code="g2b",
            status="pending",
            run_after=datetime.utcnow() - timedelta(minutes=1),
        )
        claimed = claim_next_pending_job(
            session, datetime.utcnow(), worker_id=worker_id
        )
        # Backdate the heartbeat to simulate elapsed time.
        claimed.heartbeat_at = datetime.utcnow() - timedelta(minutes=heartbeat_age_minutes)
        claimed.started_at = datetime.utcnow() - timedelta(minutes=heartbeat_age_minutes)
        session.commit()
        return claimed.id

    def test_fresh_heartbeat_is_not_requeued(self) -> None:
        with self.session_factory() as session:
            job_id = self._make_running_job(session, "worker-a", heartbeat_age_minutes=2)
            stale_before = datetime.utcnow() - timedelta(minutes=30)
            requeued = requeue_stale_running_jobs(session, stale_before)
            self.assertEqual(requeued, 0)
            session.expire_all()
            job = session.get(Job, job_id)
            self.assertEqual(job.status, "running")
            self.assertEqual(job.worker_id, "worker-a")

    def test_cold_heartbeat_is_requeued_and_ownership_cleared(self) -> None:
        with self.session_factory() as session:
            job_id = self._make_running_job(session, "worker-a", heartbeat_age_minutes=45)
            stale_before = datetime.utcnow() - timedelta(minutes=30)
            requeued = requeue_stale_running_jobs(session, stale_before)
            self.assertEqual(requeued, 1)
            session.expire_all()
            job = session.get(Job, job_id)
            self.assertEqual(job.status, "pending")
            self.assertIsNone(job.worker_id)
            self.assertIsNone(job.heartbeat_at)

    def test_heartbeat_keeps_long_job_alive(self) -> None:
        with self.session_factory() as session:
            job_id = self._make_running_job(session, "worker-a", heartbeat_age_minutes=45)
            # The worker emits a heartbeat just before stale recovery runs.
            updated = heartbeat_running_jobs(session, "worker-a")
            self.assertEqual(updated, 1)
            stale_before = datetime.utcnow() - timedelta(minutes=30)
            requeued = requeue_stale_running_jobs(session, stale_before)
            self.assertEqual(requeued, 0)
            session.expire_all()
            self.assertEqual(session.get(Job, job_id).status, "running")

    def test_heartbeat_only_touches_own_jobs(self) -> None:
        with self.session_factory() as session:
            self._make_running_job(session, "worker-a", heartbeat_age_minutes=1)
            updated = heartbeat_running_jobs(session, "worker-b")
            self.assertEqual(updated, 0)


if __name__ == "__main__":
    unittest.main()
