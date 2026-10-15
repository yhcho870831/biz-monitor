from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Job, Notice, SlackShare
from app.repositories.jobs import delete_old_jobs


class JobsRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine, future=True)()
        self.now = datetime.utcnow()

    def _job(self, status: str, age_days: int) -> Job:
        created = self.now - timedelta(days=age_days)
        job = Job(
            job_type="scheduled", site_code="g2b", search_term="x", status=status,
            run_after=created, created_at=created,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def test_deletes_only_old_finished_jobs_and_keeps_share_history(self) -> None:
        old_ok = self._job("success", 40)
        old_failed_id = self._job("failed", 31).id
        old_pending = self._job("pending", 45)
        old_running = self._job("running", 45)
        recent = self._job("success", 5)
        notice = Notice(
            site_code="g2b", site_notice_key="k", title="t", source_url="u",
            first_seen_at=self.now, last_seen_at=self.now,
            created_at=self.now, updated_at=self.now,
        )
        self.session.add(notice)
        self.session.flush()
        share = SlackShare(
            notice_id=notice.id, channel_id="C1", message_ts="1.1",
            share_type="scheduled", job_id=old_ok.id, shared_at=self.now,
        )
        self.session.add(share)
        self.session.commit()

        deleted = delete_old_jobs(self.session, now=self.now)

        self.assertEqual(deleted, 2)
        remaining = {row.id for row in self.session.query(Job)}
        self.assertEqual(remaining, {old_pending.id, old_running.id, recent.id})
        self.assertNotIn(old_failed_id, remaining)
        self.session.refresh(share)
        self.assertIsNone(share.job_id)
        self.assertEqual(share.message_ts, "1.1")
        self.assertEqual(self.session.query(SlackShare).count(), 1)


if __name__ == "__main__":
    unittest.main()
