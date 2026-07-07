from __future__ import annotations

import unittest

from app.slack_app import _format_keyword_list


class SlackAppFormattingTest(unittest.TestCase):
    def test_format_keyword_list_groups_sites_and_counts(self) -> None:
        message = _format_keyword_list(
            [
                ("kimst", "아쿠아포닉스"),
                ("g2b", "기상"),
                ("kimst", "RAS"),
                ("g2b", "AI + 기상"),
            ]
        )

        self.assertIn("총 4개", message)
        self.assertIn("*나라장터* (2)", message)
        self.assertIn("*KIMST* (2)", message)
        self.assertIn("• AI + 기상", message)
        self.assertIn("• 기상", message)
        self.assertIn("• RAS", message)
        self.assertIn("• 아쿠아포닉스", message)

    def test_format_keyword_list_empty(self) -> None:
        message = _format_keyword_list([])
        self.assertIn("등록된 검색어가 없습니다.", message)


if __name__ == "__main__":
    unittest.main()
