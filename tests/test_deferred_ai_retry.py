from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Notice, SlackShare
from app.services import scheduler as scheduler_module
from app.services.scheduler import prepare_deferred_ai_evaluations


class DeferredAiRetryTest(unittest.TestCase):
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
        )

    def _add_notice(self, key: str) -> int:
        now = datetime.utcnow()
        with self.session_factory() as session:
            notice = Notice(
                site_code="nia",
                site_notice_key=key,
                title=key,
                source_url="https://example.com/%s" % key,
                deadline_at=now + timedelta(days=1),
                raw_payload_json=json.dumps({"priority_score": 1}),
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(notice)
            session.flush()
            session.add(
                SlackShare(
                    notice_id=notice.id,
                    channel_id="C123",
                    message_ts="",
                    share_type="scheduled",
                    shared_at=now,
                )
            )
            session.commit()
            return notice.id

    def test_retries_first_failure_after_all_first_attempts(self) -> None:
        first_id = self._add_notice("first")
        second_id = self._add_notice("second")
        done = {"status": "done", "fit_score": 80}
        evaluated_notice_ids: list[int] = []

        def evaluate(_session, _settings, notice, _candidate):
            evaluated_notice_ids.append(notice.id)
            return [{"status": "failed"}, done, done][len(evaluated_notice_ids) - 1]

        with patch.object(scheduler_module, "get_latest_ai_evaluation", return_value=None), patch.object(
            scheduler_module, "is_active_notice", return_value=True
        ), patch.object(scheduler_module, "enrich_notice_candidate"), patch.object(
            scheduler_module,
            "evaluate_notice_relevance",
            side_effect=evaluate,
        ):
            attempted = prepare_deferred_ai_evaluations(self.session_factory, self.settings)

        self.assertEqual(attempted, 3)
        self.assertEqual(evaluated_notice_ids, [first_id, second_id, first_id])

    def test_does_not_retry_failure_after_its_error_was_posted(self) -> None:
        self._add_notice("posted-final-failure")
        final_failure = SimpleNamespace(
            status="failed",
            ai_recommendation_posted_at=datetime.utcnow(),
        )

        with patch.object(
            scheduler_module,
            "get_latest_ai_evaluation",
            return_value=final_failure,
        ), patch.object(scheduler_module, "evaluate_notice_relevance") as evaluate:
            attempted = prepare_deferred_ai_evaluations(self.session_factory, self.settings)

        self.assertEqual(attempted, 0)
        evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
