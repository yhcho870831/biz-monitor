from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import scheduler as scheduler_module
from app.services.scheduler import (
    AiCandidateItem,
    AiEvaluatedItem,
    PendingRunStats,
    process_job,
    run_scheduled_cycle,
    send_ai_recommendation_result,
    _run_site_ai_trailing,
)
from app.types import NoticeCandidate


def _ai_settings(**overrides):
    base = dict(
        enable_relevance_filter=True,
        ai_relevance_enabled=True,
        ai_relevance_gateway_url="http://gateway",
        ai_relevance_min_rule_score=1,
        ai_relevance_evaluate_rule_failed=True,
        ai_relevance_max_per_run=10,
        ai_relevance_share_threshold=70,
        ai_relevance_prepare_minutes_before_publish=10,
        slack_briefing_channel_id="C123",
        slack_g2b_early_flush_count=8,
        slack_deferred_publish_enabled=False,
        slack_backfill_only=False,
        slack_deferred_publish_since="",
        app_timezone="Asia/Seoul",
        site_enabled={"nia": True},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class SchedulerDelegationTest(unittest.TestCase):
    def test_run_scheduled_cycle_delegates_to_run_pending_jobs(self) -> None:
        settings = _ai_settings()
        notifier = SimpleNamespace(delete_file=lambda *_a, **_k: True)
        registry = object()

        @contextmanager
        def session_factory():
            yield object()

        stats = PendingRunStats(total_shared=3)

        with patch(
            "app.services.scheduler.cleanup_inactive_saved_notices", return_value=0
        ), patch(
            "app.services.scheduler.delete_expired_notices", return_value=0
        ), patch(
            "app.services.scheduler.enqueue_scheduled_jobs"
        ), patch(
            "app.services.scheduler._log_job_status_summary", return_value={}
        ), patch(
            "app.services.scheduler.run_pending_jobs", return_value=stats
        ) as run_pending:
            result = run_scheduled_cycle(session_factory, registry, notifier, settings)

        run_pending.assert_called_once()
        self.assertIs(result, stats)


class ProcessJobAiDecouplingTest(unittest.TestCase):
    def _run(self, settings, candidate):
        job = SimpleNamespace(
            site_code="nia",
            search_term="양식",
            keyword="양식",
            channel_id="C123",
            job_type="scheduled",
            id=1,
        )
        collector = SimpleNamespace(search=lambda _term: [candidate])
        registry = SimpleNamespace(get=lambda _code: collector)
        stats = PendingRunStats()
        notice = SimpleNamespace(id=1)

        evaluate = MagicMock()
        record_share = MagicMock()
        with patch.object(scheduler_module, "is_recruitment_notice", return_value=False), patch.object(
            scheduler_module, "excluded_scope_reason", return_value=None
        ), patch.object(
            scheduler_module, "is_active_notice", return_value=True
        ), patch.object(
            scheduler_module, "enrich_notice_candidate"
        ), patch.object(
            scheduler_module, "is_relevant", return_value=(True, "ok")
        ), patch.object(
            scheduler_module, "_is_broad_compound_term", return_value=False
        ), patch.object(
            scheduler_module, "upsert_notice", return_value=notice
        ), patch.object(
            scheduler_module, "already_shared", return_value=False
        ), patch.object(
            scheduler_module, "should_collect_attachments", return_value=False
        ), patch.object(
            scheduler_module, "generate_notice_summary"
        ), patch.object(
            scheduler_module, "ensure_candidate_amount", return_value=False
        ), patch.object(
            scheduler_module, "mark_job_success"
        ), patch.object(
            scheduler_module, "evaluate_notice_relevance", evaluate
        ), patch.object(
            scheduler_module, "record_share", record_share
        ):
            process_job(object(), job, registry, settings, stats)
        return stats, evaluate, record_share

    def test_ai_not_evaluated_inline_and_candidate_collected(self) -> None:
        candidate = NoticeCandidate(
            site_code="nia",
            site_notice_key="k1",
            title="육상양식 데이터 공고",
            source_url="https://example.com",
            priority_score=2,
        )
        stats, evaluate, _record_share = self._run(_ai_settings(), candidate)

        # AI must NOT run inside the body critical path.
        evaluate.assert_not_called()
        # Notice must still be queued for sending.
        self.assertIn(1, stats.site_pending_shares.get("nia", {}))
        # And collected for the trailing AI step.
        collected = stats.site_ai_candidates.get("nia", [])
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0].notice_id, 1)

    def test_deferred_publish_queues_pending_share_without_slack_send(self) -> None:
        candidate = NoticeCandidate(
            site_code="nia",
            site_notice_key="k1",
            title="NIA data notice",
            source_url="https://example.com",
            priority_score=2,
        )
        stats, evaluate, record_share = self._run(
            _ai_settings(slack_deferred_publish_enabled=True),
            candidate,
        )

        evaluate.assert_not_called()
        self.assertIn(1, stats.site_pending_shares.get("nia", {}))
        record_share.assert_called_once()
        args = record_share.call_args.args
        self.assertEqual(args[1], 1)
        self.assertEqual(args[2], "C123")
        self.assertEqual(args[3], "")


