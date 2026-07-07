from __future__ import annotations

from typing import List
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.collectors.playwright_utils import browser_page
from app.types import NoticeCandidate
from app.utils import normalize_text


class KIMSTCollector(BaseCollector):
    site_code = "kimst"
    base_url = "https://www.kimst.re.kr/u/news/inform_01/pjtAnuc.do"

    def search(self, term: str) -> List[NoticeCandidate]:
        url = "%s?searchType=4&searchKeyword=%s&page=1" % (
            self.base_url,
            quote(term),
        )
        soup = BeautifulSoup(self._fetch_html(url), "html.parser")
        table = soup.find("table", class_="table table-list")
        if table is None:
            return []

        tbody = table.find("tbody")
        if tbody is None:
            return []

        results = []
        for row in tbody.find_all("tr"):
            columns = row.find_all("td")
            if len(columns) < 4:
                continue

            notice_no = normalize_text(columns[0].get_text(" ", strip=True))
            title_link = columns[1].find("a")
            if title_link is None:
                continue

            title = normalize_text(title_link.get_text(" ", strip=True))
            href = normalize_text(title_link.get("href", ""))
            if href:
                href = urljoin(self.base_url, href)

            start_text = normalize_text(columns[2].get_text(" ", strip=True))
            period_text = normalize_text(columns[3].get_text(" ", strip=True))
            site_notice_key = notice_no or href or title

            results.append(
                NoticeCandidate(
                    site_code=self.site_code,
                    site_notice_key=site_notice_key,
                    title=title,
                    source_url=href,
                    notice_no=notice_no,
                    period_text=period_text or start_text,
                    raw_payload={
                        "notice_no": notice_no,
                        "start_text": start_text,
                        "period_text": period_text,
                    },
                )
            )
        return results

    def _fetch_html(self, url: str) -> str:
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            with browser_page() as page:
                page.goto(url, wait_until="networkidle")
                return page.content()
