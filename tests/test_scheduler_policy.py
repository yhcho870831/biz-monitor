from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.recruitment_filter import is_recruitment_notice
from app.services.scheduler import (
    _has_additional_site_keyword_match,
    _min_priority_for_site,
)
from app.types import NoticeCandidate

G2B_SITE_KEYWORDS = [
    "스마트양식",
    "육상양식",
    "육상양식장",
    "양식장 자동화",
    "통합관리",
    "양식장 통합관리",
    "어업",
    "기상정보",
    "유지보수",
    "유지관리",
    "양식",
]


class SchedulerPolicyTest(unittest.TestCase):
    def test_g2b_requires_at_least_one_star(self) -> None:
        self.assertEqual(_min_priority_for_site("g2b"), 1)

    def test_broad_aquaculture_search_passes_via_domain_keyword(self) -> None:
        # `양식` 단독 검색이라도 본문에 `육상양식` 등 도메인 키워드가 있으면 통과한다.
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="aqua-1",
            title="육상양식장 통합관리 시스템",
            source_url="https://example.com",
        )
        self.assertTrue(
            _has_additional_site_keyword_match(candidate, G2B_SITE_KEYWORDS, "양식")
        )

    def test_broad_integrated_management_search_passes(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="aqua-2",
            title="양식장 통합관리 시스템 설계",
            source_url="https://example.com",
        )
        self.assertTrue(
            _has_additional_site_keyword_match(
                candidate, G2B_SITE_KEYWORDS, "통합관리"
            )
        )

    def test_broad_search_without_domain_keyword_is_rejected(self) -> None:
        # 광역어 외에 다른 도메인 키워드가 전혀 없으면 통과하지 못한다.
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="aqua-3",
            title="시설물 양식 일반 용역",
            source_url="https://example.com",
        )
        self.assertFalse(
            _has_additional_site_keyword_match(candidate, G2B_SITE_KEYWORDS, "양식")
        )

    def test_other_sites_allow_zero_star(self) -> None:
        self.assertEqual(_min_priority_for_site("kimst"), 0)
        self.assertEqual(_min_priority_for_site("nia"), 0)

    def test_recruitment_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="nia",
            site_notice_key="1",
            title="2026년 데이터사업 인턴 채용 공고",
            source_url="https://example.com",
        )

        self.assertTrue(is_recruitment_notice(candidate))

    def test_talent_fostering_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="nia",
            site_notice_key="3",
            title="2026년 AI 인재양성 지원사업 공고",
            source_url="https://example.com",
        )

        self.assertTrue(is_recruitment_notice(candidate))

    def test_non_recruitment_notice_is_not_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="nia",
            site_notice_key="2",
            title="2026년 데이터 바우처 지원사업 공고",
            source_url="https://example.com",
        )

        self.assertFalse(is_recruitment_notice(candidate))

    def test_gate2_uses_db_keywords_not_env(self) -> None:
        # 2차 게이트는 DB의 활성 검색어를 사용한다.
        # env(settings)와 DB가 다를 때 DB 기준으로 판정해야 한다.
        # list_enabled_site_keywords를 모킹해서 특정 키워드만 있는 상황을 만든다.
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="db-gate-1",
            title="육상양식장 스마트팜 통합관리 시스템",
            source_url="https://example.com",
        )
        db_keywords = [("g2b", "스마트팜"), ("g2b", "육상양식장")]
        env_keywords = ["기상정보"]  # env에는 없음

        # DB 기반 키워드로는 통과해야 한다.
        with patch(
            "app.services.scheduler.list_enabled_site_keywords",
            return_value=db_keywords,
        ):
            from app.services.scheduler import _has_additional_site_keyword_match
            site_kws = [kw for sc, kw in db_keywords if sc == "g2b"]
            self.assertTrue(_has_additional_site_keyword_match(candidate, site_kws, "양식"))

        # env 기반 키워드만으로는 통과하지 못해야 한다.
        self.assertFalse(_has_additional_site_keyword_match(candidate, env_keywords, "양식"))


if __name__ == "__main__":
    unittest.main()
