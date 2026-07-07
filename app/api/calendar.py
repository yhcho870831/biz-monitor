from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.services.calendar import (
    create_manual_calendar_notice,
    deactivate_saved_notice,
    get_calendar_events,
    get_calendar_notice_list,
    get_saved_notice_detail,
    now_in_timezone,
    save_calendar_selection,
    update_saved_notice_fields,
)


class CalendarSelectionPayload(BaseModel):
    notice_id: int
    selected: bool
    selected_by: str = "관리자"


class CalendarSavedNoticePatchPayload(BaseModel):
    status: Optional[str] = None
    owner_name: Optional[str] = None
    memo: Optional[str] = None
    primary_deadline_at: Optional[datetime] = None
    amount_value: Optional[int] = None
    priority_score: Optional[int] = None
    notice_tag: Optional[str] = None
    source_url: Optional[str] = None
    deadline_confidence: Optional[str] = None


class CalendarManualNoticePayload(BaseModel):
    title: str
    organization: Optional[str] = None
    primary_deadline_at: Optional[datetime] = None
    amount_value: Optional[int] = None
    priority_score: int = 0
    notice_tag: Optional[str] = None
    source_url: Optional[str] = None
    status: str = "participating"
    owner_name: Optional[str] = None
    memo: Optional[str] = None
    selected_by: str = "관리자"
    deadline_confidence: str = "exact"


class CalendarSavedNoticeDeactivatePayload(BaseModel):
    selected_by: str = "관리자"


def build_calendar_router(session_factory: Callable, settings) -> APIRouter:
    router = APIRouter(prefix="/api/calendar", tags=["calendar"])

    def get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @router.get("/notices")
    def api_get_calendar_notices(
        site_code: Optional[str] = Query(None),
        selected_only: bool = Query(False),
        q: Optional[str] = Query(None),
        session=Depends(get_session),
    ):
        now = now_in_timezone(settings.app_timezone)
        return get_calendar_notice_list(
            session=session,
            now=now,
            site_code=site_code,
            selected_only=selected_only,
            q=q,
        )

    @router.post("/selections")
    def api_save_calendar_selection(
        payload: CalendarSelectionPayload,
        session=Depends(get_session),
    ):
        try:
            return save_calendar_selection(
                session=session,
                notice_id=payload.notice_id,
                selected=payload.selected,
                selected_by=payload.selected_by,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/manual-notices")
    def api_create_manual_notice(
        payload: CalendarManualNoticePayload,
        session=Depends(get_session),
    ):
        try:
            return create_manual_calendar_notice(
                session=session,
                title=payload.title,
                organization=payload.organization,
                primary_deadline_at=payload.primary_deadline_at,
                amount_value=payload.amount_value,
                priority_score=payload.priority_score,
                notice_tag=payload.notice_tag,
                source_url=payload.source_url,
                status=payload.status,
                owner_name=payload.owner_name,
                memo=payload.memo,
                selected_by=payload.selected_by,
                deadline_confidence=payload.deadline_confidence,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/saved-notices/{saved_notice_id}/deactivate")
    def api_deactivate_saved_notice(
        saved_notice_id: int,
        payload: CalendarSavedNoticeDeactivatePayload,
        session=Depends(get_session),
    ):
        try:
            result = deactivate_saved_notice(
                session=session,
                saved_notice_id=saved_notice_id,
                selected_by=payload.selected_by,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="saved notice not found")
        return result

    @router.get("/events")
    def api_get_calendar_events(
        month: str = Query(...),
        session=Depends(get_session),
    ):
        now = now_in_timezone(settings.app_timezone)
        try:
            return get_calendar_events(session=session, month=month, now=now)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/saved-notices/{saved_notice_id}")
    def api_get_saved_notice_detail(
        saved_notice_id: int,
        session=Depends(get_session),
    ):
        result = get_saved_notice_detail(session=session, saved_notice_id=saved_notice_id)
        if result is None:
            raise HTTPException(status_code=404, detail="saved notice not found")
        return result

    @router.patch("/saved-notices/{saved_notice_id}")
    def api_patch_saved_notice(
        saved_notice_id: int,
        payload: CalendarSavedNoticePatchPayload,
        session=Depends(get_session),
    ):
        try:
            result = update_saved_notice_fields(
                session=session,
                saved_notice_id=saved_notice_id,
                status=payload.status,
                owner_name=payload.owner_name,
                memo=payload.memo,
                primary_deadline_at=payload.primary_deadline_at,
                amount_value=payload.amount_value,
                priority_score=payload.priority_score,
                notice_tag=payload.notice_tag,
                source_url=payload.source_url,
                deadline_confidence=payload.deadline_confidence,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="saved notice not found")
        return result

    return router
