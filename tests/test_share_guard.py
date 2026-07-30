from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Notice, NoticeShareGuard, SlackShare
from app.repositories.notices import delete_expired_notices
from app.repositories.shares import (
    already_shared,
    backfill_share_guards_from_sqlite_backup,
    record_share,
    suppress_share,
)

CHANNEL = "C123"
MESSAGE_TS = "1700000000.100100"


class ShareGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, future=True)
        self.now = datetime.utcnow()

    def _add_notice(self, session, site_notice_key: str, *, seen_at: datetime) -> Notice:
        notice = Notice(
            site_code="g2b",
            site_notice_key=site_notice_key,
            title="procurement plan",
            source_url="https://example.com/plan",
            raw_payload_json=json.dumps(
                {"announcement_stage": "procurement_plan", "prcsYmd": "20200101"}
            ),
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            created_at=seen_at,
            updated_at=seen_at,
        )
        session.add(notice)
        session.flush()
        return notice

    def test_already_shared_is_false_for_a_fresh_notice(self) -> None:
        with self.session_factory() as session:
            notice = self._add_notice(session, "plan-fresh", seen_at=self.now)
            session.commit()

            self.assertFalse(already_shared(session, notice.id, CHANNEL))

    def test_record_share_creates_guard(self) -> None:
        with self.session_factory() as session:
            notice = self._add_notice(session, "plan-shared", seen_at=self.now)
            session.commit()
            record_share(session, notice.id, CHANNEL, MESSAGE_TS, "scheduled", 0)

        with self.session_factory() as session:
            guard = session.query(NoticeShareGuard).one()
            self.assertEqual(guard.site_code, "g2b")
            self.assertEqual(guard.site_notice_key, "plan-shared")
            self.assertEqual(guard.channel_id, CHANNEL)
            self.assertEqual(guard.last_message_ts, MESSAGE_TS)
            self.assertIsNone(guard.suppressed_at)

    def test_suppress_share_records_reason_on_guard(self) -> None:
        with self.session_factory() as session:
            notice = self._add_notice(session, "plan-suppressed", seen_at=self.now)
            session.commit()
            record_share(session, notice.id, CHANNEL, "", "scheduled", 0)
            share = session.query(SlackShare).one()
            suppress_share(session, share, "g2b_procurement_plan_not_published")

        with self.session_factory() as session:
            guard = session.query(NoticeShareGuard).one()
            self.assertIsNotNone(guard.suppressed_at)
            self.assertEqual(
                guard.suppressed_reason,
                "g2b_procurement_plan_not_published",
            )

    def test_guard_blocks_reshare_after_retention_deleted_the_notice(self) -> None:
        long_ago = self.now - timedelta(days=400)
        with self.session_factory() as session:
            notice = self._add_notice(session, "plan-recycled", seen_at=long_ago)
            session.commit()
            record_share(session, notice.id, CHANNEL, MESSAGE_TS, "scheduled", 0)

        with self.session_factory() as session:
            self.assertEqual(delete_expired_notices(session, now=self.now), 1)
            self.assertEqual(session.query(Notice).count(), 0)
            self.assertEqual(session.query(SlackShare).count(), 0)
            self.assertEqual(session.query(NoticeShareGuard).count(), 1)

        # The same source notice turns up again in a later collection.
        with self.session_factory() as session:
            recollected = self._add_notice(session, "plan-recycled", seen_at=self.now)
            session.commit()

            self.assertTrue(already_shared(session, recollected.id, CHANNEL))

    def test_guard_is_scoped_per_channel(self) -> None:
        with self.session_factory() as session:
            notice = self._add_notice(session, "plan-channel", seen_at=self.now)
            session.commit()
            record_share(session, notice.id, CHANNEL, MESSAGE_TS, "scheduled", 0)

            self.assertTrue(already_shared(session, notice.id, CHANNEL))
            self.assertFalse(already_shared(session, notice.id, "C999"))

    def test_backup_backfill_blocks_a_notice_deleted_before_guard_migration(self) -> None:
        with TemporaryDirectory() as temp_dir:
            backup_path = Path(temp_dir) / "pre-guard.db"
            backup_engine = create_engine("sqlite:///%s" % backup_path)
            Base.metadata.create_all(backup_engine)
            backup_session_factory = sessionmaker(bind=backup_engine, future=True)
            historical_time = self.now - timedelta(days=400)
            with backup_session_factory() as backup_session:
                historical_notice = Notice(
                    site_code="g2b",
                    site_notice_key="legacy-reappearing-notice",
                    title="historical notice",
                    source_url="https://example.com/historical",
                    first_seen_at=historical_time,
                    last_seen_at=historical_time,
                    created_at=historical_time,
                    updated_at=historical_time,
                )
                backup_session.add(historical_notice)
                backup_session.flush()
                backup_session.add(
                    SlackShare(
                        notice_id=historical_notice.id,
                        channel_id=CHANNEL,
                        message_ts=MESSAGE_TS,
                        share_type="scheduled",
                        shared_at=historical_time,
                    )
                )
                backup_session.commit()
            backup_engine.dispose()

            with self.session_factory() as session:
                self.assertEqual(
                    backfill_share_guards_from_sqlite_backup(session, backup_path), 1
                )
                session.commit()
                recollected = self._add_notice(
                    session,
                    "legacy-reappearing-notice",
                    seen_at=self.now,
                )
                session.commit()

                self.assertTrue(already_shared(session, recollected.id, CHANNEL))


if __name__ == "__main__":
    unittest.main()
