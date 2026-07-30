from __future__ import annotations

import html
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from app.collectors.playwright_utils import browser_page
from app.config import Settings
from app.types import NoticeCandidate
from app.utils import normalize_text


ATTACHMENT_PRIORITIES: list[tuple[str, int, tuple[str, ...]]] = [
    ("proposal_request", 1, ("제안요청서",)),
    ("notice_document", 2, ("공고문", "입찰공고문")),
    ("statement_of_work", 3, ("과업지시서",)),
    ("purchase_request", 4, ("구매요구서",)),
]

ATTACHMENT_CATEGORY_LABELS = {
    "proposal_request": "제안요청서",
    "notice_document": "공고문",
    "statement_of_work": "과업지시서",
    "purchase_request": "구매요구서",
    "other": "기타첨부",
}

G2B_DETAIL_URL_TEMPLATE = "https://www.g2b.go.kr/pn/pnp/pnpe/BidPbac/selectBidPbancDetail.do?bidPbancNo=%s"
G2B_DETAIL_READY_TIMEOUT_MS = 4000
G2B_DETAIL_RETRY_COUNT = 2


@dataclass
class DownloadedAttachment:
    attachment_name: str
    attachment_category: str
    priority_rank: int
    stored_path: str
    source_url: str | None
    mime_type: str | None
    file_size: int | None


def attachment_category_label(category: str) -> str:
    return ATTACHMENT_CATEGORY_LABELS.get(category, ATTACHMENT_CATEGORY_LABELS["other"])


def should_collect_attachments(candidate: NoticeCandidate) -> bool:
    if (candidate.raw_payload or {}).get("announcement_stage") in {
        "pre_announcement",
        "procurement_plan",
        "pre_specification",
    }:
        return False
    return candidate.site_code in {"g2b", "d2b"} and candidate.priority_score >= 1


def build_attachment_storage_dir(settings: Settings, candidate: NoticeCandidate) -> Path:
    notice_key = re.sub(r"[^0-9A-Za-z._-]+", "_", candidate.site_notice_key or candidate.title).strip("._-")
    safe_key = notice_key[:120] or candidate.site_code
    target = Path(settings.download_dir) / "attachments" / candidate.site_code / safe_key
    target.mkdir(parents=True, exist_ok=True)
    return target


def _rank_attachment_name(name: str) -> tuple[str, int]:
    normalized_name = normalize_text(name).lower()
    category = "other"
    priority_rank = 99
    for candidate_category, rank, keywords in ATTACHMENT_PRIORITIES:
        if any(normalize_text(keyword).lower() in normalized_name for keyword in keywords):
            category = candidate_category
            priority_rank = rank
            break
    return category, priority_rank


def list_priority_attachment_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    ranked: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for link in links:
        name = normalize_text(link.get("text", ""))
        href = (link.get("href") or "").strip()
        if not name or not href:
            continue

        category, priority_rank = _rank_attachment_name(name)
        dedupe_key = name.lower()
        if dedupe_key in seen_names:
            continue
        seen_names.add(dedupe_key)
        ranked.append(
            {
                "text": name,
                "href": href,
                "category": category,
                "priority_rank": priority_rank,
            }
        )

    ranked.sort(key=lambda item: (item["priority_rank"], item["text"]))
    return ranked


def list_priority_attachment_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    seen_names: set[str] = set()

    for row in rows:
        name = normalize_text(html.unescape(str(row.get("text") or "")))
        if not name:
            continue

        category, priority_rank = _rank_attachment_name(name)
        dedupe_key = name.lower()
        if dedupe_key in seen_names:
            continue
        seen_names.add(dedupe_key)
        ranked.append(
            {
                "text": name,
                "row_index": int(row.get("row_index", 0)),
                "category": category,
                "priority_rank": priority_rank,
            }
        )

    ranked.sort(key=lambda item: (int(item["priority_rank"]), str(item["text"])))
    return ranked