class SiteAiTrailingTest(unittest.TestCase):
    def test_trailing_evaluates_and_accumulates_recommendation(self) -> None:
        settings = _ai_settings()
        candidate = NoticeCandidate(
            site_code="nia",
            site_notice_key="k1",
            title="육상양식 데이터 공고",
            source_url="https://example.com",
            priority_score=2,
        )
        stats = PendingRunStats(
            site_ai_candidates={
                "nia": [
                    AiCandidateItem(
                        notice_id=1,
                        candidate=candidate,
                        channel_id="C123",
                        rule_passed=True,
                    )
                ]
            }
        )
        notifier = MagicMock()
        payload = {"status": "done", "fit_score": 90, "recommended_action": "review"}

        @contextmanager
        def session_factory():
            yield SimpleNamespace(get=lambda _model, _id: SimpleNamespace(id=1))

        with patch.object(
            scheduler_module, "evaluate_notice_relevance", return_value=payload
        ) as evaluate:
            _run_site_ai_trailing(session_factory, notifier, stats, settings, "nia")

        evaluate.assert_called_once()
        notifier.send_text.assert_not_called()
        # The site bucket is consumed.
        self.assertNotIn("nia", stats.site_ai_candidates)
        self.assertEqual(len(stats.ai_evaluated_items), 1)
        self.assertIsInstance(stats.ai_evaluated_items[0], AiEvaluatedItem)


class AggregateAiRecommendationSendTest(unittest.TestCase):
    def test_send_ai_recommendation_result_posts_one_combined_message(self) -> None:
        settings = _ai_settings(site_enabled={"nia": True, "g2b": True})
        notifier = MagicMock()
        stats = PendingRunStats(
            ai_evaluated_items=[
                AiEvaluatedItem(
                    notice_id=1,
                    candidate=NoticeCandidate(
                        site_code="nia",
                        site_notice_key="nia-1",
                        title="NIA notice",
                        source_url="https://example.com/nia",
                        priority_score=2,
                    ),
                    channel_id="C123",
                    payload={
                        "status": "done",
                        "fit_score": 90,
                        "recommended_action": "review",
                        "summary_for_slack": "NIA summary",
                    },
                ),
                AiEvaluatedItem(
                    notice_id=2,
                    candidate=NoticeCandidate(
                        site_code="g2b",
                        site_notice_key="g2b-1",
                        title="G2B notice",
                        source_url="https://example.com/g2b",
                        priority_score=3,
                    ),
                    channel_id="C123",
                    payload={
                        "status": "done",
                        "fit_score": 85,
                        "recommended_action": "watch",
                        "summary_for_slack": "G2B summary",
                    },
                ),
            ]
        )

        send_ai_recommendation_result(notifier, stats, settings)

        notifier.send_text.assert_called_once()
        args = notifier.send_text.call_args.args
        self.assertEqual(args[0], "C123")
        self.assertIn("*AI 추천 결과*", args[1])
        self.assertIn("NIA notice", args[1])
        self.assertIn("G2B notice", args[1])


    def test_ai_recommendation_reports_incomplete_for_next_publish_cycle(self) -> None:
        settings = _ai_settings(site_enabled={"nia": True})
        message = scheduler_module._format_ai_recommendation_result(
            [
                AiEvaluatedItem(
                    notice_id=1,
                    candidate=NoticeCandidate(
                        site_code="nia",
                        site_notice_key="nia-1",
                        title="NIA incomplete notice",
                        source_url="https://example.com/nia",
                        priority_score=2,
                    ),
                    channel_id="C123",
                    payload={"status": "timeout"},
                )
            ],
            settings,
        )

        self.assertIn("\ubbf8\uc644\ub8cc", message)
        self.assertIn("\ub2e4\uc74c \uac8c\uc2dc \uc8fc\uae30", message)


