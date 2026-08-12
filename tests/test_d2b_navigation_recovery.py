from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.collectors.d2b import D2BCollector, D2B_RETURN_TIMEOUT_MS


class D2BNavigationRecoveryTest(unittest.TestCase):
    def test_return_uses_domcontentloaded_with_bounded_timeout(self) -> None:
        collector = D2BCollector()
        page = MagicMock()

        collector._return_to_result_page(page, "\ud14c\uc2a4\ud2b8")

        page.go_back.assert_called_once_with(
            wait_until="domcontentloaded",
            timeout=D2B_RETURN_TIMEOUT_MS,
        )
        page.wait_for_timeout.assert_called_once_with(500)

    def test_return_reopens_result_page_once_when_back_navigation_fails(self) -> None:
        collector = D2BCollector()
        page = MagicMock()
        page.go_back.side_effect = RuntimeError("navigation timeout")
        collector._search_page = MagicMock()

        collector._return_to_result_page(page, "\ud14c\uc2a4\ud2b8")

        collector._search_page.assert_called_once_with(page, "\ud14c\uc2a4\ud2b8")
        page.wait_for_timeout.assert_not_called()


if __name__ == "__main__":
    unittest.main()
