from __future__ import annotations

import unittest

from app.collectors.kimst import KIMSTCollector


class KIMSTCollectorUrlTest(unittest.TestCase):
    def test_search_converts_querystring_href_to_absolute_url(self) -> None:
        collector = KIMSTCollector()
        collector._fetch_html = lambda _url: """
        <table class="table table-list">
          <tbody>
            <tr>
              <td>2025000001</td>
              <td><a href="?type=view&anucno=2025000001">테스트 공고</a></td>
              <td>2025-01-01</td>
              <td>2025-01-31</td>
            </tr>
          </tbody>
        </table>
        """

        results = collector.search("테스트")

        self.assertEqual(len(results), 1)
        self.assertEqual(
            results[0].source_url,
            "https://www.kimst.re.kr/u/news/inform_01/pjtAnuc.do?type=view&anucno=2025000001",
        )


if __name__ == "__main__":
    unittest.main()
