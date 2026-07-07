from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import collector_health as ch
from app.services.collector_health import (
    SiteHealth,
    evaluate_health,
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


if __name__ == "__main__":
    unittest.main()