def _save_download(download, target_dir: Path, suggested_name: str | None = None) -> tuple[str, int | None, str | None]:
    raw_name = suggested_name or download.suggested_filename or "attachment.bin"
    safe_name = re.sub(r"[^0-9A-Za-z._\-가-힣() ]+", "_", raw_name).strip("._ ") or "attachment.bin"
    target_path = target_dir / safe_name
    if target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        counter = 2
        while True:
            candidate = target_dir / f"{stem}-{counter}{suffix}"
            if not candidate.exists():
                target_path = candidate
                break
            counter += 1
    download.save_as(str(target_path))
    mime_type = mimetypes.guess_type(str(target_path))[0]
    file_size = target_path.stat().st_size if target_path.exists() else None
    return str(target_path), file_size, mime_type


def _close_g2b_popup(page) -> None:
    close_button = page.locator(
        "#mf_wfm_container_wq_uuid_925_wq_uuid_932_poupR23AB00000134241_wframe_popupCnts_btnClose"
    )
    if close_button.count():
        close_button.first.click(force=True)
        page.wait_for_timeout(800)


def _resolve_g2b_detail_url(candidate: NoticeCandidate) -> str:
    source_url = str(candidate.source_url or "").strip()
    if source_url:
        return source_url

    notice_no = normalize_text(candidate.notice_no or "").strip()
    if notice_no:
        return G2B_DETAIL_URL_TEMPLATE % notice_no
    return ""


def _read_g2b_attachment_matches(page) -> list[dict[str, object]]:
    attachment_script = """() => {
        const prefixes = Object.keys(window)
            .filter((key) => /_dlUntyAtchFileL$/.test(key))
            .map((key) => key.replace(/_dlUntyAtchFileL$/, ''));

        const matches = [];
        for (const prefix of prefixes) {
            const dataset = window[prefix + '_dlUntyAtchFileL'];
            const button = window[prefix + '_btnFileDown'];
            if (!dataset || !button || typeof dataset.getRowCount !== 'function') {
                continue;
            }
            const count = dataset.getRowCount();
            if (!count) {
                continue;
            }
            const rows = [];
            for (let index = 0; index < count; index += 1) {
                const row = dataset.getRowJSON(index) || {};
                rows.push({
                    row_index: index,
                    text: row.orgnlAtchFileNm || row.atchFileNm || '',
                });
            }
            matches.push({ prefix, rows });
        }
        return matches;
    }"""

    matches: list[dict[str, object]] = []
    try:
        page.wait_for_function(
            """() => Object.keys(window).some((key) => /_dlUntyAtchFileL$/.test(key))""",
            timeout=G2B_DETAIL_READY_TIMEOUT_MS,
        )
    except Exception:
        pass

    for attempt in range(1, G2B_DETAIL_RETRY_COUNT + 1):
        result = page.evaluate(attachment_script)
        if isinstance(result, list):
            matches = result
        if matches:
            return matches
        if attempt < G2B_DETAIL_RETRY_COUNT:
            page.wait_for_timeout(1500)
    return matches


def download_priority_attachments(candidate: NoticeCandidate, settings: Settings) -> list[DownloadedAttachment]:
    if not should_collect_attachments(candidate):
        return []
    if candidate.site_code == "d2b":
        return download_d2b_attachments(candidate, settings)
    if candidate.site_code == "g2b":
        return download_g2b_attachments(candidate, settings)
    return []


