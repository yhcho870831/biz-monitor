from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.collectors.playwright_utils import browser_page
from app.types import NoticeCandidate
from app.utils import normalize_text


AMOUNT_FIELD_LABELS = (
    "사업금액",
    "총사업비",
    "사업비",
    "예산금액",
    "예산",
    "소요예산",
    "배정금액",
    "추정가격",
    "기초금액",
    "금액",
)

AMOUNT_TEXT_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?)\s*억|(?:\d[\d,]*)\s*백만원|(?:\d[\d,]*)\s*천원|(?:\d[\d,]{1,})\s*원"
)


def ensure_candidate_amount(candidate: NoticeCandidate) -> bool:
    try:
        if candidate.amount_value is not None:
            return True

        direct_value = _coerce_amount_value((candidate.raw_payload or {}).get("amount_value"))
        if direct_value is not None:
            candidate.amount_value = direct_value
            return True

        amount_text = normalize_text((candidate.raw_payload or {}).get("amount_text", ""))
        if amount_text:
            candidate.raw_payload["amount_text"] = amount_text
            direct_value = _extract_amount_from_text(amount_text)
            if direct_value is not None:
                candidate.amount_value = direct_value
                candidate.raw_payload["amount_value"] = direct_value
                return True

        detail_text = _fetch_detail_amount_text(candidate)
        if not detail_text:
            return False

        candidate.raw_payload["amount_text"] = detail_text
        direct_value = _extract_amount_from_text(detail_text)
        if direct_value is None:
            return False

        candidate.amount_value = direct_value
        candidate.raw_payload["amount_value"] = direct_value
        return True
    except Exception:
        return False


