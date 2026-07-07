from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.collectors.playwright_utils import browser_page
from app.types import NoticeCandidate
from app.utils import make_period_text, normalize_text, parse_datetime


class D2BCollector(BaseCollector):
    site_code = "d2b"
    list_url = "https://www.d2b.go.kr/psb/bid/serviceBidAnnounceList.do?key=137"

    def search(self, term: str) -> List[NoticeCandidate]:
        results: List[NoticeCandidate] = []

        with browser_page() as page:
            self._search_page(page, term)
            rows = self._result_rows(page)
            row_count = rows.count()

            for index in range(row_count):
                self._search_page(page, term)
                row = self._result_rows(page).nth(index)
                link = row.locator("a[href='#none;']").first
                title = normalize_text(link.inner_text())
                if normalize_text(term) and normalize_text(term) not in title:
                    continue

                cells = row.locator("td")
                cell_values = [
                    normalize_text(cells.nth(i).inner_text()) for i in range(cells.count())
                ]
                notice_no = cell_values[3].split(" ")[0] if len(cell_values) > 3 else ""
                reference_no = cell_values[3].split(" ")[-1] if len(cell_values) > 3 else ""
                organization = cell_values[5] if len(cell_values) > 5 else ""
                register_deadline = parse_datetime(cell_values[6]) if len(cell_values) > 6 else None
                bid_deadline = parse_datetime(cell_values[7]) if len(cell_values) > 7 else None

                detail = self._extract_detail(page, link)
                detail_title = detail.get("title") or title
                detail_notice_no = detail.get("notice_no") or notice_no
                detail_reference_no = detail.get("reference_no") or reference_no
                detail_org = detail.get("organization") or organization
                detail_deadline = detail.get("deadline_at") or register_deadline or bid_deadline

                results.append(
                    NoticeCandidate(
                        site_code=self.site_code,
                        site_notice_key=detail_notice_no or detail_reference_no or detail_title,
                        title=detail_title,
                        source_url=detail.get("source_url") or page.url,
                        organization=detail_org,
                        notice_no=detail_notice_no,
                        reference_no=detail_reference_no,
                        deadline_at=detail_deadline,
                        open_at=detail.get("open_at"),
                        period_text=detail.get("period_text") or make_period_text(None, detail_deadline),
                        raw_payload=detail,
                    )
                )

        return results

    def _search_page(self, page, term: str) -> None:
        page.goto(self.list_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.locator("#chgDate3").click()
        page.locator("#anmt_name").fill(normalize_text(term))
        page.locator("#btn_search").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)

    def _result_rows(self, page):
        return page.locator("#SBHE_DATAGRID_WHOLE_TABLE_datagrid1 tr").filter(
            has=page.locator("a[href='#none;']")
        )

    def _extract_detail(self, page, link) -> dict:
        link.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        soup = BeautifulSoup(page.content(), "html.parser")
        title = ""
        for paragraph in soup.select("p"):
            text = normalize_text(paragraph.get_text(" ", strip=True))
            if text.startswith("[입찰건명]"):
                title = text.replace("[입찰건명]", "", 1).strip()
                break
        if not title:
            title = self._field_value(soup, "입찰건명")

        register_deadline_text = self._field_value(soup, "입찰참가등록 마감일시")
        bid_deadline_text = self._field_value(soup, "입찰서제출 마감일시")
        open_at_text = self._field_value(soup, "개찰일시")

        detail = {
            "title": title,
            "organization": self._field_value(soup, "발주기관"),
            "notice_no": self._field_value(soup, "공고번호-차수"),
            "reference_no": self._field_value(soup, "통합참조번호"),
            "register_deadline_text": register_deadline_text,
            "bid_deadline_text": bid_deadline_text,
            "open_at_text": open_at_text,
            "source_url": page.url,
        }
        screenshot_path = self._capture_detail_screenshot(page, detail)
        if screenshot_path:
            detail["screenshot_path"] = screenshot_path
        detail["deadline_at"] = parse_datetime(register_deadline_text) or parse_datetime(
            bid_deadline_text
        )
        detail["open_at"] = parse_datetime(open_at_text)
        detail["period_text"] = (
            normalize_text(bid_deadline_text)
            or normalize_text(register_deadline_text)
            or "기간 미기재"
        )

        page.go_back(wait_until="networkidle")
        page.wait_for_timeout(500)
        return detail

    def _capture_detail_screenshot(self, page, detail: dict) -> str:
        base_dir = Path(os.getenv("TEMP_DIR", os.path.join(os.getcwd(), "output", "tmp")))
        screenshot_dir = base_dir / "d2b_screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        name_parts = [
            normalize_text(detail.get("notice_no", "")),
            normalize_text(detail.get("title", "")),
        ]
        raw_name = "-".join(part for part in name_parts if part) or "d2b-notice"
        safe_name = re.sub(r"[^0-9A-Za-z._-]+", "_", raw_name)[:120].strip("._-") or "d2b-notice"
        path = screenshot_dir / f"{safe_name}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    def _field_value(self, soup: BeautifulSoup, label: str) -> str:
        normalized_label = normalize_text(label)
        for header in soup.find_all(["th", "dt", "strong"]):
            text = normalize_text(header.get_text(" ", strip=True))
            if normalized_label in text:
                sibling = header.find_next(["td", "dd"])
                if sibling:
                    return normalize_text(sibling.get_text(" ", strip=True))
        return ""

    @staticmethod
    def is_active(candidate: NoticeCandidate, now: datetime) -> bool:
        if candidate.deadline_at is None:
            return True
        return candidate.deadline_at >= now
