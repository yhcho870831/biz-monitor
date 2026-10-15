from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Notice, NoticeAiEvaluation, SlackShare
from app.services import scheduler as scheduler_module
from app.services.scheduler import (
    _latest_unposted_ai_items,
    prepare_deferred_ai_evaluations,
)


class AiDecoupledTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, future=True)
        self.settings = SimpleNamespace(
            slack_briefing_channel_id="C123",
            slack_deferred_publish_since="",
            ai_relevance_enabled=True,
            ai_relevance_gateway_url="http://gateway",
            ai_relevance_max_per_run=30,
            ai_relevance_min_rule_score=1,
            ai_relevance_retry_cooldown_minutes=30,
            ai_relevance_retry_window_hours=6,
        )

    def _add_notice(self, key: str, *, status=None, age_minutes=0, span_hours=0, posted=False) -> int:
        now = datetime.utcnow()
        with self.session_factory() as session:
            notice = Notice(
                site_code="nia", site_notice_key=key, title=key,
                source_url="https://example.com/%s" % key,
                deadline_at=now + timedelta(days=1),
                raw_payload_json=json.dumps({"priority_score": 1}),
                first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now,
            )
            session.add(notice)
            session.flush()
            session.add(SlackShare(notice_id=notice.id, channel_id="C123", message_ts="",
                                   share_type="scheduled", shared_at=now))
            if status:
                updated = now - timedelta(minutes=age_minutes)
                session.add(NoticeAiEvaluation(
                    notice_id=notice.id, provider="p", model="m", prompt_version="v1",
                    input_hash=key, status=status, fit_score=80 if status == "done" else None,
                    recommended_action="review" if status == "done" else None,
                    created_at=updated - timedelta(hours=span_hours), updated_at=updated,
                    ai_recommendation_posted_at=now if posted else None,
                ))
            session.commit()
            return notice.id

    def _prepare(self, **kwargs):
        calls: list[int] = []

        def evaluate(_s, _settings, notice, _c):
            calls.append(notice.id)
            return {"status": "failed"}

        with patch.object(scheduler_module, "is_active_notice", return_value=True), patch.object(
            scheduler_module, "enrich_notice_candidate"
        ), patch.object(scheduler_module, "evaluate_notice_relevance", side_effect=evaluate):
            prepare_deferred_ai_evaluations(self.session_factory, self.settings, **kwargs)
        return calls

    def test_failed_evaluation_waits_for_cooldown(self) -> None:
        self._add_notice("cooling", status="failed", age_minutes=5)
        ready = self._add_notice("ready", status="failed", age_minutes=45)
        self.assertEqual(self._prepare(retry_immediately=False), [ready])

    def test_failed_evaluation_stops_after_retry_window(self) -> None:
        self._add_notice("final", status="failed", age_minutes=45, span_hours=7)
        self.assertEqual(self._prepare(retry_immediately=False), [])

    def test_loop_mode_does_not_retry_in_same_pass(self) -> None:
        nid = self._add_notice("fresh")
        self.assertEqual(self._prepare(retry_immediately=False), [nid])

    def _unposted(self):
        with self.session_factory() as session:
            items, ids = _latest_unposted_ai_items(session, self.settings)
            return sorted(i.candidate.title for i in items), len(ids)

    def test_publish_skips_unfinished_and_in_window_failures(self) -> None:
        self._add_notice("no-eval")
        self._add_notice("retrying", status="failed", age_minutes=5, span_hours=1)
        done = self._add_notice("done", status="done")
        self._add_notice("final", status="failed", age_minutes=5, span_hours=7)
        with patch.object(scheduler_module, "is_active_notice", return_value=True), patch.object(
            scheduler_module, "enrich_notice_candidate"
        ):
            titles, marked = self._unposted()
        self.assertEqual(titles, ["done", "final"])
        self.assertEqual(marked, 2)
        self.assertIsNotNone(done)


if __name__ == "__main__":
    unittest.main()
