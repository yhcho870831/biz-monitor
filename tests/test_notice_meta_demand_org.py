from __future__ import annotations

import unittest
from datetime import datetime

from app.bootstrap import create_schema
from app.db import create_db_engine, create_session_factory
from app.models import CompanyProject
from app.services.notice_meta import enrich_notice_candidate
from app.types import NoticeCandidate


class NoticeMetaDemandOrganizationTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        create_schema(engine)
        self.session_factory = create_session_factory(engine)
        with self.session_factory() as session:
            session.add(
                CompanyProject(
                    project_name="기상 AI 모델 개발",
                    organization="기상청",
                    category="2026",
                    keywords_json='["기상", "AI", "연구용역"]',
                    enabled=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            session.commit()

    def test_g2b_uses_demand_organization_for_priority_score(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="R26BK01439388-000",
            title="한국형 AI 기상 기후 파운데이션 모델 개발 용역",
            source_url="https://www.g2b.go.kr/example",
            organization="조달청",
            raw_payload={
                "공고기관": "조달청",
                "수요기관": "기상청",
            },
        )

        with self.session_factory() as session:
            enrich_notice_candidate(session, candidate)

        self.assertEqual(candidate.organization, "기상청")
        self.assertGreaterEqual(candidate.priority_score, 1)


if __name__ == "__main__":
    unittest.main()
