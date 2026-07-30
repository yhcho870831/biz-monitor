from __future__ import annotations

import unittest
from datetime import datetime

from app.services.deadline import is_active_notice
from app.types import NoticeCandidate
from app.utils import parse_datetime


class G2BLifecycleFilterTest(unittest.TestCase):
    def _g2b(self, **overrides) -> NoticeCandidate:
        base = dict(
            site_code="g2b",
            site_notice_key="g2b-test",
            title="나라장터 테스트 공고",
            source_url="https://www.g2b.go.kr/",
        )
        base.update(overrides)
        return NoticeCandidate(**base)

    def test_compact_g2b_posting_date_is_parsed(self) -> None:
        self.assertEqual(parse_datetime("20260115"), datetime(2026, 1, 15))

    def test_closed_pre_announcement_is_filtered(self) -> None:
        candidate = self._g2b(
            period_text="20250115",
            raw_payload={
                "announcement_stage": "pre_announcement",
                "oderPlanPgstNm": "마감",
                "prcsYmd": "20250115",
            },
        )
        self.assertFalse(is_active_notice(candidate, datetime(2026, 7, 30)))

    def test_current_pre_announcement_is_kept(self) -> None:
        candidate = self._g2b(
            period_text="20260729",
            raw_payload={
                "announcement_stage": "pre_announcement",
                "oderPlanPgstNm": "게시중",
                "prcsYmd": "20260729",
            },
        )
        self.assertTrue(is_active_notice(candidate, datetime(2026, 7, 30)))

    def test_current_procurement_plan_is_kept(self) -> None:
        candidate = self._g2b(
            period_text="20260729",
            raw_payload={
                "announcement_stage": "procurement_plan",
                "oderPlanPgstNm": "\uac8c\uc2dc\uc911",
                "prcsYmd": "20260729",
            },
        )
        self.assertTrue(is_active_notice(candidate, datetime(2026, 7, 30)))

    def test_stale_pre_announcement_is_filtered_even_when_still_marked_open(self) -> None:
        candidate = self._g2b(
            period_text="20260115",
            raw_payload={
                "announcement_stage": "pre_announcement",
                "oderPlanPgstNm": "게시중",
                "prcsYmd": "20260115",
            },
        )
        self.assertFalse(is_active_notice(candidate, datetime(2026, 7, 30)))

    def test_bid_without_deadline_is_filtered(self) -> None:
        self.assertFalse(is_active_notice(self._g2b(), datetime(2026, 7, 30)))

    def test_open_bid_with_future_deadline_is_kept(self) -> None:
        candidate = self._g2b(deadline_at=datetime(2026, 8, 1, 18, 0))
        self.assertTrue(is_active_notice(candidate, datetime(2026, 7, 30)))


if __name__ == "__main__":
    unittest.main()
