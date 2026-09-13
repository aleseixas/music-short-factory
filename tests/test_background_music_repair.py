from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.repair_background_music as repair


class BackgroundMusicRepairTests(unittest.TestCase):
    def test_profile_order_keeps_external_before_eligible_repo_fallback(self):
        profiles = {
            "ambient_calm": ["ambient_calm/old.mp3"],
            "latin_pop_uplifting": ["latin_pop_uplifting/fresh.mp3"],
            "external_selected": [{"file": "old.mp3", "url": "https://example.com/old.mp3"}],
            "fallback_social_dark": [{"file": "a.mp3", "url": "https://pixabay.com/music/a/"}],
            "external_other": [{"file": "b.mp3", "url": "https://cdn.freesound.org/b.mp3"}],
        }
        with patch.object(repair, "_load_music_profiles", return_value=profiles):
            order = repair._all_profile_order(Path("/tmp/project"), "external_selected")
        self.assertLess(order.index("fallback_social_dark"), order.index("ambient_calm"))
        self.assertLess(order.index("external_other"), order.index("latin_pop_uplifting"))
        self.assertNotIn("external_selected", order)

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

    def test_403_circuit_across_profiles_still_rejects_recent_repo_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music_root = root / "assets/audio/music"
            music_root.mkdir(parents=True)
            profiles = {
                "selected_external": [{"file": "selected.mp3", "url": "https://pixabay.com/music/selected/"}],
                "ambient_calm": ["ambient_calm/recent.mp3"],
                "latin_pop_uplifting": ["latin_pop_uplifting/fresh.mp3"],
            }
            for profile_number in range(3):
                profiles[f"fallback_social_{profile_number}"] = [
                    {
                        "file": f"external/manual/pixabay/track-{profile_number}-{index}.mp3",
                        "url": f"https://pixabay.com/music/track-{profile_number}-{index}/",
                    }
                    for index in range(10)
                ]
            (music_root / "catalog.json").write_text(
                json.dumps({"schema_version": 1, "profiles": profiles}), encoding="utf-8"
            )
            for file in ("ambient_calm/recent.mp3", "latin_pop_uplifting/fresh.mp3"):
                local = music_root / file
                local.parent.mkdir(parents=True)
                local.write_bytes(b"existing local catalog fixture")
            for slug, profile in (("old_episode", "ambient_calm"), ("same_slug", "selected_external")):
                episode = root / "episodes" / slug
                episode.mkdir(parents=True)
                (episode / "timeline.json").write_text(
                    json.dumps({"background_music": {"profile": profile, "volume": 0.1}}),
                    encoding="utf-8",
                )
            with (
                patch.dict(os.environ, {"AUDIO_PROVIDER_CIRCUIT_STATE": ""}),
                patch.dict("engine.audio_library._PIXABAY_HTTP_403_FAILURES", {}, clear=True),
                patch.object(repair, "PROJECT_ROOT", root),
                patch.object(repair, "load_project_config", return_value=SimpleNamespace(paths=SimpleNamespace(episodes_dir="episodes"))),
                patch.object(repair, "_recent_episode_slugs", return_value=["old_episode"]),
                patch("sys.argv", ["repair_background_music.py", "same_slug"]),
                patch("engine.audio_library.requests.get", return_value=SimpleNamespace(status_code=403, close=Mock())) as request,
            ):
                self.assertEqual(repair.main(), 0)
            result = json.loads((root / "episodes/same_slug/timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(result["background_music"]["profile"], "latin_pop_uplifting")
            self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
