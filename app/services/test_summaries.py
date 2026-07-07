from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

from app.bootstrap import create_schema
from app.db import create_db_engine, create_session_factory
from app.models import Notice
from app.repositories.attachments import get_attachment, upsert_notice_attachment
from app.services.calendar import get_saved_notice_detail, save_calendar_selection
from app.services.summaries import generate_notice_summary
from app.types import NoticeCandidate


class SummaryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path("data") / f"test-summaries-{uuid4().hex}.db"
        self.engine = create_db_engine(f"sqlite:///{self.db_path.as_posix()}")
        create_schema(self.engine)
        self.session_factory = create_session_factory(self.engine)
        self.output_dir = Path(tempfile.gettempdir()) / f"summary-test-{uuid4().hex}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.settings = SimpleNamespace(download_dir=str(self.output_dir))

    def tearDown(self) -> None:
        self.engine.dispose()
        if self.db_path.exists():
            self.db_path.unlink()
        if self.output_dir.exists():
            for path in sorted(self.output_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()

    def _create_notice(self, **kwargs) -> int:
        defaults = dict(
            site_code="g2b",
            site_notice_key=f"notice-{uuid4().hex}",
            title="AI 수중영상 분석 모델 개발 용역",
            organization="국립수산과학원",
            notice_no="R26BK01413554-000",
            reference_no=None,
            start_at=None,
            deadline_at=datetime(2026, 3, 30, 18, 0, 0),
            open_at=None,
            period_text="2026-03-30 18:00",
            source_url="https://example.com/notice",
            raw_payload_json="{}",
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        defaults.update(kwargs)
        with self.session_factory() as session:
            notice = Notice(**defaults)
            session.add(notice)
            session.commit()
            return notice.id

    def _create_hwpx(self, path: Path, text: str, encoding: str = "utf-16") -> None:
        with ZipFile(path, "w") as archive:
            archive.writestr("Preview/PrvText.txt", text.encode(encoding))

    def test_generate_summary_from_g2b_hwpx_attachment(self) -> None:
        notice_id = self._create_notice()
        attachment_path = self.output_dir / "제안요청서.hwpx"
        self._create_hwpx(
            attachment_path,
            "\n".join(
                [
                    "사업목적",
                    "독도 수중영상 분석을 자동화하는 AI 모델을 개발한다.",
                    "과업 수행 방안",
                    "데이터 전처리, 학습 데이터셋 구축, 모델 학습, 성능 검증을 수행한다.",
                    "성능 평가 및 검증",
                    "어류 15종 식별률 90% 이상을 달성한다.",
                    "과업기간: 계약일로부터 2026년 11월 30일까지",
                ]
            ),
        )

        with self.session_factory() as session:
            upsert_notice_attachment(
                session,
                notice_id=notice_id,
                site_code="g2b",
                attachment_name="제안요청서.hwpx",
                attachment_category="proposal_request",
                priority_rank=1,
                stored_path=str(attachment_path),
                source_url="https://example.com/file",
                mime_type="application/octet-stream",
                file_size=attachment_path.stat().st_size,
            )
            notice = session.get(Notice, notice_id)
            result = generate_notice_summary(
                session,
                self.settings,
                notice,
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key=notice.site_notice_key,
                    title=notice.title,
                    source_url=notice.source_url,
                    organization=notice.organization,
                    period_text=notice.period_text,
                    priority_score=1,
                ),
            )

            self.assertEqual(result["summary_status"], "done")
            detail = get_saved_notice_detail(session, save_calendar_selection(session, notice_id, True, "관리자")["saved_notice_id"])
            self.assertEqual(detail["summary"]["summary_status"], "done")
            self.assertIn("독도 수중영상 분석", detail["summary"]["purpose"])
            self.assertIn("어류 15종 식별률 90% 이상", detail["summary"]["quantitative_targets"])
            attachment = get_attachment(session, detail["summary"]["attachment_id"])
            self.assertTrue(bool(attachment.is_summary_source))

    def test_generate_summary_from_g2b_utf8_hwpx_attachment(self) -> None:
        notice_id = self._create_notice()
        attachment_path = self.output_dir / "utf8-제안요청서.hwpx"
        self._create_hwpx(
            attachment_path,
            "\n".join(
                [
                    "사업목적",
                    "독도 수중영상 분석을 자동화하는 AI 모델을 개발한다.",
                    "과업 수행 방안",
                    "데이터 전처리, 학습 데이터셋 구축, 모델 학습 및 성능 검증을 수행한다.",
                    "성과목표",
                    "어류 15종 식별률 90% 이상",
                    "과업기간: 계약일로부터 2026년 11월 30일까지",
                ]
            ),
            encoding="utf-8",
        )

        with self.session_factory() as session:
            upsert_notice_attachment(
                session,
                notice_id=notice_id,
                site_code="g2b",
                attachment_name="utf8-제안요청서.hwpx",
                attachment_category="proposal_request",
                priority_rank=1,
                stored_path=str(attachment_path),
                source_url="https://example.com/file",
                mime_type="application/octet-stream",
                file_size=attachment_path.stat().st_size,
            )
            notice = session.get(Notice, notice_id)
            result = generate_notice_summary(
                session,
                self.settings,
                notice,
                NoticeCandidate(
                    site_code="g2b",
                    site_notice_key=notice.site_notice_key,
                    title=notice.title,
                    source_url=notice.source_url,
                    organization=notice.organization,
                    period_text=notice.period_text,
                    priority_score=1,
                ),
            )

            self.assertEqual(result["summary_status"], "done")
            self.assertIn("독도 수중영상 분석", result["purpose"])
            self.assertIn("어류 15종 식별률 90% 이상", result["quantitative_targets"])

    def test_generate_summary_from_iris_body(self) -> None:
        notice_id = self._create_notice(
            site_code="iris",
            title="2026년도 AI 기반 해양 데이터 활용 사업 공고",
            source_url="https://example.com/iris",
            deadline_at=None,
            period_text="2026년도 / 공모예고",
        )
        with self.session_factory() as session:
            notice = session.get(Notice, notice_id)
            from unittest.mock import patch

            with patch(
                "app.services.summaries._extract_visible_iris_text",
                return_value="\n".join(
                    [
                        "사업목적 AI 기반 해양 데이터 활용 기술개발 지원",
                        "지원내용 해양 데이터 분석, AI 모델 개발, 실증",
                        "성과목표 정확도 90% 이상",
                        "사업기간 2026년 4월 ~ 2026년 12월",
                    ]
                ),
            ):
                result = generate_notice_summary(
                    session,
                    self.settings,
                    notice,
                    NoticeCandidate(
                        site_code="iris",
                        site_notice_key=notice.site_notice_key,
                        title=notice.title,
                        source_url=notice.source_url,
                        organization=notice.organization,
                        period_text=notice.period_text,
                        raw_payload={"iris_result_type": "business"},
                        priority_score=1,
                    ),
                )

            self.assertEqual(result["summary_status"], "done")
            self.assertIn("해양 데이터 활용", result["purpose"])
            self.assertIn("정확도 90% 이상", result["quantitative_targets"])


if __name__ == "__main__":
    unittest.main()
