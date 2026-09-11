from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.repair_background_music as repair


class BackgroundMusicRepairTests(unittest.TestCase):
    def test_profile_order_includes_external_catalog_profiles(self):
        profiles = {
            "ambient_calm": [object()],
            "dark_cinematic": [object()],
            "external_fresh_track": [object()],
            "fallback_social_modern": [object()],
        }
        with patch.object(repair, "_load_music_profiles", return_value=profiles):
            order = repair._all_profile_order(Path("/tmp/project"), "ambient_calm")

        self.assertIn("external_fresh_track", order)
        self.assertIn("fallback_social_modern", order)
        self.assertNotIn("ambient_calm", order)

    def test_profile_order_does_not_stop_at_local_profiles(self):
        profiles = {
            name: [object()]
            for name in repair.LOCAL_PROFILE_ORDER
        }
        profiles["external_last_resort"] = [object()]

        with patch.object(repair, "_load_music_profiles", return_value=profiles):
            order = repair._all_profile_order(Path("/tmp/project"), "uplifting_documentary")

        self.assertIn("external_last_resort", order)
        self.assertEqual(len(order), len(profiles) - 1)


if __name__ == "__main__":
    unittest.main()
