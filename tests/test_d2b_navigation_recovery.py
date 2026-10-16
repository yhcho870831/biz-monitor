from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.collectors.d2b import D2BCollector, D2B_RETURN_TIMEOUT_MS


class D2BNavigationRecoveryTest(unittest.TestCase):
    def test_return_uses_domcontentloaded_with_bounded_timeout(self) -> None:
        collector = D2BCollector()
        page = MagicMock()

        collector._return_to_result_page(page, "\ud14c\uc2a4\ud2b8")

        page.go_back.assert_called_once_with(
            wait_until="domcontentloaded",
            timeout=D2B_RETURN_TIMEOUT_MS,
        )
        page.wait_for_timeout.assert_called_once_with(500)

    def test_return_reopens_result_page_once_when_back_navigation_fails(self) -> None:
        collector = D2BCollector()
        page = MagicMock()
        page.go_back.side_effect = RuntimeError("navigation timeout")
        collector._search_page = MagicMock()

        collector._return_to_result_page(page, "\ud14c\uc2a4\ud2b8")

        collector._search_page.assert_called_once_with(page, "\ud14c\uc2a4\ud2b8")
        page.wait_for_timeout.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class D2BNegotiationDetailParsingTest(unittest.TestCase):
    """공개수의 detail pages use different labels than regular bid notices."""

    def _extract(self, html):
        from unittest.mock import MagicMock

        collector = D2BCollector()
        page = MagicMock()
        page.content.return_value = html
        page.url = "https://www.d2b.go.kr/peb/openNego/openNegoPlanView.do"
        collector._capture_detail_screenshot = lambda *_a, **_k: None
        collector._return_to_result_page = lambda *_a, **_k: None
        return collector._extract_detail(page, MagicMock(), "용역")

    def test_negotiation_notice_uses_estimate_deadline_and_work_name(self):
        html = (
            "<p>[공사명] 27년 재난예방공사 설계 용역(1공구)</p>"
            "<table><tr><th>견적서제출마감일시</th><td>2026-10-16 10:00</td>"
            "<th>개찰일시</th><td>2026-10-16 10:30</td></tr></table>"
        )
        detail = self._extract(html)
        self.assertEqual(detail["title"], "27년 재난예방공사 설계 용역(1공구)")
        self.assertEqual(detail["period_text"], "2026-10-16 10:00")
        self.assertEqual(detail["deadline_at"].strftime("%Y-%m-%d %H:%M"), "2026-10-16 10:00")

    def test_regular_bid_notice_unchanged(self):
        html = (
            "<p>[입찰건명] 일반 입찰 건</p>"
            "<table><tr><th>입찰서제출 마감일시</th><td>2026-10-22 16:00</td></tr></table>"
        )
        detail = self._extract(html)
        self.assertEqual(detail["title"], "일반 입찰 건")
        self.assertEqual(detail["period_text"], "2026-10-22 16:00")
