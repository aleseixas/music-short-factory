import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.assets import AssetManager
from engine.ffmpeg import VideoStreamInfo, probe_audio_duration
from engine.media_cache import download_to_cache
from engine.models import AssetSpec, BackgroundMusicSpec, SfxCue
from engine.music import resolve_background_music
from engine.sfx import resolve_sfx_cues


class FakeResponse:
    def __init__(self, chunks=(), status_code=200, headers=None):
        self.status_code = status_code
        self._chunks = tuple(chunks)
        self.headers = dict(headers or {})
        self.closed = False

    def iter_content(self, chunk_size):
        self.chunk_size = chunk_size
        return iter(self._chunks)

    def close(self):
        self.closed = True


def write_music_catalog(root: Path, entries: list[object]) -> Path:
    music_root = root / "assets" / "audio" / "music"
    music_root.mkdir(parents=True, exist_ok=True)
    (music_root / "catalog.json").write_text(
        json.dumps({"schema_version": 1, "profiles": {"remote": entries}}),
        encoding="utf-8",
    )
    return music_root


def write_sfx_catalog(root: Path, entries: list[object]) -> Path:
    sfx_root = root / "assets" / "audio" / "sfx"
    sfx_root.mkdir(parents=True, exist_ok=True)
    (sfx_root / "catalog.json").write_text(
        json.dumps({"schema_version": 1, "types": {"impact": entries}}),
        encoding="utf-8",
    )
    return sfx_root


class RemoteMediaCacheTests(unittest.TestCase):
    def test_download_is_atomic_and_cache_hit_skips_the_network(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache" / "video"
            response = FakeResponse((b"remote-", b"video"))
            with patch("engine.media_cache.requests.get", return_value=response) as get:
                first = download_to_cache(
                    "https://cdn.example.test/clips/scene.mp4?signature=secret",
                    cache,
                    "clips/scene.mp4",
                    "video de teste",
                    attempts=1,
                )
                second = download_to_cache(
                    "https://cdn.example.test/clips/scene.mp4?signature=secret",
                    cache,
                    "clips/scene.mp4",
                    "video de teste",
                    attempts=1,
                )

            self.assertEqual(first, cache / "clips" / "scene.mp4")
            self.assertEqual(second, first)
            self.assertEqual(first.read_bytes(), b"remote-video")
            self.assertFalse(first.with_name("scene.mp4.part").exists())
            self.assertTrue(response.closed)
            get.assert_called_once()
            user_agent = get.call_args.kwargs["headers"]["User-Agent"]
            self.assertIn("MusicShortFactory/", user_agent)
            self.assertIn("github.com/aleseixas/music-short-factory", user_agent)

    def test_http_429_retry_after_is_respected_and_retry_succeeds(self):
        responses = (
            FakeResponse(status_code=429, headers={"Retry-After": "3"}),
            FakeResponse((b"downloaded-after-retry",)),
        )
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache" / "video"
            with (
                patch(
                    "engine.media_cache.requests.get",
                    side_effect=responses,
                ) as get,
                patch("engine.media_cache._wait_for_host_slot"),
                patch("engine.media_cache._defer_host") as defer_host,
                patch("engine.media_cache.time.sleep") as sleep,
            ):
                result = download_to_cache(
                    "https://upload.wikimedia.org/media/scene.mp4",
                    cache,
                    "scene.mp4",
                    "video remoto",
                    attempts=3,
                )

            self.assertEqual(result.read_bytes(), b"downloaded-after-retry")
            self.assertEqual(get.call_count, 2)
            sleep.assert_called_once_with(3.0)
            defer_host.assert_called_once_with("upload.wikimedia.org", 3.0)

    def test_http_429_without_retry_after_uses_progressive_backoff(self):
        responses = tuple(FakeResponse(status_code=429) for _ in range(3))
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache" / "music"
            with (
                patch(
                    "engine.media_cache.requests.get",
                    side_effect=responses,
                ) as get,
                patch("engine.media_cache._wait_for_host_slot"),
                patch("engine.media_cache._defer_host") as defer_host,
                patch("engine.media_cache.time.sleep") as sleep,
                self.assertRaisesRegex(RuntimeError, "HTTP 429"),
            ):
                download_to_cache(
                    "https://upload.wikimedia.org/audio/track.mp3",
                    cache,
                    "track.mp3",
                    "musica remota",
                    attempts=3,
                )

            self.assertEqual(get.call_count, 3)
            self.assertEqual(
                [call.args for call in sleep.call_args_list],
                [(1.0,), (2.0,)],
            )
            self.assertEqual(
                [call.args for call in defer_host.call_args_list],
                [
                    ("upload.wikimedia.org", 1.0),
                    ("upload.wikimedia.org", 2.0),
                ],
            )

    def test_download_failures_are_clear_and_do_not_leave_partial_files(self):
        cases = (
            (FakeResponse((), 503), "HTTP 503"),
            (FakeResponse(()), "resposta vazia"),
        )
        for response, detail in cases:
            with self.subTest(detail=detail), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory) / "cache" / "music"
                url = "https://cdn.example.test/music/track.mp3?token=private-value"
                with patch("engine.media_cache.requests.get", return_value=response):
                    with self.assertRaisesRegex(RuntimeError, detail) as raised:
                        download_to_cache(
                            url,
                            cache,
                            "track.mp3",
                            "musica remota",
                            attempts=1,
                        )
                self.assertNotIn("private-value", str(raised.exception))
                self.assertFalse((cache / "track.mp3").exists())
                self.assertFalse((cache / "track.mp3.part").exists())

    def test_url_must_point_directly_to_the_expected_file_type(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("engine.media_cache.requests.get") as get,
                self.assertRaisesRegex(RuntimeError, "URL invalida"),
            ):
                download_to_cache(
                    "https://cdn.example.test/download?id=42",
                    Path(directory) / "cache" / "video",
                    "scene.webm",
                    "video remoto",
                    attempts=1,
                )
            get.assert_not_called()


