from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap import create_schema
from app.db import create_db_engine, create_session_factory
from app.models import Notice
from app.web import create_web_app


class CalendarApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data") / f"test-calendar-api-{uuid4().hex}.db"
        self.engine = create_db_engine(f"sqlite:///{self.db_path.as_posix()}")
        create_schema(self.engine)
        self.session_factory = create_session_factory(self.engine)
        future_deadline = datetime.utcnow() + timedelta(days=14)
        future_start = future_deadline - timedelta(days=7)

        with self.session_factory() as session:
            session.add(
                Notice(
                    site_code="g2b",
                    site_notice_key="g2b-1",
                    title="기상 예측 모델 고도화 연구용역",
                    organization="기상청",
                    notice_no=None,
                    reference_no=None,
                    start_at=future_start,
                    deadline_at=future_deadline,
                    open_at=None,
                    period_text=(
                        f"{future_start.strftime('%Y-%m-%d %H:%M')} ~ "
                        f"{future_deadline.strftime('%Y-%m-%d %H:%M')}"
                    ),
                    source_url="https://example.com/g2b-1",
                    raw_payload_json=json.dumps(
                        {
                            "description": "연구용역",
                            "amount": "150,000,000원",
                        },
                        ensure_ascii=False,
                    ),
                    first_seen_at=datetime.utcnow(),
                    last_seen_at=datetime.utcnow(),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            session.commit()

        app = create_web_app(SimpleNamespace(app_timezone="Asia/Seoul"), self.session_factory)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()

    def test_notices_endpoint(self) -> None:
        response = self.client.get("/api/calendar/notices")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("sites", payload)
        self.assertGreaterEqual(len(payload["sites"]), 1)
        self.assertEqual(payload["sites"][0]["site_code"], "g2b")
        self.assertEqual(payload["sites"][0]["items"][0]["priority_score"], 2)

    def test_selection_endpoint(self) -> None:
        response = self.client.post(
            "/api/calendar/selections",
            json={"notice_id": 1, "selected": True, "selected_by": "테스터"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["selected"])
        self.assertTrue(payload["is_active"])

    def test_manual_notice_endpoint_accepts_single_amount_field(self) -> None:
        response = self.client.post(
            "/api/calendar/manual-notices",
            json={
                "title": "직접 등록 사업",
                "organization": "한국해양과학기술원",
                "primary_deadline_at": "2026-05-20T18:00:00",
                "amount_value": 120000000,
                "priority_score": 3,
                "notice_tag": "research_service",
                "source_url": "https://example.com/manual",
                "status": "participating",
                "owner_name": "김과장",
                "memo": "테스트",
                "selected_by": "테스터",
                "deadline_confidence": "exact",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["origin_type"], "manual")
        self.assertEqual(payload["site_code"], "manual")
        self.assertEqual(payload["amount_text"], "120,000,000원")

    def test_manual_notice_endpoint_allows_blank_source_url(self) -> None:
        response = self.client.post(
            "/api/calendar/manual-notices",
            json={"title": "링크 없는 직접 등록 사업", "selected_by": "관리자"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["origin_type"], "manual")
        self.assertEqual(payload["source_url"], "")

    def test_manual_notice_defaults_selected_by_to_admin(self) -> None:
        response = self.client.post(
            "/api/calendar/manual-notices",
            json={"title": "직접 등록 기본 선택자 테스트"},
        )
        self.assertEqual(response.status_code, 200)

        saved_notice_id = response.json()["id"]
        detail_response = self.client.get(f"/api/calendar/saved-notices/{saved_notice_id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["selected_by"], "관리자")

    def test_iris_search_helper_renders_prefilled_form(self) -> None:
        with patch(
            "app.web._fetch_iris_organization_options",
            return_value={"한국연구재단": "10001"},
        ):
            response = self.client.get(
                "/helpers/iris-search",
                params={
                    "title": "해양기상 데이터 분석",
                    "year": "2026",
                    "organization": "과학기술정보통신부 > 한국연구재단",
                    "source_url": "https://example.com/iris",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('name="bsnsAncmTl" value="해양기상 데이터 분석"', response.text)
        self.assertIn('name="bsnsYy" value="2026"', response.text)
        self.assertIn('name="sorgnId" value="10001"', response.text)
        self.assertIn("IRIS에서 검색 실행", response.text)

    def test_patch_saved_notice_rejects_invalid_status(self) -> None:
        create_response = self.client.post(
            "/api/calendar/selections",
            json={"notice_id": 1, "selected": True, "selected_by": "테스터"},
        )
        saved_notice_id = create_response.json()["saved_notice_id"]
        response = self.client.patch(
            f"/api/calendar/saved-notices/{saved_notice_id}",
            json={"status": "bad-status"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
