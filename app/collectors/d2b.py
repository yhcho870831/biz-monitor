from __future__ import annotations

import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.collectors.playwright_utils import browser_page
from app.types import NoticeCandidate
from app.utils import make_period_text, normalize_text, parse_datetime


D2B_RETURN_TIMEOUT_MS = 15_000
D2B_DETAIL_READY_TIMEOUT_MS = 8_000
MAX_RESULT_PAGES = 10

logger = logging.getLogger(__name__)


class D2BCollector(BaseCollector):
    site_code = "d2b"
    list_url = "https://www.d2b.go.kr/psb/bid/serviceBidAnnounceList.do?key=137"

    def search(self, term: str) -> List[NoticeCandidate]:
        results: List[NoticeCandidate] = []

        with browser_page() as page:
            self._search_page(page, term)
            targets = self._collect_targets(page, term)

            for page_no, index in targets:
                self._search_page(page, term)
                self._goto_result_page(page, page_no)
                row = self._result_rows(page).nth(index)
                link = row.locator("a[href='#none;']").first
                title = normalize_text(link.inner_text())

                cells = row.locator("td")
                cell_values = [
                    normalize_text(cells.nth(i).inner_text()) for i in range(cells.count())
                ]
                notice_no = cell_values[3].split(" ")[0] if len(cell_values) > 3 else ""
                reference_no = cell_values[3].split(" ")[-1] if len(cell_values) > 3 else ""
                organization = cell_values[5] if len(cell_values) > 5 else ""
                register_deadline = parse_datetime(cell_values[6]) if len(cell_values) > 6 else None
                bid_deadline = parse_datetime(cell_values[7]) if len(cell_values) > 7 else None

                detail = self._extract_detail(page, link, term)
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
        # No period preset is clicked: the form's default window is
        # "bid deadline from today to +2 months", i.e. only notices that are
        # still open. The old 12-month preset returned the *oldest* 20 of
        # hundreds of mostly closed notices, so nothing open was ever seen.
        page.goto(self.list_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.locator("#anmt_name").fill(normalize_text(term))
        page.locator("#btn_search").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)

    def _collect_targets(self, page, term: str) -> list[tuple[int, int]]:
        """Return (page_no, row_index) for every result row whose title matches."""
        normalized_term = normalize_text(term)
        total = self._total_count(page)
        first_rows = self._result_rows(page).count()
        pages = 1
        if first_rows and total > first_rows:
            pages = min(MAX_RESULT_PAGES, -(-total // first_rows))
            if -(-total // first_rows) > MAX_RESULT_PAGES:
                logger.warning(
                    "d2b result pages capped term=%s total=%s pages=%s", normalized_term, total, pages
                )
        targets: list[tuple[int, int]] = []
        for page_no in range(1, pages + 1):
            if page_no > 1:
                self._goto_result_page(page, page_no)
            rows = self._result_rows(page)
            for index in range(rows.count()):
                title = normalize_text(rows.nth(index).locator("a[href='#none;']").first.inner_text())
                if normalized_term and normalized_term not in title:
                    continue
                targets.append((page_no, index))
        return targets

    def _total_count(self, page) -> int:
        match = re.search(r"총\s*([\d,]+)\s*건", page.locator("body").inner_text())
        return int(match.group(1).replace(",", "")) if match else 0

    def _goto_result_page(self, page, page_no: int) -> None:
        if page_no <= 1:
            return
        page.evaluate("(n) => view.grid.selectPageNo(n)", page_no)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

    def _result_rows(self, page):
        return page.locator("#SBHE_DATAGRID_WHOLE_TABLE_datagrid1 tr").filter(
            has=page.locator("a[href='#none;']")
        )

    def _extract_detail(self, page, link, term: str) -> dict:
        link.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        # The detail body is sometimes still empty after networkidle; wait for
        # a field label instead of scraping a blank page.
        try:
            page.get_by_text("개찰일시").first.wait_for(timeout=D2B_DETAIL_READY_TIMEOUT_MS)
        except Exception:
            logger.warning("d2b detail body not ready url=%s", page.url)

        soup = BeautifulSoup(page.content(), "html.parser")
        title = ""
        for paragraph in soup.select("p"):
            text = normalize_text(paragraph.get_text(" ", strip=True))
            # Open-negotiation (공개수의) notices headline "[공사명]" instead.
            for prefix in ("[입찰건명]", "[공사명]"):
                if text.startswith(prefix):
                    title = text.replace(prefix, "", 1).strip()
                    break
            if title:
                break
        if not title:
            title = self._field_value(soup, "입찰건명")

        register_deadline_text = self._field_value(soup, "입찰참가등록 마감일시")
        # Open-negotiation notices call the bid deadline "견적서제출마감일시".
        bid_deadline_text = self._field_value(soup, "입찰서제출 마감일시") or self._field_value(
            soup, "견적서제출마감일시"
        )
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

        self._return_to_result_page(page, term)
        return detail

    def _return_to_result_page(self, page, term: str) -> None:
        """Restore the list after a detail scrape without failing the whole job.

        D2B sometimes keeps background requests open, so waiting for
        ``networkidle`` after ``go_back`` can time out even though the browser is
        otherwise usable.  A single direct list-page reload is the recovery path
        for that transient navigation failure.
        """
        try:
            page.go_back(
                wait_until="domcontentloaded",
                timeout=D2B_RETURN_TIMEOUT_MS,
            )
            page.wait_for_timeout(500)
        except Exception:
            logger.warning(
                "d2b detail return failed; reopening result page once term=%s",
                normalize_text(term),
                exc_info=True,
            )
            self._search_page(page, term)

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
