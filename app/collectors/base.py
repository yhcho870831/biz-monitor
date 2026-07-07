from __future__ import annotations

from typing import List

from app.types import NoticeCandidate


class BaseCollector:
    site_code = ""

    def search(self, term: str) -> List[NoticeCandidate]:
        raise NotImplementedError
