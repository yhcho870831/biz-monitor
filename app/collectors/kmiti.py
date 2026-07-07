from __future__ import annotations

import re
from typing import List

import requests
from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.types import NoticeCandidate
from app.utils import extract_datetimes, normalize_text, parse_title_deadline


class KMITICollector(BaseCollector):
    site_code = "kmiti"
    list_url = "https://www.kmiti.or.kr/kr/board/kmi_notice/boardList.do"
    detail_url = "https://www.kmiti.or.kr/kr/board/kmi_notice/boardView.do"

    def search(self, term: str) -> List[NoticeCandidate]:
        html = self._search_page_html(term)
        return self._parse_results(html)

    def _search_page_html(self, term: str) -> str:
        response = requests.post(
            self.list_url,
            data={
                "searchCondition": "all",
                "searchKeyword": term,
                "pageIndex": "1",
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.content.decode("utf-8", "ignore")

    def _parse_results(self, html: str) -> List[NoticeCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        results = []
        seen_keys = set()

        for item in soup.select("li[onclick*='fnLinkView']"):
            onclick = item.get("onclick", "")
            match = re.search(
                r"fnLinkView\('(?P<bbs_idx>\d+)'\s*,\s*'(?P<bcf_idx>\d+)'\s*,\s*'(?P<cat_idx>\d+)'\)",
                onclick,
            )
            if not match:
                continue

            bbs_idx = match.group("bbs_idx")
            cat_idx = match.group("cat_idx")
            site_notice_key = bbs_idx
            if site_notice_key in seen_keys:
                continue
            seen_keys.add(site_notice_key)

            title_link = item.find("a")
            if title_link is None:
                continue

            title = normalize_text(title_link.get_text(" ", strip=True))
            if title.startswith("제목"):
                title = normalize_text(title[2:])

            category = normalize_text(
                (item.select_one("p.con.w10") or item.find("p", class_="con")).get_text(
                    " ", strip=True
                )
            )
            posted_at = normalize_text(
                (item.select_one("p.date") or item.find("p", class_="date")).get_text(
                    " ", strip=True
                )
            )
            detail_url = "%s?bbsIdx=%s&catIdx=%s" % (
                self.detail_url,
                bbs_idx,
                cat_idx,
            )

            # The list only exposes the posting date; the real deadline (if any)
            # lives in the title, e.g. 「…공모전」(~4.24.(금) 18:00). Parse it so
            # expired notices are filtered and the Slack "입찰마감" shows the true
            # deadline instead of the posting date.
            posted_dates = extract_datetimes(posted_at)
            deadline_at = parse_title_deadline(
                title, reference=posted_dates[0] if posted_dates else None
            )

            results.append(
                NoticeCandidate(
                    site_code=self.site_code,
                    site_notice_key=site_notice_key,
                    title=title,
                    source_url=detail_url,
                    organization="한국기상산업기술원",
                    notice_no=bbs_idx,
                    period_text=("작성일 %s" % posted_at) if posted_at else "기간 미기재",
                    deadline_at=deadline_at,
                    raw_payload={
                        "category": category,
                        "posted_at": posted_at,
                        "cat_idx": cat_idx,
                    },
                )
            )
        return results
