from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.collectors import g2b as g2b_module
from app.collectors.base import PartialSearchError
from app.collectors.g2b import G2BCollector
from app.services import scheduler as scheduler_module
from app.services.scheduler import PendingRunStats, process_job
from app.types import NoticeCandidate


def _candidate(key: str) -> NoticeCandidate:
    return NoticeCandidate(
        site_code="g2b", site_notice_key=key, title="t " + key, source_url="https://example.com"
    )


class SearchStagesTest(unittest.TestCase):
    def _search(self, bid, prespec):
        collector = G2BCollector()
        with patch.object(G2BCollector, "_search_bid_notices", side_effect=bid), patch.object(
            G2BCollector, "_search_pre_announcements", side_effect=prespec
        ) as pre:
            try:
                return collector.search("양식"), pre
            except Exception as exc:  # noqa: BLE001 - the tests inspect the error
                return exc, pre

    def test_both_stages_ok_returns_deduplicated_results(self):
        result, _ = self._search(
            lambda t: [_candidate("a"), _candidate("b")], lambda t: [_candidate("b"), _candidate("c")]
        )
        self.assertEqual([c.site_notice_key for c in result], ["a", "b", "c"])

    def test_bid_failure_still_runs_pre_specification_and_keeps_its_results(self):
        def bid(_t):
            raise RuntimeError("bid menu failed")

        result, pre = self._search(bid, lambda t: [_candidate("p1")])
        pre.assert_called_once()
        self.assertIsInstance(result, PartialSearchError)
        self.assertEqual([c.site_notice_key for c in result.results], ["p1"])
        self.assertEqual(list(result.failures), ["bid"])

    def test_pre_specification_failure_keeps_bid_results(self):
        def prespec(_t):
            raise RuntimeError("binding timeout")

        result, _ = self._search(lambda t: [_candidate("b1")], prespec)
        self.assertIsInstance(result, PartialSearchError)
        self.assertEqual([c.site_notice_key for c in result.results], ["b1"])
        self.assertEqual(list(result.failures), ["pre_specification"])

    def test_both_stages_failing_is_an_ordinary_failure(self):
        def bid(_t):
            raise RuntimeError("first")

        def prespec(_t):
            raise RuntimeError("second")

        result, _ = self._search(bid, prespec)
        self.assertNotIsInstance(result, PartialSearchError)
        self.assertEqual(str(result), "first")


class FreshBrowserRetryTest(unittest.TestCase):
    def test_retries_with_a_new_browser_then_succeeds(self):
        calls = []

        def action():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("stuck home screen")
            return "ok"

        with patch.object(g2b_module.time, "sleep"):
            self.assertEqual(G2BCollector()._with_fresh_browser("bid", action), "ok")
        self.assertEqual(len(calls), 2)

    def test_gives_up_after_the_configured_attempts(self):
        action = MagicMock(side_effect=RuntimeError("down"))
        with patch.object(g2b_module.time, "sleep"):
            with self.assertRaises(RuntimeError):
                G2BCollector()._with_fresh_browser("bid", action)
        self.assertEqual(action.call_count, g2b_module.G2B_BROWSER_ATTEMPTS)


class MenuWaitTest(unittest.TestCase):
    def test_waits_for_the_menu_before_clicking(self):
        page = MagicMock()
        G2BCollector()._click_menu(page, "menu_id_1", 10)
        page.wait_for_selector.assert_called_once()
        self.assertEqual(page.wait_for_selector.call_args[0][0], "#menu_id_1")
        page.evaluate.assert_called_once()
        page.wait_for_timeout.assert_called_once_with(10)

    def test_missing_menu_is_an_explicit_error_not_a_skipped_click(self):
        page = MagicMock()
        page.wait_for_selector.side_effect = TimeoutError("30000ms")
        with self.assertRaisesRegex(RuntimeError, "menu element not found: menu_id_1"):
            G2BCollector()._click_menu(page, "menu_id_1", 10)
        page.evaluate.assert_not_called()


class ProcessJobPartialTest(unittest.TestCase):
    def test_partial_results_are_stored_then_job_fails_for_retry(self):
        candidate = _candidate("p1")
        candidate.priority_score = 2
        error = PartialSearchError([candidate], {"bid": RuntimeError("menu")})

        def search(_term):
            raise error

        job = SimpleNamespace(
            site_code="g2b", search_term="양식", keyword="양식", channel_id="C1",
            job_type="scheduled", id=7,
        )
        registry = SimpleNamespace(get=lambda _c: SimpleNamespace(search=search))
        settings = SimpleNamespace(
            enable_relevance_filter=True, ai_relevance_enabled=False,
            slack_briefing_channel_id="C1", slack_g2b_early_flush_count=0,
            slack_deferred_publish_enabled=False, slack_backfill_only=False,
            ai_relevance_min_rule_score=1, ai_relevance_evaluate_rule_failed=False,
            site_enabled={"g2b": True},
        )
        stats = PendingRunStats()
        upsert = MagicMock(return_value=SimpleNamespace(id=1))
        success = MagicMock()
        with patch.object(scheduler_module, "is_recruitment_notice", return_value=False), patch.object(
            scheduler_module, "excluded_scope_reason", return_value=None
        ), patch.object(scheduler_module, "is_active_notice", return_value=True), patch.object(
            scheduler_module, "enrich_notice_candidate"
        ), patch.object(scheduler_module, "is_relevant", return_value=(True, "ok")), patch.object(
            scheduler_module, "_is_broad_compound_term", return_value=False
        ), patch.object(scheduler_module, "upsert_notice", upsert), patch.object(
            scheduler_module, "already_shared", return_value=False
        ), patch.object(scheduler_module, "should_collect_attachments", return_value=False), patch.object(
            scheduler_module, "generate_notice_summary"
        ), patch.object(scheduler_module, "ensure_candidate_amount", return_value=False), patch.object(
            scheduler_module, "mark_job_success", success
        ), patch.object(scheduler_module, "record_share"):
            with self.assertRaises(PartialSearchError):
                process_job(object(), job, registry, settings, stats)

        upsert.assert_called_once()  # the surviving stage's notice was persisted
        success.assert_not_called()  # but the job is failed so the stage is retried
        self.assertIn(1, stats.site_pending_shares.get("g2b", {}))


if __name__ == "__main__":
    unittest.main()
