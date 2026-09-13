import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.audio_library import AudioCatalogEntry
from engine.models import BackgroundMusicSpec
from engine.music import resolve_background_music


class MusicProfileResilienceTests(unittest.TestCase):
    def test_open_pixabay_circuit_probes_cached_audio_and_discards_invalid_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cached = root / "cache/music/external/manual/pixabay/invalid.mp3"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"invalid cached audio")
            local = root / "assets/audio/music/local/fresh.mp3"
            local.parent.mkdir(parents=True)
            local.write_bytes(b"known local catalog fixture")
            candidates = (
                AudioCatalogEntry(
                    root / "missing.mp3", "external/manual/pixabay/invalid.mp3",
                    "https://pixabay.com/music/cached/",
                ),
                AudioCatalogEntry(local, "local/fresh.mp3", None),
            )
            with (
                patch.dict(os.environ, {"AUDIO_PROVIDER_CIRCUIT_STATE": ""}),
                patch.dict("engine.audio_library._PIXABAY_HTTP_403_FAILURES", {"process": 2}, clear=True),
                patch("engine.music.background_music_candidates", return_value=candidates),
                patch("engine.music.probe_audio_duration", side_effect=RuntimeError("invalid audio")) as probe,
                patch("engine.audio_library.requests.get") as request,
            ):
                result = resolve_background_music(root, BackgroundMusicSpec("mixed", 0.1), "same_slug")
            probe.assert_called_once_with(cached)
            request.assert_not_called()
            self.assertFalse(cached.exists())
            self.assertEqual(result.path, local)

    def _write_remote_profile(self, root: Path) -> None:
        music_root = root / "assets" / "audio" / "music"
        music_root.mkdir(parents=True, exist_ok=True)
        (music_root / "catalog.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profiles": {
                        "fallback_social_test": [
                            {
                                "file": "external/manual/pixabay/a.mp3",
                                "url": "https://pixabay.com/music/a-1/",
                            },
                            {
                                "file": "external/manual/pixabay/b.mp3",
                                "url": "https://pixabay.com/music/b-2/",
                            },
                            {
                                "file": "external/manual/pixabay/c.mp3",
                                "url": "https://pixabay.com/music/c-3/",
                            },
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_broken_selected_track_rotates_to_next_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_remote_profile(root)
            recovered = root / "cache" / "music" / "recovered.mp3"
            recovered.parent.mkdir(parents=True, exist_ok=True)
            recovered.write_bytes(b"fixture")

            with (
                patch(
                    "engine.music.materialize_audio_catalog_entry",
                    side_effect=[RuntimeError("first source unavailable"), (recovered, True)],
                ) as materialize,
                patch("engine.music.probe_audio_duration", return_value=2.0),
            ):
                result = resolve_background_music(
                    root,
                    BackgroundMusicSpec("fallback_social_test", 0.1),
                    "demo_episode",
                )

        self.assertEqual(result.path, recovered)
        self.assertEqual(result.profile, "fallback_social_test")
        self.assertEqual(materialize.call_count, 2)

    def test_all_broken_tracks_report_combined_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_remote_profile(root)

            with patch(
                "engine.music.materialize_audio_catalog_entry",
                side_effect=RuntimeError("source unavailable"),
            ) as materialize:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Nenhuma faixa do profile de background music",
                ) as context:
                    resolve_background_music(
                        root,
                        BackgroundMusicSpec("fallback_social_test", 0.1),
                        "demo_episode",
                    )

        self.assertEqual(materialize.call_count, 3)
        self.assertIn("source unavailable", str(context.exception))


if __name__ == "__main__":
    unittest.main()
