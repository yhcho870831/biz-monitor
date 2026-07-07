from __future__ import annotations

import re
from typing import List

import requests
from bs4 import BeautifulSoup

from app.collectors.base import BaseCollector
from app.collectors.playwright_utils import browser_page
from app.types import NoticeCandidate
from app.utils import normalize_text


class NIACollector(BaseCollector):
    site_code = "nia"
    list_url = "https://www.nia.or.kr/site/nia_kor/ex/bbs/List.do?cbIdx=78336"
    view_url = "https://www.nia.or.kr/site/nia_kor/ex/bbs/View.do"

    def search(self, term: str) -> List[NoticeCandidate]:
        html = self._search_page_html(term)
        return self._parse_search_results(html)

    def _search_page_html(self, term: str) -> str:
        with browser_page() as page:
            page.goto(self.list_url, wait_until="networkidle")
            page.locator("#searchKeyTemp").fill(term)
            page.locator("button.theme_a.small").click()
            page.wait_for_load_state("networkidle")
            return page.content()

    def _parse_search_results(self, html: str) -> List[NoticeCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        board = soup.find("div", class_="board_type01")
        if board is None:
            return []

        results = []
        for item in board.select("ul > li"):
            if "conNone" in (item.get("class") or []):
                continue
            anchor = item.find("a", href=True)
            if anchor is None:
                continue
            onclick = anchor.get("onclick", "")
            match = re.search(
                r"doBbsFView\('(?P<cbidx>\d+)','(?P<bcidx>\d+)','(?P<gbn>[^']+)','(?P<parent>\d+)'\)",
                onclick,
            )
            if not match:
                continue

            bc_idx = match.group("bcidx")
            parent_seq = match.group("parent")
            detail_url = "%s?cbIdx=78336&bcIdx=%s&parentSeq=%s" % (
                self.view_url,
                bc_idx,
                parent_seq,
            )
            list_text = normalize_text(anchor.get_text(" ", strip=True))
            list_title = self._clean_result_title(list_text)
            detail = self._fetch_detail(detail_url)
            detail.setdefault("source_url", detail_url)
            detail_title = self._clean_result_title(str(detail.get("title") or ""))
            if not detail_title or detail_title in {"\uc785\ucc30\uacf5\uace0", "\ubaa9\ub85d"}:
                detail["title"] = list_title
            else:
                detail["title"] = detail_title

            posted_at = detail.get("posted_at") or self._extract_posted_at(list_text)
            if posted_at:
                detail["posted_at"] = posted_at
            if posted_at and not detail.get("period_text"):
                detail["period_text"] = "\uac8c\uc2dc\uc77c %s" % posted_at

            results.append(
                NoticeCandidate(
                    site_code=self.site_code,
                    site_notice_key=bc_idx,
                    title=detail.get("title", list_title),
                    source_url=detail["source_url"],
                    organization=detail.get("organization"),
                    notice_no=bc_idx,
                    period_text=detail.get("period_text"),
                    raw_payload=detail,
                )
            )
        return results

    def _fetch_detail(self, detail_url: str) -> dict:
        response = requests.get(detail_url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        container = soup.select_one(
            "div.board_view, div.board_view01, div.view_cont, div.view_type01"
        )
        if container is None:
            container = soup

        title = None
        for selector in [
            "title",
            "div.board_view h3",
            "div.board_view h4",
            "div#sub_contentsArea2 h3",
        ]:
            node = soup.select_one(selector)
            if node:
                title = normalize_text(node.get_text(" ", strip=True))
                if title:
                    break

        text_chunks = [
            normalize_text(text)
            for text in container.stripped_strings
            if normalize_text(text)
        ]
        body_text = "\n".join(text_chunks)
        posted_at = self._extract_posted_at(body_text)
        period_text = self._extract_period_text(text_chunks)
        if posted_at and period_text == "\uae30\uac04 \ubbf8\uae30\uc7ac":
            period_text = "\uac8c\uc2dc\uc77c %s" % posted_at

        return {
            "title": self._clean_result_title(title or ""),
            "organization": self._extract_organization(text_chunks, body_text),
            "period_text": period_text,
            "posted_at": posted_at,
            "source_url": detail_url,
            "body_excerpt": body_text[:4000],
        }

    def _extract_period_text(self, chunks: List[str]) -> str:
        for chunk in chunks:
            if "\uacf5\uace0\uae30\uac04" in chunk:
                return chunk
            if "\uc811\uc218" in chunk and "~" in chunk:
                return chunk
            if "\uacc4\uc57d\uae30\uac04" in chunk:
                return chunk
        for index, chunk in enumerate(chunks):
            if chunk in {"\ub0a0 \uc9dc", "\ub0a0\uc9dc"} and index + 1 < len(chunks):
                return normalize_text(chunks[index + 1])
        return "\uae30\uac04 \ubbf8\uae30\uc7ac"

    def _extract_organization(self, chunks: List[str], body_text: str) -> str:
        known_keywords = [
            "\uc7ac\ubb34\uad00\ub9ac\ud300",
            "\uad6d\uac00\ub370\uc774\ud130\uc778\ud504\ub77c\ud300",
            "AI\uae30\uc220\uc804\ub7b5\ud300",
            "\ub124\ud2b8\uc6cc\ud06c\uc804\ub7b5\ud300",
            "\ud55c\uad6d\uc9c0\ub2a5\uc815\ubcf4\uc0ac\ud68c\uc9c4\ud765\uc6d0",
            "\uacfc\ud559\uae30\uc220\uc815\ubcf4\ud1b5\uc2e0\ubd80",
        ]
        for keyword in known_keywords:
            if keyword in body_text:
                return keyword
        for chunk in chunks:
            if chunk.endswith("\ud300") or chunk.endswith("\ubcf8\ubd80"):
                return chunk
        return "\ubbf8\uae30\uc7ac"

    def _extract_posted_at(self, text: str) -> str:
        match = re.search(r"(20\d{2}[./-]\d{2}[./-]\d{2})", normalize_text(text or ""))
        if not match:
            return ""
        return match.group(1).replace("-", ".").replace("/", ".")

    def _clean_result_title(self, title: str) -> str:
        cleaned = normalize_text(title or "")
        if "|" in cleaned:
            cleaned = cleaned.split("|")[0].strip()
        if ">" in cleaned:
            cleaned = cleaned.split(">")[-1].strip()
        cleaned = re.sub(
            r"\s*(\ucca8\ubd80\ud30c\uc77c\s*\uc788\uc74c\s*)?(new\s*)?20\d{2}[./-]\d{2}[./-]\d{2}\s+\uc870\ud68c\uc218.*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\s*(\ucca8\ubd80\ud30c\uc77c\s*\uc788\uc74c|new)\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return normalize_text(cleaned)
