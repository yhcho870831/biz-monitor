from __future__ import annotations

import unittest
from datetime import datetime

from app.services.deadline import is_active_notice
from app.services.notifier import _display_deadline
from app.types import NoticeCandidate
from app.utils import parse_title_deadline


class TitleDeadlineParseTest(unittest.TestCase):
    def test_contest_title_with_weekday_and_time(self) -> None:
        self.assertEqual(
            parse_title_deadline("「공모전」(~4.24.(금) 18:00)", datetime(2026, 4, 1)),
            datetime(2026, 4, 24, 18, 0),
        )

    def test_slash_date_defaults_to_end_of_day(self) -> None:
        self.assertEqual(
            parse_title_deadline("참여요청(~5/29)", datetime(2026, 5, 19)),
            datetime(2026, 5, 29, 23, 59),
        )

    def test_year_wrap_dec_to_jan(self) -> None:
        self.assertEqual(
            parse_title_deadline("연말공고(~1.10. 18:00)", datetime(2025, 12, 20)),
            datetime(2026, 1, 10, 18, 0),
        )

    def test_no_pattern_returns_none(self) -> None:
        self.assertIsNone(
            parse_title_deadline("마감 정보 없는 제목", datetime(2026, 4, 1))
        )


class DeadlineFilterAndDisplayTest(unittest.TestCase):
    def _kmiti(self, **kw) -> NoticeCandidate:
        base = dict(
            site_code="kmiti",
            site_notice_key="k",
            title="t",
            source_url="https://x",
            period_text="작성일 2026.04.01",
        )
        base.update(kw)
        return NoticeCandidate(**base)

    def test_expired_title_deadline_is_filtered(self) -> None:
        candidate = self._kmiti(deadline_at=datetime(2026, 4, 24, 18, 0))
        self.assertFalse(is_active_notice(candidate, datetime(2026, 6, 10)))

    def test_future_title_deadline_is_active(self) -> None:
        candidate = self._kmiti(deadline_at=datetime(2026, 12, 31, 18, 0))
        self.assertTrue(is_active_notice(candidate, datetime(2026, 6, 10)))

    def test_posting_date_not_shown_as_deadline(self) -> None:
        candidate = self._kmiti(deadline_at=None)
        self.assertEqual(_display_deadline(candidate), "미기재")

    def test_real_period_range_still_used(self) -> None:
        candidate = self._kmiti(period_text="2026.06.01 ~ 2026.06.15", deadline_at=None)
        self.assertEqual(_display_deadline(candidate), "2026-06-15 00:00")

    def test_parsed_deadline_is_displayed(self) -> None:
        candidate = self._kmiti(deadline_at=datetime(2026, 4, 24, 18, 0))
        self.assertEqual(_display_deadline(candidate), "2026-04-24 18:00")


if __name__ == "__main__":
    unittest.main()
