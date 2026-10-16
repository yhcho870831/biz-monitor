from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collectors import http_retry
from app.models import Base, Job
from app.services.collector_health import load_window_rows

DNS_MSG = (
    "HTTPSConnectionPool(host='www.nia.or.kr', port=443): Max retries exceeded "
    "(Caused by NameResolutionError(\"Failed to resolve 'www.nia.or.kr' "
    "([Errno -5] No address associated with hostname)\"))"
)


class DnsRetryTest(unittest.TestCase):
    def test_retries_dns_then_succeeds(self):
        ok = MagicMock()
        with patch.object(requests, "get", side_effect=[requests.ConnectionError(DNS_MSG), ok]) as g, \
                patch.object(http_retry.time, "sleep") as sleep:
            self.assertIs(http_retry.request_with_dns_retry("get", "http://x"), ok)
        self.assertEqual(g.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_gives_up_after_all_dns_retries(self):
        err = requests.ConnectionError(DNS_MSG)
        with patch.object(requests, "get", side_effect=err) as g, \
                patch.object(http_retry.time, "sleep"):
            with self.assertRaises(requests.ConnectionError):
                http_retry.request_with_dns_retry("get", "http://x")
        self.assertEqual(g.call_count, 3)

    def test_non_dns_error_not_retried(self):
        with patch.object(requests, "get", side_effect=requests.ConnectionError("Connection refused")) as g, \
                patch.object(http_retry.time, "sleep") as sleep:
            with self.assertRaises(requests.ConnectionError):
                http_retry.request_with_dns_retry("get", "http://x")
        self.assertEqual(g.call_count, 1)
        sleep.assert_not_called()

    def test_timeout_not_retried(self):
        with patch.object(requests, "get", side_effect=requests.Timeout("slow")) as g:
            with self.assertRaises(requests.Timeout):
                http_retry.request_with_dns_retry("get", "http://x")
        self.assertEqual(g.call_count, 1)


class HealthRecoveryTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.session = Session(engine)
        self.now = datetime(2026, 10, 16, 0, 0)

    def _job(self, **kw):
        base = dict(
            job_type="scheduled", site_code="nia", search_term="ai", keyword="ai",
            status="success", attempt=0, run_after=self.now, created_at=self.now - timedelta(hours=2),
            finished_at=self.now - timedelta(hours=1),
        )
        base.update(kw)
        self.session.add(Job(**base))
        self.session.commit()

    def _rows(self):
        return load_window_rows(self.session, self.now - timedelta(hours=24), ["nia"])

    def test_failure_recovered_by_retry_is_dropped(self):
        self._job(status="failed", error_message="dns")
        self._job(job_type="retry", attempt=1, created_at=self.now - timedelta(hours=1, minutes=50))
        self.assertEqual([r[1] for r in self._rows()], ["success"])

    def test_failure_without_successful_retry_is_kept(self):
        self._job(status="failed", error_message="dns")
        self._job(job_type="retry", attempt=1, status="failed", error_message="dns again",
                  created_at=self.now - timedelta(hours=1, minutes=50))
        self.assertEqual(sorted(r[1] for r in self._rows()), ["failed", "failed"])


if __name__ == "__main__":
    unittest.main()
