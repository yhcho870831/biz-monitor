from __future__ import annotations

from functools import lru_cache
from html import escape
from pathlib import Path
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.calendar import build_calendar_router
from app.repositories.attachments import get_attachment

IRIS_SEARCH_URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmListView.do"


def _normalize_organization_name(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").strip()


@lru_cache(maxsize=1)
def _fetch_iris_organization_options() -> dict[str, str]:
    response = requests.get(IRIS_SEARCH_URL, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    select = soup.select_one("select#sorgnId")
    if select is None:
        return {}

    mapping: dict[str, str] = {}
    for option in select.select("option"):
        value = (option.get("value") or "").strip()
        label = " ".join(option.get_text(" ", strip=True).split())
        if not value or not label:
            continue
        mapping[_normalize_organization_name(label)] = value
    return mapping


def _resolve_iris_organization_value(organization: str | None) -> tuple[str | None, str | None]:
    if not organization:
        return None, None

    options = _fetch_iris_organization_options()
    candidates = []
    normalized_full = _normalize_organization_name(organization)
    if normalized_full:
        candidates.append(normalized_full)

    for segment in re.split(r"\s*>\s*", organization):
        normalized_segment = _normalize_organization_name(segment)
        if normalized_segment and normalized_segment not in candidates:
            candidates.append(normalized_segment)

    for candidate in candidates:
        matched = options.get(candidate)
        if matched:
            return matched, candidate

    return None, None


def _coerce_iris_year(raw_year: str | None) -> str:
    if raw_year and re.fullmatch(r"\d{4}", raw_year.strip()):
        return raw_year.strip()
    return ""


def create_web_app(settings, session_factory) -> FastAPI:
    app = FastAPI(title="biz-monitor calendar", version="1.0.0")
    static_dir = Path(__file__).resolve().parent / "web_static"

    app.include_router(build_calendar_router(session_factory, settings))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/calendar")

    @app.get("/calendar", include_in_schema=False)
    def calendar_page():
        return FileResponse(static_dir / "calendar.html")

    @app.get("/downloads/attachments/{attachment_id}", include_in_schema=False)
    def download_attachment(attachment_id: int):
        with session_factory() as session:
            attachment = get_attachment(session, attachment_id)
            if attachment is None:
                raise HTTPException(status_code=404, detail="attachment not found")
            path = Path(attachment.stored_path)
            if not path.exists():
                raise HTTPException(status_code=404, detail="attachment file not found")
            return FileResponse(
                path,
                filename=attachment.attachment_name,
                media_type=attachment.mime_type or "application/octet-stream",
            )

    @app.get("/helpers/iris-search", include_in_schema=False)
    def iris_search_helper(
        title: str,
        year: Optional[str] = None,
        organization: Optional[str] = None,
        source_url: Optional[str] = None,
    ):
        resolved_year = _coerce_iris_year(year)
        try:
            sorgn_id, matched_org = _resolve_iris_organization_value(organization)
        except Exception:
            sorgn_id, matched_org = None, None

        hidden_inputs = [
            f'<input type="hidden" name="bsnsAncmTl" value="{escape(title)}" />',
            f'<input type="hidden" name="ancmTl" value="{escape(title)}" />',
        ]
        if resolved_year:
            hidden_inputs.append(
                f'<input type="hidden" name="bsnsYy" value="{escape(resolved_year)}" />'
            )
        if sorgn_id:
            hidden_inputs.append(
                f'<input type="hidden" name="sorgnId" value="{escape(sorgn_id)}" />'
            )

        note = (
            f"전문기관을 <strong>{escape(matched_org or organization or '')}</strong>로 자동 선택합니다."
            if sorgn_id
            else "전문기관은 자동 선택하지 못했습니다. IRIS 화면에서 직접 선택해 주세요."
        )
        source_link = (
            f'<p><a href="{escape(source_url)}" target="_blank" rel="noreferrer">현재 원문 열기</a></p>'
            if source_url
            else ""
        )

        html = f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>IRIS 검색 도우미</title>
    <style>
      body {{
        margin: 0;
        font-family: "Segoe UI", "Noto Sans KR", sans-serif;
        background: #f4f7fb;
        color: #18212b;
      }}
      .wrap {{
        max-width: 720px;
        margin: 48px auto;
        padding: 24px;
        background: #ffffff;
        border: 1px solid #d9e1ea;
        border-radius: 16px;
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: 28px;
      }}
      .summary {{
        margin: 18px 0;
        padding: 16px;
        background: #f7fafc;
        border-radius: 12px;
        line-height: 1.7;
      }}
      button {{
        border: 1px solid #c7d2de;
        background: #f8fafc;
        border-radius: 10px;
        padding: 12px 16px;
        cursor: pointer;
        font: inherit;
      }}
      .subtle {{
        color: #5f6d7b;
        font-size: 14px;
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <h1>IRIS 검색 도우미</h1>
      <p>사업명, 연도, 전문기관을 가능한 범위에서 미리 채운 뒤 IRIS 검색 화면으로 이동합니다.</p>
      <div class="summary">
        <div><strong>사업명</strong>: {escape(title)}</div>
        <div><strong>사업년도</strong>: {escape(resolved_year or "미지정")}</div>
        <div><strong>전문기관</strong>: {escape(organization or "미지정")}</div>
        <div class="subtle">{note}</div>
      </div>
      <form id="irisSearchHelperForm" method="post" action="{IRIS_SEARCH_URL}">
        {''.join(hidden_inputs)}
        <button type="submit">IRIS에서 검색 실행</button>
      </form>
      <p class="subtle">화면이 자동으로 넘어가지 않으면 버튼을 눌러 주세요.</p>
      {source_link}
    </div>
    <script>
      window.setTimeout(function () {{
        const form = document.getElementById("irisSearchHelperForm");
        if (form) {{
          form.submit();
        }}
      }}, 150);
    </script>
  </body>
</html>"""
        return HTMLResponse(content=html)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    return app
