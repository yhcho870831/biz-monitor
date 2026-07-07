from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.bootstrap import create_schema
from app.db import create_db_engine, create_session_factory
from app.models import CompanyProject, Notice
from app.repositories.calendar_saved_notices import get_saved_notice_by_source_notice_id
from app.services.calendar import (
    cleanup_inactive_saved_notices,
    create_manual_calendar_notice,
    get_calendar_events,
    get_calendar_notice_list,
    get_saved_notice_detail,
    save_calendar_selection,
    update_saved_notice_fields,
)


class CalendarServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_db_engine("sqlite:///:memory:")
        create_schema(engine)
        self.session_factory = create_session_factory(engine)

        with self.session_factory() as session:
            session.add_all(
                [
                    CompanyProject(
                        project_name="Historic weather award",
                        organization="KMA",
                        category=None,
                        keywords_json="[]",
                        enabled=True,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    ),
                    Notice(
                        site_code="g2b",
                        site_notice_key="g2b-1",
                        title="Weather model research",
                        organization="KMA",
                        notice_no=None,
                        reference_no=None,
                        start_at=None,
                        deadline_at=datetime(2026, 3, 28, 18, 0, 0),
                        open_at=None,
                        period_text="2026-03-28 18:00",
                        source_url="https://example.com/g2b-1",
                        raw_payload_json="{}",
                        first_seen_at=datetime.utcnow(),
                        last_seen_at=datetime.utcnow(),
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    ),
                    Notice(
                        site_code="kimst",
                        site_notice_key="kimst-1",
                        title="Aquafarm sensors purchase",
                        organization="KIMST",
                        notice_no=None,
                        reference_no=None,
                        start_at=None,
                        deadline_at=datetime(2026, 4, 2, 11, 0, 0),
                        open_at=None,
                        period_text="2026-04-02 11:00",
                        source_url="https://example.com/kimst-1",
                        raw_payload_json="{}",
                        first_seen_at=datetime.utcnow(),
                        last_seen_at=datetime.utcnow(),
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    ),
                ]
            )
            session.commit()

    def test_save_selection_creates_saved_notice(self) -> None:
        with self.session_factory() as session:
            result = save_calendar_selection(session, 1, True, "admin")
            self.assertTrue(result["selected"])
            saved = get_saved_notice_by_source_notice_id(session, 1)
            self.assertIsNotNone(saved)
            self.assertTrue(saved.is_active)
            self.assertEqual(saved.selected_by, "admin")
            self.assertEqual(saved.origin_type, "notice")

    def test_save_selection_reactivates_existing_row(self) -> None:
        with self.session_factory() as session:
            save_calendar_selection(session, 1, True, "admin")
            save_calendar_selection(session, 1, False, "admin")
            save_calendar_selection(session, 1, True, "manager")

            saved = get_saved_notice_by_source_notice_id(session, 1)
            self.assertIsNotNone(saved)
            self.assertTrue(saved.is_active)
            self.assertEqual(saved.status, "participating")
            self.assertIsNone(saved.deselected_at)
            self.assertEqual(saved.selected_by, "manager")

    def test_notice_list_selected_only_filter(self) -> None:
        now = datetime(2026, 3, 24, 9, 0, 0)
        with self.session_factory() as session:
            save_calendar_selection(session, 1, True, "admin")
            result = get_calendar_notice_list(session, now, selected_only=True)
            self.assertEqual(len(result["sites"]), 1)
            self.assertEqual(result["sites"][0]["site_code"], "g2b")
            self.assertEqual(len(result["sites"][0]["items"]), 1)

    def test_selected_notice_is_sorted_to_top_of_site_group(self) -> None:
        now = datetime(2026, 3, 24, 9, 0, 0)
        with self.session_factory() as session:
            save_calendar_selection(session, 1, True, "tester")
            result = get_calendar_notice_list(session, now)
            g2b_site = next(site for site in result["sites"] if site["site_code"] == "g2b")
            self.assertTrue(g2b_site["items"][0]["selected"])

    def test_calendar_events_returns_selected_notice(self) -> None:
        now = datetime(2026, 3, 24, 9, 0, 0)
        with self.session_factory() as session:
            save_calendar_selection(session, 1, True, "admin")
            saved = get_saved_notice_by_source_notice_id(session, 1)
            saved.priority_score = 1
            session.commit()

            result = get_calendar_events(session, "2026-03", now)
            self.assertEqual(len(result["events"]), 1)
            self.assertEqual(result["events"][0]["source_notice_id"], 1)
            self.assertEqual(result["events"][0]["status_label"], "참여 중")

    def test_zero_priority_saved_notice_is_hidden_from_calendar(self) -> None:
        now = datetime(2026, 3, 24, 9, 0, 0)
        with self.session_factory() as session:
            result = save_calendar_selection(session, 1, True, "admin")
            update_saved_notice_fields(session, result["saved_notice_id"], priority_score=0)
            calendar_events = get_calendar_events(session, "2026-03", now)
            self.assertEqual(calendar_events["events"], [])

    def test_zero_priority_manual_saved_notice_is_hidden_from_saved_list(self) -> None:
        now = datetime(2026, 3, 24, 9, 0, 0)
        with self.session_factory() as session:
            create_manual_calendar_notice(
                session=session,
                title="Manual zero priority project",
                organization="Manual Org",
                primary_deadline_at=datetime(2026, 5, 20, 18, 0, 0),
                amount_value=50_000_000,
                priority_score=0,
                notice_tag="general_service",
                source_url="https://example.com/manual-zero",
                status="participating",
                owner_name="manager",
                memo="memo",
                selected_by="admin",
                deadline_confidence="exact",
            )
            result = get_calendar_notice_list(session, now)
            self.assertEqual(result["saved_sites"], [])

    def test_selection_without_deadline_uses_posted_date_plus_14_days(self) -> None:
        with self.session_factory() as session:
            notice = Notice(
                site_code="nia",
                site_notice_key="nia-1",
                title="NIA archived notice",
                organization="NIA",
                notice_no=None,
                reference_no=None,
                start_at=datetime(2026, 3, 10, 9, 0, 0),
                deadline_at=None,
                open_at=None,
                period_text=None,
                source_url="https://example.com/nia-1",
                raw_payload_json="{}",
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(notice)
            session.commit()
            session.refresh(notice)

            result = save_calendar_selection(session, notice.id, True, "admin")
            detail = get_saved_notice_detail(session, result["saved_notice_id"])
            self.assertEqual(detail["primary_deadline_at"], "2026-03-24T09:00:00")
            self.assertEqual(detail["deadline_confidence"], "unknown")
            self.assertEqual(detail["deadline_confidence_label"], "확인필요")

    def test_selected_notice_without_deadline_appears_in_notice_list(self) -> None:
        now = datetime(2026, 3, 24, 12, 0, 0)
        with self.session_factory() as session:
            notice = Notice(
                site_code="nia",
                site_notice_key="nia-2",
                title="NIA selected without deadline",
                organization="KMA",
                notice_no=None,
                reference_no=None,
                start_at=datetime(2026, 3, 12, 9, 0, 0),
                deadline_at=None,
                open_at=None,
                period_text=None,
                source_url="https://example.com/nia-2",
                raw_payload_json="{}",
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(notice)
            session.commit()
            session.refresh(notice)

            save_calendar_selection(session, notice.id, True, "admin")
            result = get_calendar_notice_list(session, now)
            nia_site = next(site for site in result["sites"] if site["site_code"] == "nia")
            self.assertEqual(len(nia_site["items"]), 1)
            self.assertTrue(nia_site["items"][0]["selected"])

    def test_notice_without_real_posted_date_is_hidden_from_notice_list(self) -> None:
        now = datetime(2026, 3, 24, 12, 0, 0)
        with self.session_factory() as session:
            notice = Notice(
                site_code="g2b",
                site_notice_key="g2b-undated",
                title="Historic project resurfaced",
                organization="Historic Org",
                notice_no=None,
                reference_no=None,
                start_at=None,
                deadline_at=None,
                open_at=None,
                period_text=None,
                source_url="https://example.com/g2b-undated",
                raw_payload_json="{}",
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(notice)
            session.commit()

            result = get_calendar_notice_list(session, now)
            all_titles = [
                item["title"]
                for site in result["sites"]
                for item in site["items"]
            ]
            self.assertNotIn("Historic project resurfaced", all_titles)

    def test_notice_with_same_day_past_deadline_is_hidden(self) -> None:
        now = datetime(2026, 3, 24, 12, 0, 0)
        with self.session_factory() as session:
            notice = Notice(
                site_code="g2b",
                site_notice_key="g2b-same-day-past",
                title="Past earlier today",
                organization="Historic Org",
                notice_no=None,
                reference_no=None,
                start_at=datetime(2026, 3, 20, 9, 0, 0),
                deadline_at=datetime(2026, 3, 24, 9, 0, 0),
                open_at=None,
                period_text="2026-03-20 09:00 ~ 2026-03-24 09:00",
                source_url="https://example.com/g2b-same-day-past",
                raw_payload_json="{}",
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(notice)
            session.commit()

            result = get_calendar_notice_list(session, now)
            all_titles = [
                item["title"]
                for site in result["sites"]
                for item in site["items"]
            ]
            self.assertNotIn("Past earlier today", all_titles)

    def test_manual_notice_without_deadline_is_visible_in_saved_list(self) -> None:
        now = datetime(2026, 3, 24, 9, 0, 0)
        with self.session_factory() as session:
            create_manual_calendar_notice(
                session=session,
                title="Manual project without deadline",
                organization="Manual Org",
                primary_deadline_at=None,
                amount_value=None,
                priority_score=1,
                notice_tag="general_service",
                source_url="",
                status="participating",
                owner_name=None,
                memo=None,
                selected_by="admin",
                deadline_confidence="unknown",
            )
            result = get_calendar_notice_list(session, now)
            self.assertEqual(len(result["saved_sites"]), 1)
            self.assertIsNone(result["saved_sites"][0]["items"][0]["primary_deadline_at"])

    def test_closed_manual_notice_is_hidden_from_saved_list(self) -> None:
        now = datetime(2026, 3, 24, 9, 0, 0)
        with self.session_factory() as session:
            create_manual_calendar_notice(
                session=session,
                title="Closed historic project",
                organization="Manual Org",
                primary_deadline_at=datetime(2026, 3, 20, 18, 0, 0),
                amount_value=None,
                priority_score=2,
                notice_tag="general_service",
                source_url="",
                status="closed",
                owner_name=None,
                memo=None,
                selected_by="admin",
                deadline_confidence="exact",
            )
            result = get_calendar_notice_list(session, now)
            self.assertEqual(result["saved_sites"], [])

    def test_closed_saved_selection_is_not_marked_selected(self) -> None:
        now = datetime(2026, 3, 24, 9, 0, 0)
        with self.session_factory() as session:
            result = save_calendar_selection(session, 1, True, "admin")
            update_saved_notice_fields(session, result["saved_notice_id"], status="closed")

            payload = get_calendar_notice_list(session, now)
            g2b_site = next(site for site in payload["sites"] if site["site_code"] == "g2b")
            self.assertFalse(g2b_site["items"][0]["selected"])

    def test_create_manual_notice_without_source_url(self) -> None:
        with self.session_factory() as session:
            detail = create_manual_calendar_notice(
                session=session,
                title="Manual project",
                organization="Manual Org",
                primary_deadline_at=datetime(2026, 5, 20, 18, 0, 0),
                amount_value=120_000_000,
                priority_score=3,
                notice_tag="research_service",
                source_url="",
                status="participating",
                owner_name="manager",
                memo="memo",
                selected_by="admin",
                deadline_confidence="exact",
            )
            self.assertEqual(detail["origin_type"], "manual")
            self.assertEqual(detail["site_code"], "manual")
            self.assertEqual(detail["source_url"], "")
            self.assertEqual(detail["amount_text"], "120,000,000원")
            self.assertTrue(detail["is_active"])

    def test_cleanup_inactive_saved_notice_after_three_days(self) -> None:
        with self.session_factory() as session:
            result = save_calendar_selection(session, 1, True, "admin")
            save_calendar_selection(session, 1, False, "admin")
            saved = get_saved_notice_by_source_notice_id(session, 1)
            saved.primary_deadline_at = datetime.utcnow() - timedelta(days=10)
            saved.deselected_at = datetime.utcnow() - timedelta(days=4)
            session.commit()

            count = cleanup_inactive_saved_notices(session, datetime.utcnow())
            self.assertEqual(count, 1)
            detail = get_saved_notice_detail(session, result["saved_notice_id"])
            self.assertIsNone(detail)


if __name__ == "__main__":
    unittest.main()
