from __future__ import annotations

import hashlib
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


def _expected_first(files: list[str], profile: str, episode_slug: str) -> str:
    ordered = sorted(files, key=str.casefold)
    digest = hashlib.sha256(f"{profile}\0{episode_slug}".encode("utf-8")).digest()
    return ordered[int.from_bytes(digest[:8], "big") % len(ordered)]


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

    def test_supplemental_catalog_is_merged_with_base_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music_root = root / "assets/audio/music"
            profile = "fallback_social_dark"
            base_file = "external/manual/pixabay/base.mp3"
            extra_file = "external/manual/pixabay/extra.mp3"
            _write_catalog(
                music_root / "catalog.json",
                {
                    profile: [
                        {
                            "file": base_file,
                            "url": "https://pixabay.com/music/demo-base/",
                            "content_id_status": "registered",
                        }
                    ]
                },
            )
            _write_catalog(
                music_root / "fallback_social_extra.json",
                {
                    profile: [
                        {
                            "file": extra_file,
                            "url": "https://pixabay.com/music/demo-extra/",
                            "content_id_status": "explicit_no_content_id",
                        }
                    ]
                },
            )
            slug = "episode-a"
            expected = _expected_first([base_file, extra_file], profile, slug)
            resolved_path = root / "cache/music/resolved.mp3"

            def materialize(entry, *_args, **_kwargs):
                self.assertEqual(entry.relative_file, expected)
                return resolved_path, True

            with (
                patch("engine.music.materialize_audio_catalog_entry", side_effect=materialize),
                patch("engine.music.probe_audio_duration", return_value=3.0),
            ):
                result = resolve_background_music(
                    root,
                    BackgroundMusicSpec(profile, 0.1),
                    slug,
                )

        self.assertEqual(result.path, resolved_path)

    def test_content_id_status_does_not_change_selection_order(self):
        profile = "fallback_social_dark"
        slug = "episode-same-order"
        files = [
            "external/manual/pixabay/a.mp3",
            "external/manual/pixabay/b.mp3",
            "external/manual/pixabay/c.mp3",
        ]
        expected = _expected_first(files, profile, slug)
        observed: list[str] = []

        for statuses in (
            ["registered", "explicit_no_content_id", "registered"],
            ["explicit_no_content_id", "registered", "explicit_no_content_id"],
        ):
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                music_root = root / "assets/audio/music"
                _write_catalog(
                    music_root / "catalog.json",
                    {
                        profile: [
                            {
                                "file": file,
                                "url": f"https://pixabay.com/music/demo-{index}/",
                                "content_id_status": status,
                            }
                            for index, (file, status) in enumerate(zip(files, statuses), start=1)
                        ]
                    },
                )
                resolved_path = root / "cache/music/resolved.mp3"

                def materialize(entry, *_args, **_kwargs):
                    observed.append(entry.relative_file)
                    return resolved_path, True

                with (
                    patch(
                        "engine.music.materialize_audio_catalog_entry",
                        side_effect=materialize,
                    ),
                    patch("engine.music.probe_audio_duration", return_value=3.0),
                ):
                    resolve_background_music(
                        root,
                        BackgroundMusicSpec(profile, 0.1),
                        slug,
                    )

        self.assertEqual(observed, [expected, expected])

    def test_failed_track_falls_back_to_next_rotated_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music_root = root / "assets/audio/music"
            profile = "fallback_social_dark"
            files = [
                "external/manual/pixabay/a.mp3",
                "external/manual/pixabay/b.mp3",
            ]
            _write_catalog(
                music_root / "catalog.json",
                {
                    profile: [
                        {
                            "file": file,
                            "url": f"https://pixabay.com/music/demo-{index}/",
                            "content_id_status": (
                                "registered" if index == 1 else "explicit_no_content_id"
                            ),
                        }
                        for index, file in enumerate(files, start=1)
                    ]
                },
            )
            slug = "episode-fallback"
            first = _expected_first(files, profile, slug)
            second = files[1] if first == files[0] else files[0]
            calls: list[str] = []
            resolved_path = root / "cache/music/resolved.mp3"

            def materialize(entry, *_args, **_kwargs):
                calls.append(entry.relative_file)
                if len(calls) == 1:
                    raise RuntimeError("source unavailable")
                return resolved_path, True

            with (
                patch("engine.music.materialize_audio_catalog_entry", side_effect=materialize),
                patch("engine.music.probe_audio_duration", return_value=3.0),
            ):
                result = resolve_background_music(
                    root,
                    BackgroundMusicSpec(profile, 0.1),
                    slug,
                )

        self.assertEqual(calls, [first, second])
        self.assertEqual(result.path, resolved_path)


if __name__ == "__main__":
    unittest.main()