def _coerce_amount_value(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    if isinstance(value, str):
        text = normalize_text(value)
        if not text:
            return None
        digits = re.sub(r"[^\d]", "", text)
        if digits:
            parsed = int(digits)
            return parsed if parsed > 0 else None
    return None


def _extract_amount_from_text(text: str) -> int | None:
    normalized = normalize_text(text)
    if not normalized:
        return None

    amounts: list[int] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*억", normalized):
        amounts.append(int(float(match.group(1)) * 100_000_000))
    for match in re.finditer(r"(\d[\d,]*)\s*백만원", normalized):
        amounts.append(int(match.group(1).replace(",", "")) * 1_000_000)
    for match in re.finditer(r"(\d[\d,]*)\s*천원", normalized):
        amounts.append(int(match.group(1).replace(",", "")) * 1_000)
    for match in re.finditer(r"(\d[\d,]{1,})\s*원", normalized):
        amounts.append(int(match.group(1).replace(",", "")))
    return max(amounts) if amounts else None


def _fetch_detail_amount_text(candidate: NoticeCandidate) -> str:
    if not candidate.source_url:
        return ""

    if candidate.site_code == "g2b":
        soup = _fetch_g2b_detail_soup(candidate)
        return _extract_amount_text_from_soup(soup)

    if candidate.site_code == "d2b":
        soup = _fetch_d2b_detail_soup(candidate)
        return _extract_amount_text_from_soup(soup)

    with browser_page() as page:
        page.goto(candidate.source_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
        soup = BeautifulSoup(page.content(), "html.parser")

    return _extract_amount_text_from_soup(soup)


def _extract_amount_text_from_soup(soup: BeautifulSoup) -> str:
    labeled = _extract_labeled_amount_text(soup)
    if labeled:
        return labeled

    full_text = normalize_text(soup.get_text("\n", strip=True))
    if not full_text:
        return ""

    contextual = _extract_contextual_amount_text(full_text)
    if contextual:
        return contextual

    return ""


def _fetch_g2b_detail_soup(candidate: NoticeCandidate) -> BeautifulSoup:
    with browser_page() as page:
        page.goto("https://www.g2b.go.kr/", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        close_button = page.locator(
            "#mf_wfm_container_wq_uuid_925_wq_uuid_932_poupR23AB00000134241_wframe_popupCnts_btnClose"
        )
        if close_button.count():
            close_button.first.click(force=True)
            page.wait_for_timeout(800)

        for menu_id in [
            "mf_wfm_gnb_wfm_gnbMenu_genMenu1_1_btnMenu1",
            "mf_wfm_gnb_wfm_gnbMenu_genMenu1_1_genMenu2_0_btnMenu2",
            "mf_wfm_gnb_wfm_gnbMenu_genMenu1_1_genMenu2_0_genMenu3_0_btnMenu3",
        ]:
            page.evaluate(
                "(menuId) => { const el = document.getElementById(menuId); if (el) el.click(); }",
                menu_id,
            )
            page.wait_for_timeout(1000)

        found = page.evaluate(
            """async ({ noticeNo, title }) => {
                const sc = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_scwin;
                const dl = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_dlBidPbancLstM;
                const list = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_dlBidPbancLstL;
                if (!sc || !dl || !list) return 0;
                dl.set('bidPbancNo', noticeNo || '');
                dl.set('bidPbancNm', noticeNo ? '' : (title || ''));
                dl.set('pbancKndCd', '');
                await sc.fnSrch4();
                return typeof list.getRowCount === 'function' ? list.getRowCount() : 0;
            }""",
            {
                "noticeNo": normalize_text(candidate.notice_no or ""),
                "title": normalize_text(candidate.title or ""),
            },
        )
        page.wait_for_timeout(1200)
        if not found:
            return BeautifulSoup("", "html.parser")

        detail_opened = page.evaluate(
            """async () => {
                try {
                    const sc = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_scwin;
                    const dl = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_dlBidPbancLstM;
                    const list = window.mf_wfm_container_tacBidPbancLst_contents_tab2_body_dlBidPbancLstL;
                    const row = list.getRowJSON(0);
                    const options = { isHistory: true, param: row };
                    options.param.pbancType = 'pbanc';
                    options.param.bidPbancNo = options.param.bidPbancUntyNo;
                    options.param.bidPbancOrd = options.param.bidPbancUntyOrd;
                    const searchInfo = {
                        mapId: 'dlBidPbancLstM',
                        mapData: dl.getJSON ? dl.getJSON() : {},
                        callbackFn: 'scwin.fnSrch4',
                    };
                    await sc.fnMoveBidDtl2(options, searchInfo);
                    return true;
                } catch (error) {
                    return false;
                }
            }"""
        )
        if not detail_opened:
            return BeautifulSoup("", "html.parser")

        page.wait_for_timeout(2500)
        return BeautifulSoup(page.content(), "html.parser")


def _fetch_d2b_detail_soup(candidate: NoticeCandidate) -> BeautifulSoup:
    with browser_page() as page:
        page.goto(
            "https://www.d2b.go.kr/psb/bid/serviceBidAnnounceList.do?key=137",
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(1500)
        page.locator("#chgDate3").click()
        page.locator("#anmt_name").fill(normalize_text(candidate.title))
        page.locator("#btn_search").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)

        rows = page.locator("#SBHE_DATAGRID_WHOLE_TABLE_datagrid1 tr").filter(
            has=page.locator("a[href='#none;']")
        )
        row_count = rows.count()
        target_row = None
        for index in range(row_count):
            row = rows.nth(index)
            row_text = normalize_text(row.inner_text())
            if candidate.notice_no and normalize_text(candidate.notice_no) in row_text:
                target_row = row
                break
            if normalize_text(candidate.title) and normalize_text(candidate.title) in row_text:
                target_row = row
                break

        if target_row is None:
            return BeautifulSoup("", "html.parser")

        target_row.locator("a[href='#none;']").first.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1200)
        return BeautifulSoup(page.content(), "html.parser")


def _extract_labeled_amount_text(soup: BeautifulSoup) -> str:
    for header in soup.find_all(["th", "dt", "strong", "label"]):
        label_text = normalize_text(header.get_text(" ", strip=True))
        if not _contains_amount_label(label_text):
            continue

        sibling = header.find_next(["td", "dd"])
        sibling_text = normalize_text(sibling.get_text(" ", strip=True)) if sibling else ""
        combined = normalize_text(f"{label_text} {sibling_text}")
        if combined and AMOUNT_TEXT_PATTERN.search(combined):
            return combined

    chunks = [normalize_text(text) for text in soup.stripped_strings if normalize_text(text)]
    for index, chunk in enumerate(chunks):
        if not _contains_amount_label(chunk):
            continue
        window = normalize_text(" ".join(chunks[index : index + 3]))
        if window and AMOUNT_TEXT_PATTERN.search(window):
            return window

    return ""


def _extract_contextual_amount_text(text: str) -> str:
    compact = normalize_text(text)
    for label in AMOUNT_FIELD_LABELS:
        pattern = re.compile(
            rf"({re.escape(label)}[^\\n]{{0,80}}?(?:\d+(?:\.\d+)?\s*억|\d[\d,]*\s*백만원|\d[\d,]*\s*천원|\d[\d,]{1,}\s*원))"
        )
        match = pattern.search(compact)
        if match:
            return normalize_text(match.group(1))
    return ""


def _contains_amount_label(text: str) -> bool:
    return any(label in text for label in AMOUNT_FIELD_LABELS)
