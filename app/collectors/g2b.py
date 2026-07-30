from __future__ import annotations

import html
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List
from xml.etree import ElementTree as ET

from app.collectors.base import BaseCollector
from app.collectors.playwright_utils import browser_page
from app.types import NoticeCandidate
from app.utils import extract_datetimes, make_period_text, normalize_text


XML_NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
PROCUREMENT_PLAN_STAGE = "procurement_plan"
PRE_ANNOUNCEMENT_PAGE_SIZE = 100
G2B_ACTION_TIMEOUT_MS = 45000
G2B_DOWNLOAD_TIMEOUT_MS = 20000
G2B_BINDING_WAIT_TIMEOUT_MS = 15000
G2B_BINDING_RETRY_COUNT = 2


class G2BCollector(BaseCollector):
    site_code = "g2b"
    list_url = "https://www.g2b.go.kr/"

    def search(self, term: str) -> List[NoticeCandidate]:
        results: list[NoticeCandidate] = []
        results.extend(self._search_bid_notices(term))
        results.extend(self._search_pre_announcements(term))

        deduped: dict[str, NoticeCandidate] = {}
        for candidate in results:
            deduped.setdefault(candidate.site_notice_key, candidate)
        return list(deduped.values())

    def _search_bid_notices(self, term: str) -> List[NoticeCandidate]:
        download_path = self._download_bid_excel(term)
        if download_path is None:
            return []
        try:
            rows = self._parse_bid_excel(download_path)
        finally:
            if download_path.exists():
                download_path.unlink()

        results: list[NoticeCandidate] = []
        for row in rows:
            title = row.get("title", "")
            organization = row.get("organization", "")
            notice_no = row.get("notice_no", "")
            period_text = row.get("period_text", "")
            dates = extract_datetimes(period_text)
            posted_at = dates[0] if dates else None
            deadline_at = dates[1] if len(dates) > 1 else None
            source_url = (
                "https://www.g2b.go.kr/pn/pnp/pnpe/BidPbac/selectBidPbancDetail.do?bidPbancNo=%s"
                % notice_no
                if notice_no
                else self.list_url
            )

            results.append(
                NoticeCandidate(
                    site_code=self.site_code,
                    site_notice_key=notice_no or title,
                    title=title,
                    source_url=source_url,
                    organization=organization,
                    notice_no=notice_no,
                    start_at=posted_at,
                    deadline_at=deadline_at,
                    period_text=make_period_text(posted_at, deadline_at, raw=period_text),
                    raw_payload=row,
                )
            )
        return results

    def _search_pre_announcements(self, term: str) -> List[NoticeCandidate]:
        rows = self._fetch_pre_announcement_rows(term)
        if not rows:
            return []

        results: list[NoticeCandidate] = []
        seen_keys: set[str] = set()
        for row in rows:
            payload = self._normalize_payload_row(row)
            title = payload.get("bizNm", "")
            notice_no = payload.get("oderPlanNo", "")
            if not title:
                continue

            site_notice_key = f"prespec:{notice_no or title}"
            if site_notice_key in seen_keys:
                continue
            seen_keys.add(site_notice_key)

            organization = payload.get("oderInstUntyGrpNm") or payload.get("pbancInstUntyGrpNm")
            posted_text = payload.get("prcsYmd", "")
            dates = extract_datetimes(posted_text)
            posted_at = dates[0] if dates else None
            amount_value = self._coerce_amount_value(payload.get("bgtSumAmt"))

            # This route is an order/procurement-plan list, not the separate
            # G2B pre-specification service. Keep the legacy key for dedupe.
            payload["announcement_stage"] = PROCUREMENT_PLAN_STAGE
            if amount_value is not None:
                payload["amount_value"] = amount_value
                payload["amount_text"] = f"{amount_value:,}원"

            results.append(
                NoticeCandidate(
                    site_code=self.site_code,
                    site_notice_key=site_notice_key,
                    title=title,
                    source_url=self.list_url,
                    organization=organization,
                    notice_no=notice_no or None,
                    start_at=posted_at,
                    period_text=make_period_text(posted_at, None, raw=posted_text),
                    raw_payload=payload,
                    amount_value=amount_value,
                )
            )
        return results

    def _download_bid_excel(self, term: str) -> Path | None:
        with browser_page(accept_downloads=True) as page:
            page.goto(self.list_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            self._close_home_popup(page)
            self._open_bid_notice_menu(page)

            row_count = self._evaluate_with_binding_retry(
                page,
                binding_script="""
                    () =>
                        !!window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_scwin &&
                        !!window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_dlBidPbancLstM &&
                        !!window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_gridView1
                """,
                evaluate_script="""async ({ term, timeoutMs }) => {
                    const withTimeout = (promise, label) =>
                        Promise.race([
                            Promise.resolve(promise),
                            new Promise((_, reject) => {
                                setTimeout(
                                    () => reject(new Error(`${label} timed out after ${timeoutMs}ms`)),
                                    timeoutMs,
                                );
                            }),
                        ]);

                    const sc = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_scwin;
                    const dl = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_dlBidPbancLstM;
                    const grid = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_gridView1;
                    if (!sc || !dl || !grid) {
                        throw new Error("g2b bid search bindings not ready");
                    }

                    dl.set("bidPbancNo", "");
                    dl.set("bidPbancNm", term);
                    dl.set("pbancKndCd", "");
                    await withTimeout(sc.fnSrch4(), "g2b bid search");
                    return grid && typeof grid.getRowCount === "function" ? grid.getRowCount() : 0;
                }""",
                arg={"term": normalize_text(term), "timeoutMs": G2B_ACTION_TIMEOUT_MS},
                label="g2b bid search",
            )
            page.wait_for_timeout(1500)
            if not row_count:
                return None

            with page.expect_download(timeout=G2B_DOWNLOAD_TIMEOUT_MS) as download_info:
                page.evaluate(
                    "() => window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_scwin.btnExcelDown4_onclick()"
                )
            download = download_info.value

            target = Path(tempfile.gettempdir()) / ("g2b-%s" % download.suggested_filename)
            download.save_as(str(target))
            return target

    def _fetch_pre_announcement_rows(self, term: str) -> List[Dict[str, Any]]:
        with browser_page() as page:
            page.goto(self.list_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            self._close_home_popup(page)
            self._open_procurement_plan_menu(page)

            rows = self._evaluate_with_binding_retry(
                page,
                binding_script="""
                    () =>
                        !!window.mf_wfm_container_scwin &&
                        !!window.mf_wfm_container_dlOderReqSrchM &&
                        !!window.mf_wfm_container_gridView1
                """,
                evaluate_script="""async ({ term, pageSize, timeoutMs }) => {
                    const withTimeout = (promise, label) =>
                        Promise.race([
                            Promise.resolve(promise),
                            new Promise((_, reject) => {
                                setTimeout(
                                    () => reject(new Error(`${label} timed out after ${timeoutMs}ms`)),
                                    timeoutMs,
                                );
                            }),
                        ]);

                    const sc = window.mf_wfm_container_scwin;
                    const dl = window.mf_wfm_container_dlOderReqSrchM;
                    const grid = window.mf_wfm_container_gridView1;
                    if (!sc || !dl || !grid) {
                        throw new Error("g2b procurement plan bindings not ready");
                    }

                    dl.set("srchTy", "0002");
                    dl.set("bizNm", term);
                    dl.set("currentPage", 1);
                    dl.set("recordCountPerPage", String(pageSize));
                    await withTimeout(sc.sm_searchOderReqList(1), "g2b procurement plan page 1");

                    const firstPageCount =
                        typeof grid.getRowCount === "function" ? grid.getRowCount() : 0;
                    if (!firstPageCount) return [];

                    const firstRow =
                        typeof grid.getRowJSON === "function" ? (grid.getRowJSON(0) || {}) : {};
                    const totalCount = Number(firstRow.totCnt || firstPageCount) || firstPageCount;
                    const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
                    const collected = [];

                    for (let pageNo = 1; pageNo <= totalPages; pageNo += 1) {
                        if (pageNo > 1) {
                            dl.set("currentPage", pageNo);
                            await withTimeout(
                                sc.sm_searchOderReqList(pageNo),
                                `g2b procurement plan page ${pageNo}`,
                            );
                        }
                        const rowCount =
                            typeof grid.getRowCount === "function" ? grid.getRowCount() : 0;
                        for (let index = 0; index < rowCount; index += 1) {
                            const row =
                                typeof grid.getRowJSON === "function"
                                    ? (grid.getRowJSON(index) || {})
                                    : {};
                            collected.push(row);
                        }
                    }

                    return collected;
                }""",
                arg={
                    "term": normalize_text(term),
                    "pageSize": PRE_ANNOUNCEMENT_PAGE_SIZE,
                    "timeoutMs": G2B_ACTION_TIMEOUT_MS,
                },
                label="g2b procurement plan",
            )
            page.wait_for_timeout(800)
            return rows if isinstance(rows, list) else []

    def _evaluate_with_binding_retry(
        self,
        page,
        *,
        binding_script: str,
        evaluate_script: str,
        arg: Dict[str, Any],
        label: str,
    ):
        last_error: Exception | None = None
        for attempt in range(1, G2B_BINDING_RETRY_COUNT + 1):
            try:
                page.wait_for_function(binding_script, timeout=G2B_BINDING_WAIT_TIMEOUT_MS)
                return page.evaluate(evaluate_script, arg)
            except Exception as exc:
                last_error = exc
                if attempt >= G2B_BINDING_RETRY_COUNT:
                    break
                page.wait_for_timeout(1500)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{label} evaluation failed without a captured exception")

    def _close_home_popup(self, page) -> None:
        close_button = page.locator(
            "#mf_wfm_container_wq_uuid_925_wq_uuid_932_poupR23AB00000134241_wframe_popupCnts_btnClose"
        )
        if close_button.count():
            close_button.first.click(force=True)
            page.wait_for_timeout(800)

    def _open_bid_notice_menu(self, page) -> None:
        for menu_id in [
            "mf_wfm_gnb_wfm_gnbMenu_genMenu1_1_btnMenu1",
            "mf_wfm_gnb_wfm_gnbMenu_genMenu1_1_genMenu2_0_btnMenu2",
            "mf_wfm_gnb_wfm_gnbMenu_genMenu1_1_genMenu2_0_genMenu3_0_btnMenu3",
        ]:
            page.evaluate(
                "(menu_id) => { const el = document.getElementById(menu_id); if (el) el.click(); }",
                menu_id,
            )
            page.wait_for_timeout(1000)

    def _open_procurement_plan_menu(self, page) -> None:
        for menu_id in [
            "mf_wfm_gnb_wfm_gnbMenu_genMenu1_0_btnMenu1",
            "mf_wfm_gnb_wfm_gnbMenu_genMenu1_0_genMenu2_0_btnMenu2",
        ]:
            page.evaluate(
                "(menu_id) => { const el = document.getElementById(menu_id); if (el) el.click(); }",
                menu_id,
            )
            page.wait_for_timeout(1200)

    def _parse_bid_excel(self, path: Path) -> List[Dict[str, str]]:
        with zipfile.ZipFile(path) as archive:
            shared_strings = self._read_shared_strings(archive)
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))

        rows: list[dict[str, str]] = []
        for row in sheet.findall(".//a:sheetData/a:row", XML_NS):
            values = self._read_row_values(row, shared_strings)
            if len(values) < 8:
                continue
            notice_no = normalize_text(values[3])
            if not re.match(r"^[A-Z0-9]{4,}-\d+$", notice_no):
                continue
            title = normalize_text(values[4])
            if not title:
                continue

            rows.append(
                {
                    "business_type": normalize_text(values[0]),
                    "domestic_overseas": normalize_text(values[1]),
                    "notice_kind": normalize_text(values[2]),
                    "notice_no": notice_no,
                    "title": title,
                    "organization": normalize_text(values[6]) or normalize_text(values[5]),
                    "posted_by": normalize_text(values[5]),
                    "period_text": normalize_text(values[7]),
                }
            )
        return rows

    def _read_shared_strings(self, archive: zipfile.ZipFile) -> List[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        values = []
        for item in root.findall("a:si", XML_NS):
            text = "".join(node.text or "" for node in item.iterfind(".//a:t", XML_NS))
            values.append(normalize_text(text))
        return values

    def _read_row_values(self, row, shared_strings: List[str]) -> List[str]:
        values = []
        for cell in row.findall("a:c", XML_NS):
            cell_type = cell.get("t")
            if cell_type == "inlineStr":
                text = "".join(
                    node.text or "" for node in cell.iterfind(".//a:is//a:t", XML_NS)
                )
                values.append(normalize_text(text))
                continue

            value_node = cell.find("a:v", XML_NS)
            if value_node is None:
                values.append("")
                continue

            raw_value = value_node.text or ""
            if cell_type == "s":
                try:
                    values.append(shared_strings[int(raw_value)])
                except (ValueError, IndexError):
                    values.append(normalize_text(raw_value))
            else:
                values.append(normalize_text(raw_value))
        return values

    def _normalize_payload_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for key, value in row.items():
            normalized_key = normalize_text(str(key))
            if not normalized_key:
                continue
            if isinstance(value, str):
                payload[normalized_key] = normalize_text(html.unescape(value))
            else:
                payload[normalized_key] = value
        return payload

    def _coerce_amount_value(self, value: Any) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, float):
            parsed = int(value)
            return parsed if parsed > 0 else None

        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            return None
        parsed = int(digits)
        return parsed if parsed > 0 else None