def download_d2b_attachments(candidate: NoticeCandidate, settings: Settings) -> list[DownloadedAttachment]:
    target_dir = build_attachment_storage_dir(settings, candidate)
    attachments: list[DownloadedAttachment] = []

    with browser_page(accept_downloads=True) as page:
        page.goto("https://www.d2b.go.kr/psb/bid/serviceBidAnnounceList.do?key=137", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        page.locator("#chgDate3").click()
        page.locator("#anmt_name").fill(normalize_text(candidate.title))
        page.locator("#btn_search").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1200)

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
            return []

        target_row.locator("a[href='#none;']").first.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1200)

        for tab_text in ("첨부파일", "첨부 파일"):
            tab = page.get_by_text(tab_text, exact=False).first
            if tab.count():
                tab.click()
                page.wait_for_timeout(600)
                break

        raw_links = page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href*=\"downloadSes.do\"]')).map(a => ({href: a.getAttribute('href') || '', text: (a.textContent || '').trim()}))"
        )
        for item in list_priority_attachment_links(raw_links):
            href = urljoin(page.url, item["href"])
            raw_href = item["href"]
            anchor = page.locator(f'a[href=\"{raw_href}\"]').first
            with page.expect_download(timeout=15000) as download_info:
                anchor.click()
            download = download_info.value
            stored_path, file_size, mime_type = _save_download(download, target_dir, item["text"])
            attachments.append(
                DownloadedAttachment(
                    attachment_name=item["text"],
                    attachment_category=item["category"],
                    priority_rank=item["priority_rank"],
                    stored_path=stored_path,
                    source_url=href,
                    mime_type=mime_type,
                    file_size=file_size,
                )
            )

    return attachments


def download_g2b_attachments(candidate: NoticeCandidate, settings: Settings) -> list[DownloadedAttachment]:
    target_dir = build_attachment_storage_dir(settings, candidate)
    attachments: list[DownloadedAttachment] = []
    detail_url = _resolve_g2b_detail_url(candidate)
    if not detail_url:
        return attachments

    with browser_page(accept_downloads=True) as page:
        page.goto(detail_url, wait_until="domcontentloaded")
        _close_g2b_popup(page)
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        prefix = None
        rows: list[dict[str, object]] = []
        matches = _read_g2b_attachment_matches(page)
        best_score: tuple[int, int] | None = None
        for match in matches:
            candidate_rows = match.get("rows", [])
            ranked_rows = list_priority_attachment_rows(candidate_rows)
            best_rank = min((int(item["priority_rank"]) for item in ranked_rows), default=99)
            score = (best_rank, -len(candidate_rows))
            if best_score is None or score < best_score:
                best_score = score
                prefix = match.get("prefix")
                rows = candidate_rows
        if not prefix or not rows:
            return []

        for item in list_priority_attachment_rows(rows):
            page.evaluate(
                """({ prefix, rowIndex }) => {
                    const dataset = window[prefix + '_dlUntyAtchFileL'];
                    for (let index = 0; index < dataset.getRowCount(); index += 1) {
                        dataset.setCellData(index, 'CHK', '');
                    }
                    dataset.setCellData(rowIndex, 'CHK', '1');
                }""",
                {"prefix": prefix, "rowIndex": int(item["row_index"])},
            )
            page.wait_for_timeout(300)

            with page.expect_download(timeout=15000) as download_info:
                page.evaluate(
                    """(prefix) => {
                        const button = window[prefix + '_btnFileDown'];
                        if (button && typeof button.trigger === 'function') {
                            button.trigger('click');
                            return;
                        }
                        if (button && typeof button.click === 'function') {
                            button.click();
                            return;
                        }
                        const element = document.getElementById(prefix + '_btnFileDown');
                        if (element) {
                            element.click();
                        }
                    }""",
                    prefix,
                )

            download = download_info.value
            stored_path, file_size, mime_type = _save_download(
                download,
                target_dir,
                str(item["text"]),
            )
            attachments.append(
                DownloadedAttachment(
                    attachment_name=str(item["text"]),
                    attachment_category=str(item["category"]),
                    priority_rank=int(item["priority_rank"]),
                    stored_path=stored_path,
                    source_url=download.url or detail_url,
                    mime_type=mime_type,
                    file_size=file_size,
                )
            )

    return attachments
