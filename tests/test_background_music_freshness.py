from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from check_episode_media import _assert_background_is_fresh
from engine.models import BackgroundMusicSpec


class BackgroundMusicFreshnessTests(unittest.TestCase):
    @staticmethod
    def _write_catalog(root: Path, old_url: str, current_url: str) -> None:
        music_root = root / "assets" / "audio" / "music"
        music_root.mkdir(parents=True, exist_ok=True)
        (music_root / "catalog.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profiles": {
                        "old_profile": [
                            {
                                "file": "external/openverse/old-alias.ogg",
                                "url": old_url,
                            }
                        ],
                        "current_profile": [
                            {
                                "file": "external/openverse/current-alias.ogg",
                                "url": current_url,
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _write_old_timeline(root: Path) -> None:
        episode_dir = root / "episodes" / "old_episode"
        episode_dir.mkdir(parents=True, exist_ok=True)
        (episode_dir / "timeline.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "background_music": {"profile": "old_profile", "volume": 0.1},
                    "shots": [],
                }
            ),
            encoding="utf-8",
        )

    def test_same_underlying_url_is_blocked_even_with_different_profile_and_file(self):
        same_url = "https://upload.wikimedia.org/wikipedia/commons/a/a1/same.ogg"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_catalog(root, same_url + "?old=1", same_url + "?new=2")
            self._write_old_timeline(root)
            cache_dir = root / "cache"
            resolved = cache_dir / "music" / "external" / "openverse" / "current-alias.ogg"

            with patch(
                "check_episode_media._recent_episode_slugs",
                return_value=["old_episode"],
            ):
                with self.assertRaisesRegex(RuntimeError, "BACKGROUND_REUSE_BLOCKED"):
                    _assert_background_is_fresh(
                        root,
                        Path("episodes"),
                        cache_dir,
                        "current_episode",
                        BackgroundMusicSpec("current_profile", 0.1),
                        resolved,
                    )

    def test_different_track_passes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_catalog(
                root,
                "https://upload.wikimedia.org/wikipedia/commons/a/a1/old.ogg",
                "https://upload.wikimedia.org/wikipedia/commons/b/b2/new.ogg",
            )
            self._write_old_timeline(root)
            cache_dir = root / "cache"
            resolved = cache_dir / "music" / "external" / "openverse" / "current-alias.ogg"

            with patch(
                "check_episode_media._recent_episode_slugs",
                return_value=["old_episode"],
            ):
                _assert_background_is_fresh(
                    root,
                    Path("episodes"),
                    cache_dir,
                    "current_episode",
                    BackgroundMusicSpec("current_profile", 0.1),
                    resolved,
                )


if __name__ == "__main__":
    unittest.main()