class BroadCompoundRegressionTest(unittest.TestCase):
    def _job(self):
        return SimpleNamespace(
            site_code="g2b",
            search_term="\uc720\uc9c0\ubcf4\uc218",
            keyword="\uc720\uc9c0\ubcf4\uc218",
            channel_id="C123",
            job_type="scheduled",
            id=10,
        )

    def _candidate(self, title: str) -> NoticeCandidate:
        return NoticeCandidate(
            site_code="g2b",
            site_notice_key="g2b-1",
            title=title,
            source_url="https://example.com",
            priority_score=1,
            organization="\uae30\uad00",
        )

    def _base_patches(self, candidate, site_keywords):
        collector = SimpleNamespace(search=lambda _term: [candidate])
        registry = SimpleNamespace(get=lambda _code: collector)
        stats = PendingRunStats()
        notice = SimpleNamespace(id=1)
        patches = [
            patch.object(scheduler_module, "is_recruitment_notice", return_value=False),
            patch.object(scheduler_module, "excluded_scope_reason", return_value=None),
            patch.object(scheduler_module, "is_active_notice", return_value=True),
            patch.object(scheduler_module, "enrich_notice_candidate"),
            patch.object(scheduler_module, "is_relevant", return_value=(True, "ok")),
            patch.object(scheduler_module, "upsert_notice", return_value=notice),
            patch.object(scheduler_module, "already_shared", return_value=False),
            patch.object(scheduler_module, "should_collect_attachments", return_value=False),
            patch.object(scheduler_module, "generate_notice_summary"),
            patch.object(scheduler_module, "ensure_candidate_amount", return_value=False),
            patch.object(scheduler_module, "mark_job_success"),
            patch.object(scheduler_module, "list_enabled_site_keywords", return_value=[("g2b", kw) for kw in site_keywords]),
        ]
        return registry, stats, patches

    def test_broad_maintenance_term_without_supporting_keyword_is_not_shared(self) -> None:
        settings = _ai_settings(enable_relevance_filter=True, slack_briefing_channel_id="C123")
        candidate = self._candidate("\uc2dc\uc124 \uc720\uc9c0\ubcf4\uc218 \uc6a9\uc5ed")
        registry, stats, patches = self._base_patches(candidate, ["\uc720\uc9c0\ubcf4\uc218", "\ud574\uc591\uad00\uce21"])

        with patch.object(scheduler_module, "_is_broad_compound_term", return_value=True):
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patches[11]:
                process_job(object(), self._job(), registry, settings, stats)

        self.assertEqual(stats.site_pending_shares.get("g2b", {}), {})

    def test_broad_maintenance_term_with_supporting_keyword_is_shared(self) -> None:
        settings = _ai_settings(enable_relevance_filter=True, slack_briefing_channel_id="C123")
        candidate = self._candidate("\ud574\uc591\uad00\uce21 \uc2dc\uc2a4\ud15c \uc720\uc9c0\ubcf4\uc218 \uc6a9\uc5ed")
        registry, stats, patches = self._base_patches(candidate, ["\uc720\uc9c0\ubcf4\uc218", "\ud574\uc591\uad00\uce21"])

        with patch.object(scheduler_module, "_is_broad_compound_term", return_value=True):
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10], patches[11]:
                process_job(object(), self._job(), registry, settings, stats)

        self.assertIn(1, stats.site_pending_shares.get("g2b", {}))


if __name__ == "__main__":
    unittest.main()
