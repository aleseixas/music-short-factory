import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest

from engine.audio_library import (
    AUDIO_PROVIDER_CIRCUIT_STATE_ENV,
    AudioCatalogEntry,
    AudioProviderUnavailable,
    MAX_EXTERNAL_MUSIC_BYTES,
    PIXABAY_AUDIO_HOSTS,
    _resolve_pixabay_audio_url,
    materialize_audio_catalog_entry,
)


class PixabayMusicTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {AUDIO_PROVIDER_CIRCUIT_STATE_ENV: ""})
        environment.start()
        self.addCleanup(environment.stop)
        state = patch.dict("engine.audio_library._PIXABAY_HTTP_403_FAILURES", {}, clear=True)
        state.start()
        self.addCleanup(state.stop)

    @staticmethod
    def _forbidden_response(*_args, **_kwargs):
        return SimpleNamespace(status_code=403, close=Mock())

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

    def test_repeated_403_opens_circuit_without_requesting_remaining_pages(self):
        with patch(
            "engine.audio_library.requests.get", side_effect=self._forbidden_response
        ) as request:
            for index in range(20):
                expected = RuntimeError if index < 2 else AudioProviderUnavailable
                with self.assertRaises(expected):
                    _resolve_pixabay_audio_url(
                        f"https://pixabay.com/music/track-{index}/", f"profile {index}"
                    )
        self.assertEqual(request.call_count, 2)

    def test_non_403_failures_do_not_disable_provider(self):
        response = SimpleNamespace(status_code=404, close=Mock())
        with patch("engine.audio_library.requests.get", return_value=response) as request:
            for index in range(3):
                with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                    _resolve_pixabay_audio_url(
                        f"https://pixabay.com/music/missing-{index}/", "profile"
                    )
        self.assertEqual(request.call_count, 3)

    def test_circuit_is_shared_with_subprocesses_but_not_another_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "run-1.json"
            with (
                patch.dict(os.environ, {AUDIO_PROVIDER_CIRCUIT_STATE_ENV: str(state_path)}),
                patch(
                    "engine.audio_library.requests.get", side_effect=self._forbidden_response
                ) as request,
            ):
                for _attempt in range(2):
                    with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                        _resolve_pixabay_audio_url("https://pixabay.com/music/a/", "profile")
                # Simulate a new Python process with only the run-scoped file available.
                with patch.dict(
                    "engine.audio_library._PIXABAY_HTTP_403_FAILURES", {}, clear=True
                ):
                    with self.assertRaises(AudioProviderUnavailable):
                        _resolve_pixabay_audio_url("https://pixabay.com/music/b/", "repair")
                self.assertEqual(request.call_count, 2)
                self.assertEqual(
                    json.loads(state_path.read_text(encoding="utf-8")),
                    {"providers": {"pixabay": {"http_403_failures": 2}}},
                )
                with patch.dict(
                    os.environ,
                    {AUDIO_PROVIDER_CIRCUIT_STATE_ENV: str(Path(temp_dir) / "run-2.json")},
                ):
                    with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                        _resolve_pixabay_audio_url("https://pixabay.com/music/a/", "new run")
                self.assertEqual(request.call_count, 3)

    def test_open_circuit_reuses_remote_cache_without_resolving_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cached = root / "cache/external/manual/pixabay/cached.mp3"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"existing audio, caller must still probe")
            entry = AudioCatalogEntry(
                root / "missing.mp3", "external/manual/pixabay/cached.mp3",
                "https://pixabay.com/music/cached-123/",
            )
            with (
                patch.dict("engine.audio_library._PIXABAY_HTTP_403_FAILURES", {"process": 2}),
                patch("engine.audio_library.requests.get") as request,
                patch("engine.audio_library.download_to_cache") as download,
            ):
                result = materialize_audio_catalog_entry(
                    entry, root / "cache", "cached profile", "background music"
                )
            self.assertEqual(result, (cached, True))
            request.assert_not_called()
            download.assert_not_called()

    def test_cdn_403_failures_also_open_provider_circuit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch("engine.audio_library.download_to_cache", side_effect=RuntimeError("HTTP 403")) as download,
            ):
                for index in range(5):
                    entry = AudioCatalogEntry(
                        root / "missing.mp3", f"external/manual/pixabay/track-{index}.mp3",
                        f"https://cdn.pixabay.com/download/audio/track-{index}.mp3",
                    )
                    with self.assertRaises(RuntimeError):
                        materialize_audio_catalog_entry(
                            entry, root / "cache", "profile", "background music"
                        )
            self.assertEqual(download.call_count, 2)

    def test_open_circuit_does_not_block_other_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entry = AudioCatalogEntry(
                root / "missing.mp3", "external/openverse/fresh.mp3",
                "https://cdn.freesound.org/previews/fresh.mp3",
            )
            with (
                patch.dict("engine.audio_library._PIXABAY_HTTP_403_FAILURES", {"process": 2}),
                patch("engine.audio_library.download_to_cache", return_value=root / "fresh.mp3") as download,
            ):
                materialize_audio_catalog_entry(entry, root / "cache", "fresh", "background music")
            download.assert_called_once()

    def test_cached_audio_does_not_bypass_https_or_openverse_host_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative, url in (
                ("external/manual/pixabay/a.mp3", "http://pixabay.com/music/a/"),
                ("external/openverse/b.mp3", "https://pixabay.com/music/b/"),
            ):
                cached = root / "cache" / relative
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(b"cached")
                entry = AudioCatalogEntry(root / "missing.mp3", relative, url)
                with self.assertRaises(RuntimeError):
                    materialize_audio_catalog_entry(
                        entry, root / "cache", "invalid source policy", "background music"
                    )


if __name__ == "__main__":
    unittest.main()
