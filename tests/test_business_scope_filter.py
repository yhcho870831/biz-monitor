from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.business_scope_filter import (
    excluded_scope_reason,
    is_excluded_business_notice,
)
from app.types import NoticeCandidate


class BusinessScopeFilterTest(unittest.TestCase):
    def test_landscape_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="1",
            title="\u0032\u0036\ub144 \ubcf8\uc0ac(\ubd84\ub2f9) \uc0ac\uc625 \uc870\uacbd \uc720\uc9c0\uad00\ub9ac \uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uc870\uacbd")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_tree_maintenance_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="2",
            title="\u0032\u0030\u0032\u0036\ub144 \uad70\uccad\uc0ac \ub4f1 \uc218\ubaa9 \uc720\uc9c0\uad00\ub9ac \uc0ac\uc5c5",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uc218\ubaa9")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_disaster_and_waste_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="3",
            title="\u0032\u0030\u0032\u0036\ub144 \ub0a8\ub3d9\uad6c \uc7ac\ub09c\ub300\ube44 \ub3c4\ub85c\uc720\uc9c0\ubcf4\uc218\uacf5\uc0ac \ud3d0\uae30\ubb3c\ucc98\ub9ac\uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\ud3d0\uae30\ubb3c")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_generic_maintenance_notice_is_not_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="4",
            title="\ud574\uc591\uad00\uce21\uc815\ubcf4\uc2dc\uc2a4\ud15c \uc720\uc9c0\uad00\ub9ac \uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertIsNone(excluded_scope_reason(candidate))
        self.assertFalse(is_excluded_business_notice(candidate))

    def test_testbed_notice_is_not_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="5",
            title="\uc5f0\uad6c\uc6a9\uc5ed(\ud574\uc0c1\uac00\ub450\ub9ac \ud14c\uc2a4\ud2b8\ubca0\ub4dc \uad6c\ucd95 \ubc0f \uc2e4\uc99d \uc5f0\uad6c)",
            source_url="https://example.com",
        )

        self.assertIsNone(excluded_scope_reason(candidate))
        self.assertFalse(is_excluded_business_notice(candidate))

    def test_vehicle_keyword_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="6",
            title="\ucc28\ub7c9\uc6a9 \uace0\uc804\uc555 \ud30c\uc6cc\ubaa8\ub4c8 \uc2a4\uc704\uce6d \ud14c\uc2a4\ud2b8\ubca0\ub4dc",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\ucc28\ub7c9")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_building_keyword_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="7",
            title="\uc804\ud1b5 \ubaa9\uc870\uac74\ucd95\ubb3c \uac70\ub3d9\ubd84\uc11d\uc744 \uc704\ud55c \ud14c\uc2a4\ud2b8\ubca0\ub4dc \uc81c\uc791 \ubc29\uc548 \uc5f0\uad6c",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uac74\ucd95")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_ion_keyword_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="8",
            title="\uc18c\ub4d0\uc774\uc628\uc804\uc9c0 \ud575\uc2ec\uc18c\uc7ac \uc0c1\uc6a9\ud654 \uc2e4\uc99d \ud14c\uc2a4\ud2b8\ubca0\ub4dc \uad6c\ucd95 \uc804\ub7b5\uc218\ub9bd \uc5f0\uad6c\uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uc774\uc628")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_disaster_response_road_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="9",
            title="\u0032\u0030\u0032\u0036\ub144 \uc11c\uad6c \ubd81\ubd80\uad8c\uc5ed \uc7ac\ub09c\ub300\ube44 \uae34\uae09 \ub3c4\ub85c\uc720\uc9c0\ubcf4\uc218\uacf5\uc0ac(\uc5f0\uac04\ub2e8\uac00 \u0032\ucc28)",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uc7ac\ub09c\ub300\ube44")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_custom_env_term_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="10",
            title="\uad50\uc721\uc6a9 AI \uc2e4\uc2b5\uc2e4 \uad6c\ucd95 \uc0ac\uc5c5",
            source_url="https://example.com",
        )

        with patch.dict(os.environ, {"EXCLUDED_NOTICE_TERMS": "\uad50\uc721,\uc2e4\uc2b5\uc2e4"}, clear=False):
            self.assertEqual(excluded_scope_reason(candidate), "\uad50\uc721")
            self.assertTrue(is_excluded_business_notice(candidate))

    def test_scope_filter_accepts_legacy_settings_argument(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="legacy",
            title="2026\ub144 \uc0ac\uc625 \uc870\uacbd \uc720\uc9c0\uad00\ub9ac \uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate, settings=object()), "\uc870\uacbd")
        self.assertTrue(is_excluded_business_notice(candidate, settings=object()))

    def test_seed_release_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="11",
            title="[\uc0ac\uc804\uacf5\uace0] 2026\ub144 \ubbfc\uac04\uc5b4\uc5c5\ud611\ub825\uc0ac\uc5c5 \uc218\uc0b0\uc885\uc790 \ub9e4\uc785\ubc29\ub958(\uaf43\uac8c)",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uc218\uc0b0\uc885\uc790")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_ssr_marker_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="12",
            title="\ud658\uacbd\uc801\uc751\ub825 \ud5a5\uc0c1\uc744 \uc704\ud55c \ub0b4\uc218\uba74 \uc591\uc2dd\ud488\uc885\uc758 SSR\uae30\ubc18 \ud2b9\uc774 \ub9c8\ucee4 \uac1c\ubc1c",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\ub0b4\uc218\uba74")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_salvage_lift_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="13",
            title="[\uc0ac\uc804\uacf5\uace0] \ubc31\uc0ac\uc7a5\ud56d \uc18c\ud615\uc5b4\uc120\uc778\uc591\uae30 \uc124\uce58\uc0ac\uc5c5",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uc778\uc591\uae30")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_fire_safety_test_building_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="14",
            title="[\uc0ac\uc804\uacf5\uace0] \uce5c\ud658\uacbd\uc120\ubc15 \ucca8\ub2e8\ud654\uc7ac\uc548\uc804\uc2dc\ud5d8\ub3d9 \uac74\ub9bd\uacf5\uc0ac \uae30\ubcf8 \ubc0f \uc2e4\uc2dc\uc124\uacc4\uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\ud654\uc7ac\uc548\uc804\uc2dc\ud5d8\ub3d9")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_ship_safety_bank_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="15",
            title="[\uc0ac\uc804\uacf5\uace0] \uc120\ubc15\uc548\uc804\uad00\ub9ac\uc0ac \ubb38\uc81c\uc740\ud589\uc2dc\uc2a4\ud15c \uace0\ub3c4\ud654 \uc0ac\uc5c5(\uae34\uae09)",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\ubb38\uc81c\uc740\ud589")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_logistics_transport_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="16",
            title="\ubb3c\ub958 \uc6b4\ubc18 \uc2dc\uc2a4\ud15c \uc6b4\uc601 \uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\ubb3c\ub958 \uc6b4\ubc18")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_water_feature_operation_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="17",
            title="\uc218\uacbd\uc2dc\uc124 \uc6b4\uc601 \ubc0f \uc720\uc9c0\uad00\ub9ac \uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uc218\uacbd\uc2dc\uc124")
        self.assertTrue(is_excluded_business_notice(candidate))

    def test_cleaning_maintenance_notice_is_filtered(self) -> None:
        candidate = NoticeCandidate(
            site_code="g2b",
            site_notice_key="18",
            title="\uccad\uc18c \ubc0f \uc720\uc9c0\uad00\ub9ac \uc6a9\uc5ed",
            source_url="https://example.com",
        )

        self.assertEqual(excluded_scope_reason(candidate), "\uccad\uc18c")
        self.assertTrue(is_excluded_business_notice(candidate))


if __name__ == "__main__":
    unittest.main()
