from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest

from engine.audio_library import (
    AudioCatalogEntry,
    MAX_EXTERNAL_MUSIC_BYTES,
    PIXABAY_AUDIO_HOSTS,
    _resolve_pixabay_audio_url,
    materialize_audio_catalog_entry,
)


class PixabayMusicTests(unittest.TestCase):
    def test_pixabay_page_resolves_audio_tag_mp3(self):
        response = SimpleNamespace(
            status_code=200,
            url="https://pixabay.com/music/ambient-demo-123/",
            text=(
                '<html><body><audio src="https://cdn.pixabay.com/download/audio/'
                '2026/01/01/demo.mp3?filename=demo.mp3"></audio></body></html>'
            ),
            close=Mock(),
        )
        with patch("engine.audio_library.requests.get", return_value=response):
            resolved = _resolve_pixabay_audio_url(
                "https://pixabay.com/music/ambient-demo-123/",
                "profile 'demo'",
            )

        self.assertEqual(
            resolved,
            "https://cdn.pixabay.com/download/audio/2026/01/01/demo.mp3?filename=demo.mp3",
        )
        response.close.assert_called_once_with()

    def test_pixabay_page_resolves_escaped_script_mp3(self):
        response = SimpleNamespace(
            status_code=200,
            url="https://www.pixabay.com/music/beats-demo-456/",
            text=(
                '<script>{"audio":"https:\\/\\/cdn.pixabay.com\\/download\\/audio\\/'
                '2026\\/02\\/02\\/beat.mp3?filename=beat.mp3\\u0026download=1"}</script>'
            ),
            close=Mock(),
        )
        with patch("engine.audio_library.requests.get", return_value=response):
            resolved = _resolve_pixabay_audio_url(
                "https://www.pixabay.com/music/beats-demo-456/",
                "profile 'demo'",
            )

        self.assertEqual(
            resolved,
            "https://cdn.pixabay.com/download/audio/2026/02/02/beat.mp3?filename=beat.mp3&download=1",
        )
        response.close.assert_called_once_with()

    def test_pixabay_page_rejects_non_pixabay_audio_host(self):
        response = SimpleNamespace(
            status_code=200,
            url="https://pixabay.com/music/ambient-demo-123/",
            text='<audio src="https://example.com/demo.mp3"></audio>',
            close=Mock(),
        )
        with (
            patch("engine.audio_library.requests.get", return_value=response),
            self.assertRaisesRegex(RuntimeError, "sem link MP3 direto"),
        ):
            _resolve_pixabay_audio_url(
                "https://pixabay.com/music/ambient-demo-123/",
                "profile 'demo'",
            )

        response.close.assert_called_once_with()

    def test_pixabay_music_uses_cdn_allowlist_and_100mb_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entry = AudioCatalogEntry(
                local_path=root / "missing.mp3",
                relative_file="external/manual/pixabay/demo.mp3",
                url="https://pixabay.com/music/ambient-demo-123/",
            )
            downloaded = root / "cache" / "demo.mp3"
            with (
                patch(
                    "engine.audio_library._resolve_manual_audio_url",
                    return_value="https://cdn.pixabay.com/download/audio/demo.mp3",
                ),
                patch(
                    "engine.audio_library.download_to_cache",
                    return_value=downloaded,
                ) as download,
            ):
                result = materialize_audio_catalog_entry(
                    entry,
                    root / "cache",
                    "profile 'demo'",
                    "background music",
                )

        self.assertEqual(result, (downloaded, True))
        download.assert_called_once_with(
            "https://cdn.pixabay.com/download/audio/demo.mp3",
            root / "cache",
            "external/manual/pixabay/demo.mp3",
            "background music 'external/manual/pixabay/demo.mp3'",
            require_https=True,
            max_bytes=MAX_EXTERNAL_MUSIC_BYTES,
            allowed_hosts=PIXABAY_AUDIO_HOSTS,
        )


if __name__ == "__main__":
    unittest.main()
