from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import collector_health as ch
from app.services.collector_health import (
    AiHealth,
    SiteHealth,
    evaluate_ai_health,
    evaluate_health,
    format_ai_alert,
    format_health_alert,
    resolve_monitored_sites,
    run_collector_health_check,
)


def _settings(**overrides):
    base = dict(
        collector_health_enabled=True,
        collector_health_channel_id="",
        collector_health_window_hours=24,
        collector_health_failed_threshold=1,
        collector_health_exclude_sites=["g2b"],
        slack_command_channel_id="C_CMD",
        slack_briefing_channel_id="C_BRIEF",
        site_enabled={
            "g2b": True,
            "d2b": True,
            "iris": True,
            "kmiti": True,
            "kimst": True,
            "nia": True,
        },
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class ResolveMonitoredSitesTest(unittest.TestCase):
    def test_excludes_g2b_by_default(self):
        sites = resolve_monitored_sites(_settings())
        self.assertNotIn("g2b", sites)
        self.assertEqual(sites, ["d2b", "iris", "kimst", "kmiti", "nia"])

    def test_skips_disabled_sites(self):
        sites = resolve_monitored_sites(
            _settings(site_enabled={"g2b": True, "d2b": True, "iris": False})
        )
        self.assertEqual(sites, ["d2b"])


class EvaluateHealthTest(unittest.TestCase):
    def test_healthy_site_not_flagged(self):
        rows = [("d2b", "success", ""), ("d2b", "success", "")]
        last = {"d2b": datetime(2026, 7, 7, 0, 0)}
        report = evaluate_health(["d2b"], rows, last)
        self.assertFalse(report["d2b"].flagged)
        self.assertEqual(report["d2b"].success_count, 2)

    def test_failure_flags_site_and_keeps_first_error(self):
        rows = [
            ("iris", "success", ""),
            ("iris", "failed", "Page.goto: Timeout 30000ms exceeded.\nCall log:..."),
        ]
        report = evaluate_health(["iris"], rows, {"iris": None})
        self.assertTrue(report["iris"].flagged)
        self.assertEqual(report["iris"].failed_count, 1)
        self.assertEqual(
            report["iris"].sample_error, "Page.goto: Timeout 30000ms exceeded."
        )

    def test_zero_success_flags_stale(self):
        report = evaluate_health(["nia"], [], {"nia": None})
        self.assertTrue(report["nia"].flagged)
        self.assertIn("성공 0건(수집 중단 의심)", report["nia"].reasons)

    def test_failed_threshold_respected(self):
        rows = [("d2b", "success", ""), ("d2b", "failed", "boom")]
        # threshold 2 => single failure should not flag on failure reason
        report = evaluate_health(["d2b"], rows, {"d2b": datetime.utcnow()}, failed_threshold=2)
        self.assertFalse(report["d2b"].flagged)

    def test_ignores_unmonitored_sites(self):
        rows = [("g2b", "failed", "boom")]
        report = evaluate_health(["d2b"], rows, {"d2b": datetime.utcnow()})
        self.assertNotIn("g2b", report)


class FormatAlertTest(unittest.TestCase):
    def test_message_contains_site_and_reason(self):
        health = SiteHealth(
            site_code="iris",
            success_count=1,
            failed_count=2,
            last_success_at=datetime(2026, 7, 7, 1, 2),
            sample_error="Timeout",
            flagged=True,
            reasons=["실패 2건"],
        )
        msg = format_health_alert([health], 24, now=datetime(2026, 7, 7, 7, 0))
        self.assertIn("iris", msg)
        self.assertIn("실패 2건", msg)
        self.assertIn("Timeout", msg)


class RunCheckPolicyTest(unittest.TestCase):
    @contextmanager
    def _fake_factory(self):
        yield MagicMock()

    def _patch_report(self, report):
        return patch.object(ch, "build_report", return_value=report)

    def test_no_alert_when_healthy(self):
        report = {"d2b": SiteHealth("d2b", success_count=3, flagged=False)}
        notifier = MagicMock()
        with self._patch_report(report):
            summary = run_collector_health_check(
                self._fake_factory, notifier, _settings()
            )
        notifier.send_text.assert_not_called()
        self.assertFalse(summary["sent"])

    def test_alert_when_unhealthy_uses_command_channel(self):
        report = {
            "iris": SiteHealth("iris", failed_count=2, flagged=True, reasons=["실패 2건"])
        }
        notifier = MagicMock()
        notifier.send_text.return_value = "111.222"
        with self._patch_report(report):
            summary = run_collector_health_check(
                self._fake_factory, notifier, _settings()
            )
        notifier.send_text.assert_called_once()
        self.assertEqual(notifier.send_text.call_args[0][0], "C_CMD")
        self.assertTrue(summary["sent"])
        self.assertEqual(summary["unhealthy"], ["iris"])

    def test_dry_run_never_sends(self):
        report = {"iris": SiteHealth("iris", failed_count=2, flagged=True)}
        notifier = MagicMock()
        with self._patch_report(report):
            summary = run_collector_health_check(
                self._fake_factory, notifier, _settings(), dry_run=True
            )
        notifier.send_text.assert_not_called()
        self.assertFalse(summary["sent"])

    def test_force_sends_even_when_healthy(self):
        report = {"d2b": SiteHealth("d2b", success_count=3, flagged=False)}
        notifier = MagicMock()
        notifier.send_text.return_value = "1.2"
        with self._patch_report(report):
            summary = run_collector_health_check(
                self._fake_factory, notifier, _settings(), force=True
            )
        notifier.send_text.assert_called_once()
        self.assertTrue(summary["sent"])

    def test_disabled_short_circuits(self):
        notifier = MagicMock()
        summary = run_collector_health_check(
            self._fake_factory, notifier, _settings(collector_health_enabled=False)
        )
        notifier.send_text.assert_not_called()
        self.assertFalse(summary["enabled"])


class EvaluateAiHealthTest(unittest.TestCase):
    def test_no_evaluations_is_normal(self):
        self.assertFalse(evaluate_ai_health([], None).flagged)

    def test_all_failed_is_flagged_with_sample_error(self):
        rows = [("failed", "gateway_http_500: 410 status code (no body)")] * 3
        health = evaluate_ai_health(rows, None)
        self.assertTrue(health.flagged)
        self.assertEqual(health.failed_count, 3)
        self.assertIn("410", health.sample_error)

    def test_any_success_clears_the_flag(self):
        rows = [("failed", "x"), ("failed", "x"), ("done", None)]
        self.assertFalse(evaluate_ai_health(rows, None).flagged)

    def test_single_failure_stays_quiet_until_threshold(self):
        self.assertFalse(evaluate_ai_health([("failed", "x")], None).flagged)
        self.assertTrue(evaluate_ai_health([("failed", "x")], None, min_failed=1).flagged)

    def test_alert_text_mentions_outage_and_last_success(self):
        text = format_ai_alert(
            AiHealth(failed_count=4, flagged=True, reasons=["평가 4건 전부 실패(성공 0건)"],
                     sample_error="410"),
            24,
            now=datetime(2026, 10, 15, 0, 0),
        )
        self.assertIn("AI 평가 장애 의심", text)
        self.assertIn("실패 4", text)
        self.assertIn("410", text)


class RunCheckAiPolicyTest(unittest.TestCase):
    @contextmanager
    def _fake_factory(self):
        yield MagicMock()

    def _run(self, ai_health=None, ai_error=False, report=None, **kwargs):
        report = report if report is not None else {
            "d2b": SiteHealth("d2b", success_count=3, flagged=False)
        }
        notifier = MagicMock()
        notifier.send_text.return_value = "1.2"
        ai_patch = (
            patch.object(ch, "build_ai_report", side_effect=RuntimeError("db"))
            if ai_error
            else patch.object(ch, "build_ai_report", return_value=ai_health)
        )
        with patch.object(ch, "build_report", return_value=report), ai_patch:
            summary = run_collector_health_check(
                self._fake_factory, notifier, _settings(), **kwargs
            )
        return summary, notifier

    def test_ai_outage_alerts_even_when_collectors_are_healthy(self):
        ai = AiHealth(failed_count=5, flagged=True, reasons=["평가 5건 전부 실패(성공 0건)"])
        summary, notifier = self._run(ai_health=ai)
        notifier.send_text.assert_called_once()
        message = notifier.send_text.call_args[0][1]
        self.assertIn("AI 평가 장애 의심", message)
        self.assertNotIn("수집기 안정성 경고", message)
        self.assertTrue(summary["sent"])
        self.assertTrue(summary["ai_unhealthy"])

    def test_both_problems_are_sent_in_one_message(self):
        ai = AiHealth(failed_count=5, flagged=True, reasons=["x"])
        report = {"iris": SiteHealth("iris", failed_count=2, flagged=True, reasons=["실패 2건"])}
        summary, notifier = self._run(ai_health=ai, report=report)
        notifier.send_text.assert_called_once()
        message = notifier.send_text.call_args[0][1]
        self.assertIn("수집기 안정성 경고", message)
        self.assertIn("AI 평가 장애 의심", message)

    def test_healthy_ai_sends_nothing(self):
        summary, notifier = self._run(ai_health=AiHealth(done_count=4))
        notifier.send_text.assert_not_called()
        self.assertFalse(summary["ai_unhealthy"])

    def test_ai_check_failure_does_not_block_collector_alert(self):
        report = {"iris": SiteHealth("iris", failed_count=2, flagged=True, reasons=["실패 2건"])}
        summary, notifier = self._run(ai_error=True, report=report)
        notifier.send_text.assert_called_once()
        self.assertIn("수집기 안정성 경고", notifier.send_text.call_args[0][1])
        self.assertFalse(summary["ai_unhealthy"])

    def test_dry_run_with_ai_outage_never_sends(self):
        ai = AiHealth(failed_count=5, flagged=True, reasons=["x"])
        summary, notifier = self._run(ai_health=ai, dry_run=True)
        notifier.send_text.assert_not_called()
        self.assertFalse(summary["sent"])


if __name__ == "__main__":
    unittest.main()
