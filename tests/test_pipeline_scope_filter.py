from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.types import NoticeCandidate


def _make_candidate(title: str, site_code: str = "g2b") -> NoticeCandidate:
    return NoticeCandidate(
        site_code=site_code,
        site_notice_key=title,
        title=title,
        source_url="https://example.com",
        deadline_at=None,
    )


class PipelineScopeFilterTest(unittest.TestCase):
    """수동 검색(pipeline.run_manual_search)이 정기 스케줄러와
    동일한 제외어 필터를 적용하는지 검증한다."""

    def _run_manual_search(self, candidates: list, excluded_scope_keywords: list | None = None):
        from app.services.pipeline import run_manual_search

        settings = SimpleNamespace(
            excluded_scope_keywords=excluded_scope_keywords or [],
            ai_relevance_enabled=False,
            ai_relevance_min_rule_score=1,
            calendar_web_url="http://test",
        )
        notifier = MagicMock()
        notifier.settings = settings
        notifier.send_text.return_value = "ts-1"

        collector = MagicMock()
        collector.search.return_value = candidates

        session = MagicMock()
        session.commit.return_value = None

        registry = {"g2b": collector}

        with patch("app.services.pipeline.create_job") as mock_job, \
             patch("app.services.pipeline.upsert_notice") as mock_upsert, \
             patch("app.services.pipeline.already_shared", return_value=False), \
             patch("app.services.pipeline.record_share"), \
             patch("app.services.pipeline.enrich_notice_candidate"), \
             patch("app.services.pipeline.is_relevant", return_value=(True, "matched")), \
             patch("app.services.pipeline.is_active_notice", return_value=True), \
             patch("app.services.pipeline.should_collect_attachments", return_value=False), \
             patch("app.services.pipeline.generate_notice_summary", return_value=None), \
             patch("app.services.pipeline.get_notice_summary_payload", return_value={}), \
             patch("app.services.pipeline.format_summary_lines", return_value=[]), \
             patch("app.services.pipeline.format_ai_evaluation_lines", return_value=[]):
            mock_job.return_value = MagicMock(id=1)
            mock_upsert.return_value = MagicMock(id=1)
            result = run_manual_search(
                session=session,
                collector_registry=registry,
                notifier=notifier,
                site_code="g2b",
                term="양식",
                channel_id="C123",
                requested_by="U123",
                use_relevance_filter=False,
            )
        return result, notifier

    def test_scope_excluded_title_is_filtered_from_manual_search(self) -> None:
        # 제목에 제외어가 있는 공고는 수동 검색에서도 제외된다.
        excluded = _make_candidate("도시 공원 양식장 관리 용역")
        result, notifier = self._run_manual_search(
            candidates=[excluded],
            excluded_scope_keywords=["공원"],
        )
        self.assertEqual(result["count"], 0)

    def test_scope_clean_title_passes_manual_search(self) -> None:
        # 제목에 제외어가 없는 공고는 수동 검색에서 통과한다.
        clean = _make_candidate("육상양식장 통합관리 시스템 구축")
        result, notifier = self._run_manual_search(
            candidates=[clean],
            excluded_scope_keywords=["공원"],
        )
        self.assertEqual(result["count"], 1)

    def test_scope_filter_in_body_only_does_not_exclude(self) -> None:
        # 제목에 없고 본문에만 제외어가 있으면 통과한다 (제목 한정 매칭).
        candidate = _make_candidate("육상양식장 통합관리 시스템")
        candidate.raw_payload = {"설명": "사업장 인근 공원 통행 안내"}
        result, _ = self._run_manual_search(
            candidates=[candidate],
            excluded_scope_keywords=["공원"],
        )
        self.assertEqual(result["count"], 1)

    def test_scope_filter_applies_before_relevance(self) -> None:
        # 제외어 필터는 is_relevant 호출 전에 동작해야 한다.
        # → is_relevant가 한 번도 호출되지 않으면 제외어 필터가 먼저 막은 것.
        excluded = _make_candidate("도시 공원 환경 정비 용역")
        with patch("app.services.pipeline.is_relevant", return_value=(True, "matched")) as mock_relevance, \
             patch("app.services.pipeline.create_job", return_value=MagicMock(id=1)), \
             patch("app.services.pipeline.upsert_notice", return_value=MagicMock(id=1)), \
             patch("app.services.pipeline.already_shared", return_value=False), \
             patch("app.services.pipeline.record_share"), \
             patch("app.services.pipeline.enrich_notice_candidate"), \
             patch("app.services.pipeline.is_active_notice", return_value=True), \
             patch("app.services.pipeline.should_collect_attachments", return_value=False), \
             patch("app.services.pipeline.generate_notice_summary", return_value=None), \
             patch("app.services.pipeline.get_notice_summary_payload", return_value={}), \
             patch("app.services.pipeline.format_summary_lines", return_value=[]), \
             patch("app.services.pipeline.format_ai_evaluation_lines", return_value=[]):

            from app.services.pipeline import run_manual_search
            settings = SimpleNamespace(
                excluded_scope_keywords=["공원"],
                ai_relevance_enabled=False,
                ai_relevance_min_rule_score=1,
                calendar_web_url="http://test",
            )
            notifier = MagicMock()
            notifier.settings = settings
            notifier.send_text.return_value = "ts-1"
            collector = MagicMock()
            collector.search.return_value = [excluded]
            session = MagicMock()

            run_manual_search(
                session=session,
                collector_registry={"g2b": collector},
                notifier=notifier,
                site_code="g2b",
                term="양식",
                channel_id="C123",
                requested_by="U123",
                use_relevance_filter=False,
            )
            mock_relevance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
