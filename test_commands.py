from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.commands import parse_manual_command


class ParseManualCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = SimpleNamespace(
            slack_command_prefix="\uacf5\uace0:",
            slack_command_suffix="\ucd94\uac00",
        )

    def test_parse_add_command(self) -> None:
        command = parse_manual_command(
            "<@U123> \uacf5\uace0:\uae30\uc0c1\uc0b0\uc5c5\uae30\uc220\uc6d0/\uae30\uc0c1 \ucd94\uac00",
            channel_id="C123",
            requested_by="U123",
            settings=self.settings,
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.action, "add")
        self.assertEqual(command.site_code, "kmiti")
        self.assertEqual(command.search_term, "\uae30\uc0c1")

    def test_parse_delete_command(self) -> None:
        command = parse_manual_command(
            "<@U123> \uacf5\uace0:KIMST/\uc544\ucfe0\uc544\ud3ec\ub2c9\uc2a4 \uc0ad\uc81c",
            channel_id="C123",
            requested_by="U123",
            settings=self.settings,
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.action, "delete")
        self.assertEqual(command.site_code, "kimst")
        self.assertEqual(command.search_term, "\uc544\ucfe0\uc544\ud3ec\ub2c9\uc2a4")

    def test_parse_keyword_list_command(self) -> None:
        command = parse_manual_command(
            "<@U123> \uac80\uc0c9\uc5b4 \ubcf4\uc5ec\uc918",
            channel_id="C123",
            requested_by="U123",
            settings=self.settings,
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.action, "list_keywords")

    def test_parse_notice_list_command(self) -> None:
        command = parse_manual_command(
            "<@U123> \uacf5\uace0\ub9ac\uc2a4\ud2b8",
            channel_id="C123",
            requested_by="U123",
            settings=self.settings,
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.action, "list_notices")

    def test_parse_calendar_link_command(self) -> None:
        command = parse_manual_command(
            "<@U123> \uc77c\uc815\ud45c",
            channel_id="C123",
            requested_by="U123",
            settings=self.settings,
        )

        self.assertIsNotNone(command)
        self.assertEqual(command.action, "calendar_link")


if __name__ == "__main__":
    unittest.main()
