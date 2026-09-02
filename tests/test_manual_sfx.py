from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest

from engine.audio_library import (
    AudioCatalogEntry,
    MAX_EXTERNAL_SFX_BYTES,
    _resolve_myinstants_audio_url,
    materialize_audio_catalog_entry,
)
from engine.models import SfxCue
from engine.sfx import _validate_trim_values


class ManualSfxTests(unittest.TestCase):
    def test_myinstants_api_resolves_direct_mp3_without_opening_page(self):
        api_response = SimpleNamespace(
            status_code=200,
            json=Mock(
                return_value={
                    "status": 200,
                    "data": {
                        "id": "demo-123",
                        "mp3": "https://www.myinstants.com/media/sounds/demo-effect.mp3",
                    },
                }
            ),
            close=Mock(),
        )
        with patch(
            "engine.audio_library.requests.get",
            return_value=api_response,
        ) as get:
            resolved = _resolve_myinstants_audio_url(
                "https://www.myinstants.com/en/instant/demo-123/",
                "type 'demo'",
            )

        self.assertEqual(
            resolved,
            "https://www.myinstants.com/media/sounds/demo-effect.mp3",
        )
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["params"], {"id": "demo-123"})
        api_response.close.assert_called_once_with()

    def test_myinstants_page_is_fallback_when_api_fails(self):
        api_response = SimpleNamespace(
            status_code=503,
            close=Mock(),
        )
        page_response = SimpleNamespace(
            status_code=200,
            url="https://www.myinstants.com/en/instant/demo-123/",
            text=(
                '<html><body><a href="/media/sounds/demo-effect.mp3">'
                "Download MP3</a></body></html>"
            ),
            close=Mock(),
        )
        with patch(
            "engine.audio_library.requests.get",
            side_effect=[api_response, page_response],
        ) as get:
            resolved = _resolve_myinstants_audio_url(
                "https://www.myinstants.com/en/instant/demo-123/",
                "type 'demo'",
            )

        self.assertEqual(
            resolved,
            "https://www.myinstants.com/media/sounds/demo-effect.mp3",
        )
        self.assertEqual(get.call_count, 2)
        api_response.close.assert_called_once_with()
        page_response.close.assert_called_once_with()

    def test_manual_sfx_uses_https_and_25mb_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entry = AudioCatalogEntry(
                local_path=root / "missing.mp3",
                relative_file="external/manual/impacts/demo.mp3",
                url="https://www.myinstants.com/en/instant/demo-123/",
            )
            downloaded = root / "cache" / "demo.mp3"
            with (
                patch(
                    "engine.audio_library._resolve_manual_audio_url",
                    return_value="https://www.myinstants.com/media/sounds/demo.mp3",
                ),
                patch(
                    "engine.audio_library.download_to_cache",
                    return_value=downloaded,
                ) as download,
            ):
                result = materialize_audio_catalog_entry(
                    entry,
                    root / "cache",
                    "type 'demo'",
                    "SFX",
                )

        self.assertEqual(result, (downloaded, True))
        download.assert_called_once_with(
            "https://www.myinstants.com/media/sounds/demo.mp3",
            root / "cache",
            "external/manual/impacts/demo.mp3",
            "SFX 'external/manual/impacts/demo.mp3'",
            require_https=True,
            max_bytes=MAX_EXTERNAL_SFX_BYTES,
        )

    def test_brazilian_meme_must_play_in_full(self):
        full = SfxCue(0.2, "meme_br_errou_faustao", 0.3)
        _validate_trim_values(full, 1)

        trimmed_start = SfxCue(
            0.2,
            "meme_br_errou_faustao",
            0.3,
            0.1,
            None,
        )
        with self.assertRaisesRegex(RuntimeError, "meme completo"):
            _validate_trim_values(trimmed_start, 1)

        trimmed_duration = SfxCue(
            0.2,
            "meme_br_errou_faustao",
            0.3,
            0.0,
            0.5,
        )
        with self.assertRaisesRegex(RuntimeError, "meme completo"):
            _validate_trim_values(trimmed_duration, 1)


if __name__ == "__main__":
    unittest.main()
