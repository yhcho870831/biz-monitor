from __future__ import annotations

import re
from typing import List
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.collectors.playwright_utils import browser_page
from app.types import NoticeCandidate
from app.utils import normalize_text


IRIS_NAVIGATION_RETRY_COUNT = 2
IRIS_RESULT_WAIT_TIMEOUT_MS = 15000
IRIS_SETTLE_WAIT_MS = 800


class IRISCollector(BaseCollector):
    site_code = "iris"
    list_url = "https://www.iris.go.kr/contents/retrieveBsnsAncmListView.do"
    detail_url = "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do"
    schedule_list_url = "https://www.iris.go.kr/contents/retrieveAncmPrntcListView.do"
    schedule_detail_url = "https://www.iris.go.kr/contents/retrieveAncmPrntcView.do"

    def search(self, term: str) -> List[NoticeCandidate]:
        business_results = self._search_with_field(term, "#bsnsAncmTl")
        if not business_results:
            business_results = self._search_with_field(term, "#ancmTl")

        schedule_results = self._search_schedule(term)
        return self._merge_results(business_results, schedule_results)

    def _search_with_field(self, term: str, selector: str) -> List[NoticeCandidate]:
        with browser_page() as page:
            self._goto_list_page(page, self.list_url)
            page.locator(selector).fill(term)
            page.locator("form#bsnsAncmListForm button.btn.type1").first.click()
            self._wait_for_result_body(page)
            body = page.locator(".dbody")
            if body.count() == 0:
                return []
            return self._parse_results(body.inner_html())

    def _parse_results(self, html: str) -> List[NoticeCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        results = []
        seen_keys = set()

        for anchor in soup.find_all("a", onclick=re.compile(r"f_bsnsAncmListForm_view\(")):
            onclick = anchor.get("onclick", "")
            match = re.search(
                r"f_bsnsAncmListForm_view\('(?P<ancm_id>[^']+)','(?P<bsns_yy>[^']+)','(?P<sorgn_bsns_cd>[^']+)','(?P<bsns_ancm_sn>[^']+)','(?P<d_day>[^']+)','(?P<rcve_str_dt>[^']+)','(?P<rcve_end_dt>[^']+)'\)",
                onclick,
            )
            if not match:
                continue

            params = match.groupdict()
            site_notice_key = "%(ancm_id)s:%(bsns_yy)s:%(sorgn_bsns_cd)s:%(bsns_ancm_sn)s" % params
            if site_notice_key in seen_keys:
                continue
            seen_keys.add(site_notice_key)

            item = anchor.find_parent("li")
            if item is None:
                continue

            title = normalize_text(anchor.get_text(" ", strip=True))
            organization = self._normalize_organization_text(
                normalize_text((item.select_one(".inst_title") or item).get_text(" ", strip=True))
            )
            period_text = normalize_text(
                (item.select_one(".period") or item).get_text(" ", strip=True)
            )
            query = {
                "ancmId": params["ancm_id"],
                "bsnsYyDetail": params["bsns_yy"],
                "sorgnBsnsCd": params["sorgn_bsns_cd"],
                "bsnsAncmSn": params["bsns_ancm_sn"],
                "detailDDay": params["d_day"],
                "chngRcveDeFrom": params["rcve_str_dt"].replace("/", ""),
                "chngRcveDeTo": params["rcve_end_dt"].replace("/", ""),
            }
            detail_url = "%s?%s" % (self.detail_url, urlencode(query))

            results.append(
                NoticeCandidate(
                    site_code=self.site_code,
                    site_notice_key=site_notice_key,
                    title=title,
                    source_url=detail_url,
                    organization=organization or "IRIS",
                    notice_no=params["ancm_id"],
                    period_text=period_text or "기간 미기재",
                    raw_payload={
                        "iris_result_type": "business",
                        "bsns_yy": params["bsns_yy"],
                        "sorgn_bsns_cd": params["sorgn_bsns_cd"],
                        "bsns_ancm_sn": params["bsns_ancm_sn"],
                        "detail_d_day": params["d_day"],
                    },
                )
            )
        return results

    def _search_schedule(self, term: str) -> List[NoticeCandidate]:
        with browser_page() as page:
            self._goto_list_page(page, self.schedule_list_url)
            page.locator("#ancmPrntcTl").fill(term)

            search_button = page.locator("form#ancmPrntcListForm button.btn.type1")
            if search_button.count() > 0:
                search_button.first.click()
            else:
                page.get_by_role("button", name="검색").first.click()

            self._wait_for_result_body(page)
            return self._parse_schedule_results(page.content())

    def _goto_list_page(self, page, url: str) -> None:
        last_error: Exception | None = None
        for attempt in range(1, IRIS_NAVIGATION_RETRY_COUNT + 1):
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_selector("body", timeout=IRIS_RESULT_WAIT_TIMEOUT_MS)
                page.wait_for_timeout(IRIS_SETTLE_WAIT_MS)
                return
            except Exception as exc:
                last_error = exc
                if attempt >= IRIS_NAVIGATION_RETRY_COUNT:
                    break
                page.wait_for_timeout(IRIS_SETTLE_WAIT_MS)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"IRIS page load failed for {url}")

    def _wait_for_result_body(self, page) -> None:
        page.wait_for_selector(".dbody", timeout=IRIS_RESULT_WAIT_TIMEOUT_MS)
        page.wait_for_timeout(IRIS_SETTLE_WAIT_MS)

    def _parse_schedule_results(self, html: str) -> List[NoticeCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        results = []
        seen_keys = set()

        pattern = re.compile(r"f_ancmPrntcListForm_view\(")
        for anchor in soup.find_all("a", onclick=pattern):
            onclick = anchor.get("onclick", "")
            match = re.search(
                r"f_ancmPrntcListForm_view\('(?P<ancm_id>[^']*)','(?P<bsns_yy>[^']*)','(?P<sorgn_bsns_cd>[^']*)','(?P<ancm_prntc_sn>[^']*)','(?P<ancm_turn>[^']*)','(?P<seq>[^']*)','(?P<hirk_sorgn_bsns_cd>[^']*)','(?P<sorgn_id>[^']*)'\)",
                onclick,
            )
            if not match:
                continue

            params = match.groupdict()
            site_notice_key = "prntc:%(ancm_id)s:%(bsns_yy)s:%(sorgn_bsns_cd)s:%(ancm_prntc_sn)s:%(seq)s" % params
            if site_notice_key in seen_keys:
                continue
            seen_keys.add(site_notice_key)

            item = anchor.find_parent("li")
            if item is None:
                item = anchor.parent
            if item is None:
                continue

            title = normalize_text(anchor.get_text(" ", strip=True))
            item_text = normalize_text(item.get_text(" ", strip=True))
            organization = self._extract_schedule_organization(item_text, title)
            period_text = self._extract_schedule_period_text(item_text, params["bsns_yy"])

            query = {
                "ancmId": params["ancm_id"],
                "bsnsYy": params["bsns_yy"],
                "sorgnBsnsCd": params["sorgn_bsns_cd"],
                "ancmPrntcSn": params["ancm_prntc_sn"],
                "ancmTurn": params["ancm_turn"],
                "seq": params["seq"],
                "hirkSorgnBsnsCd": params["hirk_sorgn_bsns_cd"],
                "sorgnId": params["sorgn_id"],
            }
            detail_url = "%s?%s" % (self.schedule_detail_url, urlencode(query))

            results.append(
                NoticeCandidate(
                    site_code=self.site_code,
                    site_notice_key=site_notice_key,
                    title=title,
                    source_url=detail_url,
                    organization=organization or "IRIS",
                    notice_no=params["ancm_id"] or params["ancm_prntc_sn"] or params["seq"],
                    period_text=period_text,
                    raw_payload={
                        "iris_result_type": "schedule",
                        "bsns_yy": params["bsns_yy"],
                        "sorgn_bsns_cd": params["sorgn_bsns_cd"],
                        "ancm_prntc_sn": params["ancm_prntc_sn"],
                        "ancm_turn": params["ancm_turn"],
                        "seq": params["seq"],
                        "hirk_sorgn_bsns_cd": params["hirk_sorgn_bsns_cd"],
                        "sorgn_id": params["sorgn_id"],
                    },
                )
            )
        return results

    def _merge_results(
        self,
        business_results: List[NoticeCandidate],
        schedule_results: List[NoticeCandidate],
    ) -> List[NoticeCandidate]:
        merged: List[NoticeCandidate] = []
        seen: set[str] = set()
        seen_links: set[str] = set()
        seen_titles: set[str] = set()

        for candidate in [*business_results, *schedule_results]:
            normalized_title = normalize_text(candidate.title).lower()
            normalized_link = normalize_text(candidate.source_url)
            if candidate.site_notice_key in seen:
                continue
            if normalized_link and normalized_link in seen_links:
                continue
            if normalized_title and normalized_title in seen_titles:
                continue

            seen.add(candidate.site_notice_key)
            if normalized_link:
                seen_links.add(normalized_link)
            if normalized_title:
                seen_titles.add(normalized_title)
            merged.append(candidate)
        return merged

    def _extract_schedule_organization(self, item_text: str, title: str) -> str:
        text = normalize_text(item_text)
        if not text:
            return ""
        candidate = text.replace(title, "", 1).strip()
        for token in (
            "공모예정",
            "사업일정",
            "접수",
            "조회수",
            "등록일",
            "사업년도",
        ):
            if token in candidate:
                candidate = candidate.split(token, 1)[0].strip()
        return self._normalize_organization_text(candidate.strip(" |"))

    def _normalize_organization_text(self, value: str) -> str:
        text = normalize_text(value)
        if not text:
            return ""
        text = re.sub(r"^\d+\s*", "", text)
        text = re.sub(r"^(전문기관|주관연구개발기관|주관기관|발주부처|발주처)\s*:\s*", "", text)
        text = re.sub(r"\s*\|\s*$", "", text)
        return text.strip()

    def _extract_schedule_period_text(self, item_text: str, bsns_yy: str) -> str:
        text = normalize_text(item_text)
        months = sorted({int(month) for month in re.findall(r"(\d{1,2})\s*월", text)})
        if months:
            month_labels = ", ".join("%d월" % month for month in months)
            return f"사업년도 {bsns_yy} / 공모예정월 {month_labels}"
        if bsns_yy:
            return f"사업년도 {bsns_yy} / 공모예고(사업일정)"
        return "공모예고(사업일정)"
