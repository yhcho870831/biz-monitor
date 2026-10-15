from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.main import _catchup_publish_times, _due_scheduler_mark


class CatchupPublishTest(unittest.TestCase):
    def test_slots_follow_each_publish_time(self) -> None:
        self.assertEqual(
            _catchup_publish_times(["09:00", "16:00"], [30, 90]),
            ["09:30", "10:30", "16:30", "17:30"],
        )

    def test_non_positive_offsets_ignored(self) -> None:
        self.assertEqual(_catchup_publish_times(["09:00"], [0, -5]), [])

    def test_slot_becomes_due_once(self) -> None:
        slots = _catchup_publish_times(["16:00"], [30])
        now = datetime(2026, 10, 15, 16, 31, tzinfo=timezone.utc)
        mark = _due_scheduler_mark(now, slots, "")
        self.assertEqual(mark, "2026-10-15 16:30")
        self.assertEqual(_due_scheduler_mark(now, slots, mark), "")


if __name__ == "__main__":
    unittest.main()


class RepeatedPublishTest(unittest.TestCase):
    """100 queued notices: repeated publishes never post one twice, nothing is lost."""

    def test_hundred_notices_post_once_each_across_runs(self) -> None:
        import json
        from datetime import timedelta
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        from app.db import Base
        from app.models import Notice, SlackShare
        from app.services.scheduler import publish_deferred_scheduled_notices

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine, future=True)
        now = datetime.utcnow()
        with session_factory() as session:
            for i in range(100):
                notice = Notice(
                    site_code="nia", site_notice_key="n%03d" % i, title="notice %03d" % i,
                    source_url="https://example.com/%d" % i,
                    deadline_at=now + timedelta(days=3),
                    raw_payload_json=json.dumps({"priority_score": 1}),
                    first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
                )
                session.add(notice)
                session.flush()
                session.add(SlackShare(notice_id=notice.id, channel_id="C123", message_ts="",
                                       share_type="scheduled", shared_at=now))
            session.commit()

        notifier = MagicMock()
        counter = iter(range(1, 10_000))
        notifier.send_text.side_effect = lambda *a, **k: "ts%d" % next(counter)
        settings = SimpleNamespace(
            slack_briefing_channel_id="C123",
            slack_deferred_publish_enabled=True,
            ai_relevance_enabled=False,
            slack_max_notices_per_site=10,
            slack_max_zero_star_per_site=10,
            calendar_web_url="",
        )
        posted_after_run = []
        for _ in range(12):
            publish_deferred_scheduled_notices(session_factory, notifier, settings)
            with session_factory() as session:
                posted_after_run.append(
                    len(session.execute(select(SlackShare).where(SlackShare.message_ts != "")).scalars().all())
                )

        self.assertEqual(posted_after_run[-1], 100)
        # Growth per run is bounded by the per-site cap and never decreases.
        self.assertEqual(posted_after_run, sorted(posted_after_run))
        self.assertLessEqual(max(b - a for a, b in zip([0] + posted_after_run, posted_after_run)), 10)
        sent_titles = " ".join(call.args[1] for call in notifier.send_text.call_args_list)
        for i in range(100):
            self.assertEqual(sent_titles.count("notice %03d" % i), 1, i)
