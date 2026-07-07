from __future__ import annotations

import unittest

from app.collectors.iris import IRISCollector


class IrisCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.collector = IRISCollector()

    def test_parse_schedule_results_builds_direct_links(self) -> None:
        html = """
        <div class="dbody">
          <ul>
            <li>
              <a onclick="f_ancmPrntcListForm_view('','2026','S050813','','','1','S002257','10013'); return false;">
                미래수요대응기상장비및활용기술개발(R&D)
              </a>
              <div class="meta">한국기상산업기술원 공모예정 1월, 3월</div>
            </li>
          </ul>
        </div>
        """

        results = self.collector._parse_schedule_results(html)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].site_notice_key, "prntc::2026:S050813::1")
        self.assertEqual(results[0].raw_payload["iris_result_type"], "schedule")
        self.assertIn("retrieveAncmPrntcView.do", results[0].source_url)
        self.assertIn("bsnsYy=2026", results[0].source_url)
        self.assertIn("seq=1", results[0].source_url)
        self.assertIn("공모예정월 1월, 3월", results[0].period_text)

    def test_merge_results_deduplicates_title(self) -> None:
        business = self.collector._parse_results(
            """
            <div class="dbody">
              <ul>
                <li>
                  <span class="inst_title">IRIS</span>
                  <span class="period">2026-03-01 ~ 2026-03-31</span>
                  <a onclick="f_bsnsAncmListForm_view('A1','2026','C01','B01','D-3','2026/03/01','2026/03/31')">
                    공통 제목
                  </a>
                </li>
              </ul>
            </div>
            """
        )
        schedule = self.collector._parse_schedule_results(
            """
            <div class="dbody">
              <ul>
                <li>
                  <a onclick="f_ancmPrntcListForm_view('','2026','S050813','','','1','S002257','10013'); return false;">
                    공통 제목
                  </a>
                </li>
              </ul>
            </div>
            """
        )

        merged = self.collector._merge_results(business, schedule)

        self.assertEqual(len(merged), 1)

    def test_normalize_organization_text_strips_index_and_label(self) -> None:
        self.assertEqual(
            self.collector._normalize_organization_text("29 전문기관 : 한국콘텐츠진흥원"),
            "한국콘텐츠진흥원",
        )


if __name__ == "__main__":
    unittest.main()
