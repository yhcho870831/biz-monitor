from __future__ import annotations

from typing import List

from app.types import NoticeCandidate


class PartialSearchError(Exception):
    """A multi-stage search finished only partly.

    ``results`` holds candidates from the stages that succeeded so the caller can
    still persist them; ``failures`` names the stages that failed. The caller is
    expected to process ``results`` and then treat the job as failed so the
    failed stage is retried.
    """

    def __init__(self, results: List[NoticeCandidate], failures: dict[str, Exception]):
        self.results = list(results)
        self.failures = dict(failures)
        detail = "; ".join(f"{stage}: {exc}" for stage, exc in self.failures.items())
        super().__init__(f"partial search ({len(self.results)} results kept) failed stages: {detail}")


class BaseCollector:
    site_code = ""

    def search(self, term: str) -> List[NoticeCandidate]:
        raise NotImplementedError
