from __future__ import annotations

import unittest

from app.services.broad_search_terms import (
    filter_supporting_keyword_matches,
    is_broad_compound_token,
    is_exact_broad_compound_search_term,
)


class BroadSearchTermsTest(unittest.TestCase):
    def test_exact_broad_terms(self) -> None:
        for term in ("유지보수", "유지관리", "통합관리", "양식"):
            self.assertTrue(is_exact_broad_compound_search_term(term))

    def test_compound_search_is_not_exact_broad(self) -> None:
        self.assertFalse(is_exact_broad_compound_search_term("기상 유지보수"))
        self.assertFalse(is_exact_broad_compound_search_term("해양 양식"))

    def test_broad_token_detection(self) -> None:
        # 정확히 일치하는 광역어만 토큰으로 간주한다.
        self.assertTrue(is_broad_compound_token("양식"))
        self.assertTrue(is_broad_compound_token("통합관리"))
        # `양식`을 부분문자열로 포함하는 도메인 키워드는 광역어가 아니다.
        self.assertFalse(is_broad_compound_token("스마트양식"))
        self.assertFalse(is_broad_compound_token("육상양식장"))
        self.assertFalse(is_broad_compound_token("수치예보"))

    def test_filter_supporting_keeps_aquaculture_keywords(self) -> None:
        # 광역어 `양식`만 제거되고 `육상양식`은 supporting으로 살아남아야 한다.
        self.assertEqual(
            filter_supporting_keyword_matches({"육상양식", "양식"}),
            {"육상양식"},
        )
        self.assertEqual(
            filter_supporting_keyword_matches({"양식", "통합관리"}),
            set(),
        )


if __name__ == "__main__":
    unittest.main()
