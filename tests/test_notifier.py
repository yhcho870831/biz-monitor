from __future__ import annotations

import unittest
from datetime import datetime

from app.services.notifier import (
    format_empty_scheduled,
    format_no_share_scheduled,
    format_site_notice_tables,
)
from app.types import NoticeCandidate


class FormatNotifierTest(unittest.TestCase):
    def test_format_uses_search_result_zero_message(self) -> None:
        message = format_empty_scheduled(
            datetime(2026, 3, 23, 15, 0, 0),
            [("\ub098\ub77c\uc7a5\ud130", 0), ("KIMST", 0), ("NIA", 0)],
        )

        self.assertIn("3\uc6d4 23\uc77c 15\uc2dc \uae30\uc900 \uac80\uc0c9\uacb0\uacfc 0\uac74\uc785\ub2c8\ub2e4.", message)
        self.assertIn("\ub098\ub77c\uc7a5\ud130 : 0\uac74", message)
        self.assertIn("KIMST : 0\uac74", message)
        self.assertIn("NIA : 0\uac74", message)

    def test_format_uses_no_share_message_when_results_exist_but_none_shared(self) -> None:
        message = format_no_share_scheduled(
            datetime(2026, 3, 23, 15, 0, 0),
            [("\ub098\ub77c\uc7a5\ud130", 120), ("KIMST", 1), ("NIA", 22)],
        )

        self.assertIn("3\uc6d4 23\uc77c 15\uc2dc \uae30\uc900 \uacf5\uc720\ud560 \uacf5\uace0\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.", message)
        self.assertIn("\ub098\ub77c\uc7a5\ud130 : 120\uac74", message)
        self.assertIn("KIMST : 1\uac74", message)
        self.assertIn("NIA : 22\uac74", message)

    def test_format_site_notice_tables(self) -> None:
        messages = format_site_notice_tables(
            "g2b",
            [
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="1",
                    title="\uae30\uc0c1 \uad00\uce21 \uc2dc\uc2a4\ud15c \uc5f0\uad6c\uc6a9\uc5ed",
                    source_url="https://example.com/1",
                    organization="\uae30\uc0c1\uccad",
                    period_text="2026-03-24 18:00",
                    amount_value=150_000_000,
                    notice_tag="research_service",
                    priority_score=3,
                )
            ],
        )

        self.assertEqual(len(messages), 1)
        self.assertIn("*\ub098\ub77c\uc7a5\ud130*", messages[0])
        self.assertIn("\uae30\uc0c1 \uad00\uce21 \uc2dc\uc2a4\ud15c", messages[0])
        self.assertIn("\uc785\ucc30\ub9c8\uac10:", messages[0])
        self.assertIn("\ubc1c\uc8fc\ucc98:", messages[0])
        self.assertIn("\uae08\uc561:", messages[0])

    def test_format_site_notice_tables_uses_attachment_link_for_g2b(self) -> None:
        messages = format_site_notice_tables(
            "g2b",
            [
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="1",
                    title="\uae30\uc0c1 \uad00\uce21 \uc2dc\uc2a4\ud15c \uc5f0\uad6c\uc6a9\uc5ed",
                    source_url="https://example.com/1",
                    organization="\uae30\uc0c1\uccad",
                    period_text="2026-03-24 18:00",
                    amount_value=150_000_000,
                    notice_tag="research_service",
                    priority_score=3,
                )
            ],
            title_link_overrides={"1": "https://intranet/download/1"},
        )

        self.assertIn("<https://intranet/download/1|", messages[0])

    def test_format_site_notice_tables_keeps_g2b_detail_link_without_attachment(self) -> None:
        messages = format_site_notice_tables(
            "g2b",
            [
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="1",
                    title="\uae30\uc0c1 \uad00\uce21 \uc2dc\uc2a4\ud15c \uc5f0\uad6c\uc6a9\uc5ed",
                    source_url="https://example.com/1",
                    organization="\uae30\uc0c1\uccad",
                    period_text="2026-03-24 18:00",
                    notice_tag="research_service",
                    priority_score=3,
                )
            ],
        )

        self.assertIn("<https://example.com/1|", messages[0])

    def test_format_site_notice_tables_labels_procurement_plan(self) -> None:
        messages = format_site_notice_tables(
            "g2b",
            [
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="plan-1",
                    title="\ubc1c\uc8fc\uacc4\ud68d",
                    source_url="https://www.g2b.go.kr/",
                    organization="\uae30\uad00",
                    start_at=datetime(2026, 7, 30, 9, 0),
                    raw_payload={
                        "announcement_stage": "procurement_plan",
                        "oderPlanPgstNm": "\uac8c\uc2dc\uc911",
                    },
                )
            ],
        )

        self.assertIn("[\ubc1c\uc8fc\uacc4\ud68d] \ubc1c\uc8fc\uacc4\ud68d", messages[0])
        self.assertIn("\uac8c\uc2dc\uc77c: 2026-07-30 09:00 | \uc0c1\ud0dc: \uac8c\uc2dc\uc911", messages[0])
        self.assertNotIn("<https://www.g2b.go.kr/|", messages[0])

    def test_format_site_notice_tables_labels_pre_specification(self) -> None:
        messages = format_site_notice_tables(
            "g2b",
            [
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="spec-1",
                    title="\uc0ac\uc804\uaddc\uaca9",
                    source_url="https://example.com/spec-1",
                    deadline_at=datetime(2026, 7, 31, 18, 0),
                    raw_payload={"announcement_stage": "pre_specification"},
                )
            ],
        )

        self.assertIn("[\uc0ac\uc804\uaddc\uaca9] \uc0ac\uc804\uaddc\uaca9", messages[0])
        self.assertIn("\uc758\uacac\ub4f1\ub85d \ub9c8\uac10: 2026-07-31 18:00", messages[0])

    def test_format_site_notice_tables_keeps_direct_link_for_iris(self) -> None:
        messages = format_site_notice_tables(
            "iris",
            [
                NoticeCandidate(
                    site_code="iris",
                    site_notice_key="1",
                    title="IRIS \uacf5\uace0",
                    source_url="https://example.com/iris",
                    organization="IRIS",
                    period_text="2026-03-24 18:00",
                    notice_tag="research_service",
                    priority_score=1,
                )
            ],
        )

        self.assertIn("<https://example.com/iris|", messages[0])

    def test_format_site_notice_tables_sorts_by_priority_desc(self) -> None:
        messages = format_site_notice_tables(
            "g2b",
            [
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="low",
                    title="\ub0ae\uc740 \uc911\uc694\ub3c4 \uacf5\uace0",
                    source_url="https://example.com/low",
                    organization="\uae30\uc0c1\uccad",
                    period_text="2026-03-24 18:00",
                    notice_tag="general_service",
                    priority_score=1,
                ),
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="high",
                    title="\ub192\uc740 \uc911\uc694\ub3c4 \uacf5\uace0",
                    source_url="https://example.com/high",
                    organization="\uae30\uc0c1\uccad",
                    period_text="2026-03-24 18:00",
                    notice_tag="research_service",
                    priority_score=3,
                ),
            ],
        )

        self.assertEqual(len(messages), 1)
        self.assertLess(messages[0].find("\ub192\uc740 \uc911\uc694\ub3c4 \uacf5\uace0"), messages[0].find("\ub0ae\uc740 \uc911\uc694\ub3c4 \uacf5\uace0"))

    def test_format_site_notice_tables_sorts_by_period_when_deadline_missing(self) -> None:
        messages = format_site_notice_tables(
            "g2b",
            [
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="later",
                    title="\ub2a6\uc740 \uc77c\uc815 \uacf5\uace0",
                    source_url="https://example.com/later",
                    organization="\uae30\uc0c1\uccad",
                    period_text="2026/03/11 10:20",
                    notice_tag="general_service",
                    priority_score=0,
                ),
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key="earlier",
                    title="\ube60\ub978 \uc77c\uc815 \uacf5\uace0",
                    source_url="https://example.com/earlier",
                    organization="\uae30\uc0c1\uccad",
                    period_text="2026/03/04 17:47",
                    notice_tag="general_service",
                    priority_score=0,
                ),
            ],
        )

        self.assertLess(messages[0].find("\ube60\ub978 \uc77c\uc815 \uacf5\uace0"), messages[0].find("\ub2a6\uc740 \uc77c\uc815 \uacf5\uace0"))

    def test_format_site_notice_tables_marks_preannouncement(self) -> None:
        messages = format_site_notice_tables(
            "iris",
            [
                NoticeCandidate(
                    site_code="iris",
                    site_notice_key="schedule-1",
                    title="\uae30\uc0c1 \uacf5\ubaa8\uc608\uace0",
                    source_url="https://example.com/pre",
                    organization="IRIS",
                    period_text="2026-04-01 18:00",
                    notice_tag="research_service",
                    priority_score=1,
                    raw_payload={"iris_result_type": "schedule"},
                )
            ],
        )

        self.assertIn("[\uc0ac\uc804\uacf5\uace0] \uae30\uc0c1 \uacf5\ubaa8\uc608\uace0", messages[0])


if __name__ == "__main__":
    unittest.main()
