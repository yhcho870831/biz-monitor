from __future__ import annotations

import unittest
from datetime import datetime

from app.collectors.nia import NIACollector
from app.services.deadline import is_active_notice


class NIACollectorTest(unittest.TestCase):
    def test_search_result_strips_list_metadata_and_keeps_posted_at(self) -> None:
        collector = NIACollector()
        collector._fetch_detail = lambda _url: {
            "title": "",
            "organization": "\uc7ac\ubb34\uad00\ub9ac\ud300",
            "period_text": "\uae30\uac04 \ubbf8\uae30\uc7ac",
            "source_url": _url,
            "body_excerpt": "",
        }

        html = """
        <div class="board_type01">
          <ul>
            <li>
              <a href="#" onclick="doBbsFView('78336','23927','B','23927')">
                [\uc870\ub2ec\uc785\ucc30\uacf5\uace0] \uacf5\ub3d9\ud65c\uc6a9 \ub370\uc774\ud130 \ub4f1\ub85d\uad00\ub9ac \uc2dc\uc2a4\ud15c \ud655\ub300\uad6c\ucd95 \uc704\ud0c1\uac10\ub9ac
                \ucca8\ubd80\ud30c\uc77c \uc788\uc74c 2021.10.28 \uc870\ud68c\uc218 513 \uc190\uc8fc\ud76c \uc7ac\ubb34\uad00\ub9ac\ud300
              </a>
            </li>
          </ul>
        </div>
        """

        results = collector._parse_search_results(html)

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].title,
            "[\uc870\ub2ec\uc785\ucc30\uacf5\uace0] \uacf5\ub3d9\ud65c\uc6a9 \ub370\uc774\ud130 \ub4f1\ub85d\uad00\ub9ac \uc2dc\uc2a4\ud15c \ud655\ub300\uad6c\ucd95 \uc704\ud0c1\uac10\ub9ac",
        )
        self.assertEqual(results[0].raw_payload["posted_at"], "2021.10.28")

    def test_old_nia_posting_without_deadline_is_filtered(self) -> None:
        collector = NIACollector()
        collector._fetch_detail = lambda _url: {
            "title": "",
            "organization": "\uc7ac\ubb34\uad00\ub9ac\ud300",
            "period_text": "\uae30\uac04 \ubbf8\uae30\uc7ac",
            "source_url": _url,
            "body_excerpt": "",
        }
        candidate = collector._parse_search_results(
            """
            <div class="board_type01">
              <ul>
                <li>
                  <a href="#" onclick="doBbsFView('78336','23927','B','23927')">
                    \uacf5\ub3d9\ud65c\uc6a9 \ub370\uc774\ud130 \ub4f1\ub85d\uad00\ub9ac \uc2dc\uc2a4\ud15c
                    \ucca8\ubd80\ud30c\uc77c \uc788\uc74c 2021.10.28 \uc870\ud68c\uc218 513
                  </a>
                </li>
              </ul>
            </div>
            """
        )[0]
        candidate.raw_payload["posted_at"] = "2021.10.28"

        self.assertFalse(is_active_notice(candidate, datetime(2026, 7, 1)))


if __name__ == "__main__":
    unittest.main()
