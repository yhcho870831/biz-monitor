from __future__ import annotations

import tempfile
import unittest
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.bootstrap import create_schema
from app.db import create_db_engine, create_session_factory
from app.models import CalendarSavedNotice, Notice
from app.repositories.attachments import get_attachment, upsert_notice_attachment
from app.repositories.notices import delete_expired_notices
from app.services.attachments import (
    _resolve_g2b_detail_url,
    download_g2b_attachments,
    list_priority_attachment_links,
)
from app.web import create_web_app


class AttachmentFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data") / f"test-attachments-{uuid4().hex}.db"
        self.engine = create_db_engine(f"sqlite:///{self.db_path.as_posix()}")
        create_schema(self.engine)
        self.session_factory = create_session_factory(self.engine)

        with self.session_factory() as session:
            notice = Notice(
                site_code="d2b",
                site_notice_key="d2b-1",
                title="첨부 테스트 공고",
                organization="기관",
                notice_no="N-1",
                reference_no=None,
                start_at=None,
                deadline_at=datetime.utcnow() - timedelta(days=40),
                open_at=None,
                period_text="",
                source_url="https://example.com",
                raw_payload_json="{}",
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(notice)
            session.commit()
            self.notice_id = notice.id

        self.temp_dir = Path(tempfile.gettempdir()) / f"attachment-test-{uuid4().hex}"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.temp_dir / "제안요청서.pdf"
        self.file_path.write_bytes(b"dummy")

        with self.session_factory() as session:
            attachment = upsert_notice_attachment(
                session,
                notice_id=self.notice_id,
                site_code="d2b",
                attachment_name="제안요청서.pdf",
                attachment_category="proposal_request",
                priority_rank=1,
                stored_path=str(self.file_path),
                source_url="https://example.com/file",
                mime_type="application/pdf",
                file_size=self.file_path.stat().st_size,
            )
            self.attachment_id = attachment.id

        app = create_web_app(SimpleNamespace(app_timezone="Asia/Seoul"), self.session_factory)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()
        if self.file_path.exists():
            self.file_path.unlink()
        if self.temp_dir.exists():
            self.temp_dir.rmdir()

    def test_download_endpoint_serves_attachment(self) -> None:
        response = self.client.get(f"/downloads/attachments/{self.attachment_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"dummy")

    def test_expired_notice_cleanup_removes_attachment_file_and_metadata(self) -> None:
        with self.session_factory() as session:
            deleted_count = delete_expired_notices(session, retention_days=28)
            self.assertEqual(deleted_count, 1)

        self.assertFalse(self.file_path.exists())
        with self.session_factory() as session:
            self.assertIsNone(get_attachment(session, self.attachment_id))

    def test_attachment_priority_sorting(self) -> None:
        ranked = list_priority_attachment_links(
            [
                {"href": "/3", "text": "구매요구서.hwp"},
                {"href": "/1", "text": "제안요청서.hwp"},
                {"href": "/2", "text": "공고문.zip"},
            ]
        )
        self.assertEqual(
            [item["category"] for item in ranked],
            ["proposal_request", "notice_document", "purchase_request"],
        )

    def test_notice_without_deadline_is_deleted_after_thirty_days_from_first_seen(self) -> None:
        with self.session_factory() as session:
            notice = Notice(
                site_code="g2b",
                site_notice_key="g2b-nodeadline",
                title="No deadline notice",
                organization="Org",
                notice_no="N-2",
                reference_no=None,
                start_at=None,
                deadline_at=None,
                open_at=None,
                period_text="",
                source_url="https://example.com/no-deadline",
                raw_payload_json="{}",
                first_seen_at=datetime.utcnow() - timedelta(days=31),
                last_seen_at=datetime.utcnow(),
                created_at=datetime.utcnow() - timedelta(days=31),
                updated_at=datetime.utcnow(),
            )
            session.add(notice)
            session.commit()
            notice_id = notice.id

            deleted_count = delete_expired_notices(session)
            self.assertGreaterEqual(deleted_count, 1)
            self.assertIsNone(session.get(Notice, notice_id))

    def test_notice_without_deadline_is_deleted_after_thirty_days_from_raw_posted_at(self) -> None:
        with self.session_factory() as session:
            notice = Notice(
                site_code="nia",
                site_notice_key="nia-old-posted",
                title="Old NIA notice",
                organization="Org",
                notice_no="N-raw",
                reference_no=None,
                start_at=None,
                deadline_at=None,
                open_at=None,
                period_text="\uac8c\uc2dc\uc77c 2021.10.28",
                source_url="https://example.com/nia-old",
                raw_payload_json=json.dumps({"posted_at": "2021.10.28"}),
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(notice)
            session.commit()
            notice_id = notice.id

            deleted_count = delete_expired_notices(session)
            self.assertGreaterEqual(deleted_count, 1)
            self.assertIsNone(session.get(Notice, notice_id))

    def test_existing_nia_notice_without_posted_at_key_uses_raw_title_date(self) -> None:
        with self.session_factory() as session:
            notice = Notice(
                site_code="nia",
                site_notice_key="nia-old-title-date",
                title="Old NIA notice",
                organization="Org",
                notice_no="N-title",
                reference_no=None,
                start_at=None,
                deadline_at=None,
                open_at=None,
                period_text="\uae30\uac04 \ubbf8\uae30\uc7ac",
                source_url="https://example.com/nia-old-title",
                raw_payload_json=json.dumps(
                    {
                        "title": "\uacf5\ub3d9\ud65c\uc6a9 \ub370\uc774\ud130 \ucca8\ubd80\ud30c\uc77c \uc788\uc74c 2021.10.28 \uc870\ud68c\uc218 513",
                    },
                    ensure_ascii=False,
                ),
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(notice)
            session.commit()
            notice_id = notice.id

            deleted_count = delete_expired_notices(session)
            self.assertGreaterEqual(deleted_count, 1)
            self.assertIsNone(session.get(Notice, notice_id))

    def test_checked_calendar_notice_is_preserved_from_cleanup(self) -> None:
        with self.session_factory() as session:
            notice = Notice(
                site_code="g2b",
                site_notice_key="g2b-protected",
                title="Protected notice",
                organization="Org",
                notice_no="N-3",
                reference_no=None,
                start_at=None,
                deadline_at=datetime.utcnow() - timedelta(days=90),
                open_at=None,
                period_text="",
                source_url="https://example.com/protected",
                raw_payload_json="{}",
                first_seen_at=datetime.utcnow() - timedelta(days=90),
                last_seen_at=datetime.utcnow(),
                created_at=datetime.utcnow() - timedelta(days=90),
                updated_at=datetime.utcnow(),
            )
            session.add(notice)
            session.commit()

            saved = CalendarSavedNotice(
                source_notice_id=notice.id,
                site_id=None,
                site_code="g2b",
                site_name="나라장터",
                title=notice.title,
                organization=notice.organization,
                primary_deadline_at=notice.deadline_at,
                amount_text=None,
                amount_value=None,
                priority_score=1,
                notice_tag="research_service",
                source_url=notice.source_url,
                raw_payload_json="{}",
                status="participating",
                owner_name=None,
                selected_at=datetime.utcnow(),
                deselected_at=None,
                updated_at=datetime.utcnow(),
                selected_by="관리자",
                is_active=True,
                memo=None,
                origin_type="notice",
                deadline_confidence="exact",
                legacy_year=None,
                import_batch_id=None,
            )
            session.add(saved)
            session.commit()

            deleted_count = delete_expired_notices(session)
            self.assertGreaterEqual(deleted_count, 1)
            self.assertIsNotNone(session.get(Notice, notice.id))

    def test_resolve_g2b_detail_url_uses_notice_url_or_fallback(self) -> None:
        candidate = SimpleNamespace(
            source_url="https://www.g2b.go.kr/pn/pnp/pnpe/BidPbac/selectBidPbancDetail.do?bidPbancNo=R26BK0001",
            notice_no="R26BK0001",
        )
        self.assertEqual(_resolve_g2b_detail_url(candidate), candidate.source_url)

        fallback_candidate = SimpleNamespace(source_url="", notice_no="R26BK0002")
        self.assertEqual(
            _resolve_g2b_detail_url(fallback_candidate),
            "https://www.g2b.go.kr/pn/pnp/pnpe/BidPbac/selectBidPbancDetail.do?bidPbancNo=R26BK0002",
        )

    def test_download_g2b_attachments_uses_detail_page_directly(self) -> None:
        temp_dir = Path(tempfile.gettempdir()) / f"g2b-attachment-test-{uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        class FakeDownload:
            suggested_filename = "제안요청서.hwp"
            url = "https://www.g2b.go.kr/download/file"

            def save_as(self, target: str) -> None:
                Path(target).write_bytes(b"dummy")

        class FakeDownloadContext:
            def __init__(self, download: FakeDownload) -> None:
                self.value = download

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        class FakeButton:
            def count(self) -> int:
                return 0

            @property
            def first(self):
                return self

            def click(self, force: bool = False) -> None:
                return None

        class FakePage:
            def __init__(self) -> None:
                self.goto_url = None
                self.evaluate_calls: list[str] = []

            def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
                self.goto_url = url

            def wait_for_timeout(self, timeout_ms: int) -> None:
                return None

            def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
                return None

            def wait_for_function(self, script: str, timeout: int | None = None) -> None:
                return None

            def locator(self, selector: str) -> FakeButton:
                return FakeButton()

            def evaluate(self, script: str, arg=None):
                self.evaluate_calls.append(script)
                if "Object.keys(window)" in script:
                    return [
                        {
                            "prefix": "detail",
                            "rows": [{"row_index": 0, "text": "제안요청서.hwp"}],
                        }
                    ]
                return None

            def expect_download(self, timeout: int = 15000) -> FakeDownloadContext:
                return FakeDownloadContext(FakeDownload())

        class FakeBrowserContext:
            def __init__(self) -> None:
                self.page = FakePage()

            def __enter__(self) -> FakePage:
                return self.page

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        candidate = SimpleNamespace(
            site_code="g2b",
            site_notice_key="g2b-1",
            title="테스트 공고",
            source_url="https://www.g2b.go.kr/pn/pnp/pnpe/BidPbac/selectBidPbancDetail.do?bidPbancNo=R26BK0003",
            notice_no="R26BK0003",
            raw_payload={},
            priority_score=1,
        )
        settings = SimpleNamespace(download_dir=str(temp_dir))
        fake_context = FakeBrowserContext()

        try:
            with patch("app.services.attachments.browser_page", return_value=fake_context):
                attachments = download_g2b_attachments(candidate, settings)
        finally:
            for path in sorted(temp_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
            if temp_dir.exists():
                temp_dir.rmdir()

        self.assertEqual(fake_context.page.goto_url, candidate.source_url)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].attachment_name, "제안요청서.hwp")
        self.assertFalse(
            any("dl.set('bidPbancNo'" in script for script in fake_context.page.evaluate_calls)
        )


if __name__ == "__main__":
    unittest.main()