class RemoteVideoAssetTests(unittest.TestCase):
    @staticmethod
    def asset(url="https://cdn.example.test/video/studio.webm") -> AssetSpec:
        return AssetSpec("studio", "studio.webm", url, "", "", 0.5, 0.5)

    def make_manager(self, root: Path) -> AssetManager:
        return AssetManager(
            root / "episode" / "assets",
            root / "work",
            1080,
            1920,
            1,
            allowed_assets_root=root / "episode",
            video_cache_dir=root / "cache" / "video",
        )

    def test_remote_video_uses_video_cache_and_ffprobe(self):
        info = VideoStreamInfo(4.0, 1920, 1080, 29.97)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            with (
                patch(
                    "engine.media_cache.requests.get",
                    return_value=FakeResponse((b"synthetic-webm",)),
                ) as get,
                patch("engine.assets.probe_video_stream", return_value=info) as probe,
            ):
                result = manager.ensure(self.asset())

            self.assertEqual(result, root / "cache" / "video" / "studio.webm")
            self.assertEqual(result.read_bytes(), b"synthetic-webm")
            probe.assert_called_once_with(result)
            get.assert_called_once()

            cached_manager = self.make_manager(root)
            with (
                patch("engine.media_cache.requests.get") as cached_get,
                patch("engine.assets.probe_video_stream", return_value=info) as cached_probe,
            ):
                self.assertEqual(cached_manager.ensure(self.asset()), result)
            cached_get.assert_not_called()
            cached_probe.assert_called_once_with(result)

    def test_existing_episode_video_has_priority_over_url(self):
        info = VideoStreamInfo(2.0, 1080, 1920, 30.0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            local = root / "episode" / "assets" / "studio.webm"
            local.write_bytes(b"local-video")
            with (
                patch("engine.assets.download_to_cache") as download,
                patch("engine.assets.probe_video_stream", return_value=info) as probe,
            ):
                result = manager.ensure(self.asset())
            self.assertEqual(result, local)
            download.assert_not_called()
            probe.assert_called_once_with(local)

    def test_invalid_remote_video_is_rejected_after_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self.make_manager(root)
            with (
                patch(
                    "engine.media_cache.requests.get",
                    return_value=FakeResponse((b"not-a-video",)),
                ),
                patch(
                    "engine.assets.probe_video_stream",
                    side_effect=RuntimeError("stream de video ausente"),
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "Asset de video invalido.*stream de video ausente",
                ),
            ):
                manager.ensure(self.asset())
            self.assertFalse((root / "cache" / "video" / "studio.webm").exists())


class AudioProbeTests(unittest.TestCase):
    def test_audio_probe_requires_stream_and_positive_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "track.mp3"
            audio.write_bytes(b"synthetic")
            with patch(
                "engine.ffmpeg.ffprobe_output",
                return_value=json.dumps(
                    {
                        "streams": [{"codec_type": "audio", "duration": "2.75"}],
                        "format": {"duration": "2.75"},
                    }
                ),
            ):
                self.assertEqual(probe_audio_duration(audio), 2.75)

            with patch(
                "engine.ffmpeg.ffprobe_output",
                return_value=json.dumps(
                    {"streams": [], "format": {"duration": "2.75"}}
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "sem stream de audio"):
                    probe_audio_duration(audio)


class RemoteAudioCatalogTests(unittest.TestCase):
    def test_music_object_uses_local_file_first_then_remote_cache_and_probe(self):
        entry = {
            "file": "uplifting/track-01.mp3",
            "url": "https://cdn.example.test/music/track-01.mp3",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            music_root = write_music_catalog(root, [entry])
            local = music_root / "uplifting" / "track-01.mp3"
            local.parent.mkdir(parents=True)
            local.write_bytes(b"local-music")
            spec = BackgroundMusicSpec("remote", 0.12)

            with (
                patch("engine.media_cache.requests.get") as local_get,
                patch("engine.music.probe_audio_duration") as local_probe,
            ):
                local_result = resolve_background_music(root, spec, "episode", root / "cache")
            self.assertEqual(local_result.path, local)
            local_get.assert_not_called()
            local_probe.assert_not_called()

            local.unlink()
            with (
                patch(
                    "engine.media_cache.requests.get",
                    return_value=FakeResponse((b"remote-music",)),
                ),
                patch(
                    "engine.music.probe_audio_duration", return_value=8.0
                ) as remote_probe,
            ):
                remote_result = resolve_background_music(
                    root,
                    spec,
                    "episode",
                    root / "cache",
                )
            expected = root / "cache" / "music" / "uplifting" / "track-01.mp3"
            self.assertEqual(remote_result.path, expected)
            self.assertEqual(expected.read_bytes(), b"remote-music")
            remote_probe.assert_called_once_with(expected)

    def test_sfx_object_uses_sfx_cache_and_is_probed_without_trim(self):
        entry = {
            "file": "impact/hit.wav",
            "url": "https://cdn.example.test/sfx/hit.wav",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sfx_catalog(root, [entry])
            cue = SfxCue(1.25, "impact", 0.4)
            with (
                patch(
                    "engine.media_cache.requests.get",
                    return_value=FakeResponse((b"remote-sfx",)),
                ),
                patch("engine.sfx.probe_audio_duration", return_value=1.5) as probe,
            ):
                resolved = resolve_sfx_cues(root, (cue,), "episode", root / "cache")

            expected = root / "cache" / "sfx" / "impact" / "hit.wav"
            self.assertEqual(resolved[0].path, expected)
            self.assertEqual(expected.read_bytes(), b"remote-sfx")
            probe.assert_called_once_with(expected)

    def test_invalid_remote_music_is_rejected_after_download(self):
        entry = {
            "file": "broken.mp3",
            "url": "https://cdn.example.test/music/broken.mp3",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_music_catalog(root, [entry])
            with (
                patch(
                    "engine.media_cache.requests.get",
                    return_value=FakeResponse((b"not-audio",)),
                ),
                patch(
                    "engine.music.probe_audio_duration",
                    side_effect=RuntimeError("duracao invalida"),
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "Background music remota invalida.*duracao invalida",
                ),
            ):
                resolve_background_music(
                    root,
                    BackgroundMusicSpec("remote", 0.2),
                    "episode",
                    root / "cache",
                )
            self.assertFalse((root / "cache" / "music" / "broken.mp3").exists())
