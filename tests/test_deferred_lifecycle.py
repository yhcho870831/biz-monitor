from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Notice, SlackShare
from app.services.scheduler import (
    _pending_slack_share_rows,
    publish_deferred_scheduled_notices,
)


class DeferredLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, future=True)
        self.settings = SimpleNamespace(
            slack_briefing_channel_id="C123",
            slack_deferred_publish_enabled=True,
            ai_relevance_enabled=False,
        )

    def _queued_notice(self, *, deadline_at: datetime, suppressed_at=None) -> SlackShare:
        now = datetime.utcnow()
        with self.session_factory() as session:
            notice = Notice(
                site_code="g2b",
                site_notice_key="bid-%s" % now.timestamp(),
                title="G2B bid",
                source_url="https://example.com/bid",
                deadline_at=deadline_at,
                raw_payload_json=json.dumps({"announcement_stage": "bid_notice"}),
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(notice)
            session.flush()
            share = SlackShare(
                notice_id=notice.id,
                channel_id="C123",
                message_ts="",
                share_type="scheduled",
                shared_at=now,
                suppressed_at=suppressed_at,
            )
            session.add(share)
            session.commit()
            session.refresh(share)
            return share

    def test_pending_query_excludes_suppressed_share(self) -> None:
        self._queued_notice(
            deadline_at=datetime.utcnow() + timedelta(days=1),
            suppressed_at=datetime.utcnow(),
        )
        with self.session_factory() as session:
            self.assertEqual(_pending_slack_share_rows(session, self.settings), [])

    def test_deferred_publish_suppresses_expired_notice_without_sending(self) -> None:
        share = self._queued_notice(deadline_at=datetime.utcnow() - timedelta(minutes=1))
        notifier = MagicMock()

        stats = publish_deferred_scheduled_notices(
            self.session_factory,
            notifier,
            self.settings,
        )

        self.assertEqual(stats.total_shared, 0)
        notifier.send_text.assert_not_called()
        with self.session_factory() as session:
            saved = session.get(SlackShare, share.id)
            self.assertIsNotNone(saved.suppressed_at)
            self.assertEqual(saved.suppressed_reason, "inactive_at_deferred_publish")


if __name__ == "__main__":
    unittest.main()
