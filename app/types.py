from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class NoticeCandidate:
    site_code: str
    site_notice_key: str
    title: str
    source_url: str
    organization: Optional[str] = None
    notice_no: Optional[str] = None
    reference_no: Optional[str] = None
    start_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    open_at: Optional[datetime] = None
    period_text: Optional[str] = None
    raw_payload: Dict[str, str] = field(default_factory=dict)
    amount_value: Optional[int] = None
    notice_tag: Optional[str] = None
    priority_score: int = 0


@dataclass
class ManualCommand:
    action: str
    site_code: str = ""
    search_term: str = ""
    channel_id: str = ""
    requested_by: str = ""
    thread_ts: str = ""
