from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.models import BackgroundMusicSpec
from engine.music import resolve_background_music


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_FALLBACK_PROFILES = {
    "ambient_calm",
    "bright_fun",
    "dark_cinematic",
    "emotional_piano",
    "energetic_hype",
    "hiphop_groove",
    "latin_pop_uplifting",
    "uplifting_documentary",
}


def _write_catalog(path: Path, profiles: dict[str, list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "profiles": profiles}),
        encoding="utf-8",
    )


class ExpandedMusicFallbackTests(unittest.TestCase):
    def test_reusable_fallback_library_has_at_least_100_tracks(self):
        base = json.loads(
            (PROJECT_ROOT / "assets/audio/music/catalog.json").read_text(encoding="utf-8")
        )["profiles"]
        extra = json.loads(
            (PROJECT_ROOT / "assets/audio/music/fallback_social_extra.json").read_text(
                encoding="utf-8"
            )
        )["profiles"]

        entries: list[object] = []
        for name in LEGACY_FALLBACK_PROFILES:
            entries.extend(base[name])
        for source in (base, extra):
            for name, tracks in source.items():
                if name.startswith("fallback_social_"):
                    entries.extend(tracks)

        files = [
            entry if isinstance(entry, str) else entry["file"]
            for entry in entries
        ]
        self.assertGreaterEqual(len(files), 100)
        self.assertEqual(len(files), len(set(files)))

    def test_supplemental_catalog_is_merged_and_safe_track_is_preferred(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music_root = root / "assets/audio/music"
            _write_catalog(
                music_root / "catalog.json",
                {
                    "fallback_social_dark": [
                        {
                            "file": "external/manual/pixabay/registered.mp3",
                            "url": "https://pixabay.com/music/demo-1/",
                            "content_id_status": "registered",
                        }
                    ]
                },
            )
            _write_catalog(
                music_root / "fallback_social_extra.json",
                {
                    "fallback_social_dark": [
                        {
                            "file": "external/manual/pixabay/safe.mp3",
                            "url": "https://pixabay.com/music/demo-2/",
                            "content_id_status": "explicit_no_content_id",
                        }
                    ]
                },
            )
            resolved_path = root / "cache/music/safe.mp3"

            def materialize(entry, *_args, **_kwargs):
                self.assertEqual(entry.relative_file, "external/manual/pixabay/safe.mp3")
                return resolved_path, True

            with (
                patch("engine.music.materialize_audio_catalog_entry", side_effect=materialize),
                patch("engine.music.probe_audio_duration", return_value=3.0),
            ):
                result = resolve_background_music(
                    root,
                    BackgroundMusicSpec("fallback_social_dark", 0.1),
                    "episode-a",
                )

        self.assertEqual(result.path, resolved_path)

    def test_registered_track_is_deep_fallback_after_safe_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music_root = root / "assets/audio/music"
            _write_catalog(
                music_root / "catalog.json",
                {
                    "fallback_social_dark": [
                        {
                            "file": "external/manual/pixabay/safe.mp3",
                            "url": "https://pixabay.com/music/demo-safe/",
                            "content_id_status": "explicit_no_content_id",
                        },
                        {
                            "file": "external/manual/pixabay/registered.mp3",
                            "url": "https://pixabay.com/music/demo-registered/",
                            "content_id_status": "registered",
                        },
                    ]
                },
            )
            calls: list[str] = []
            registered_path = root / "cache/music/registered.mp3"

            def materialize(entry, *_args, **_kwargs):
                calls.append(entry.relative_file)
                if entry.relative_file.endswith("safe.mp3"):
                    raise RuntimeError("safe source unavailable")
                return registered_path, True

            with (
                patch("engine.music.materialize_audio_catalog_entry", side_effect=materialize),
                patch("engine.music.probe_audio_duration", return_value=3.0),
            ):
                result = resolve_background_music(
                    root,
                    BackgroundMusicSpec("fallback_social_dark", 0.1),
                    "episode-b",
                )

        self.assertEqual(
            calls,
            [
                "external/manual/pixabay/safe.mp3",
                "external/manual/pixabay/registered.mp3",
            ],
        )
        self.assertEqual(result.path, registered_path)


if __name__ == "__main__":
    unittest.main()
