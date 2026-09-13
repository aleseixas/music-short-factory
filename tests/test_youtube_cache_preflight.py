from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.assets import AssetManager
from engine.ffmpeg import VideoStreamInfo
from engine.media_cache import download_to_cache
from engine.models import AssetSpec
from engine.visual_search import VisualSearchError, VisualSearchResult
import engine.visual_search_web as web
from scripts import test_youtube_auth as auth_probe
from tests.test_remote_media_cache import FakeResponse


class FakeDownloadError(Exception):
    pass


class FakeYoutubeDL:
    payload = b"video-payload" * 128
    failure: str | None = None
    _download_retcode = 0

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def extract_info(self, _url, download=True):
        target = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
        target.write_bytes(self.payload)
        if self.failure:
            raise FakeDownloadError(self.failure)
        return {"id": "jsz2fjVDtEo", "ext": "mp4", "_filename": str(target)}

    def prepare_filename(self, info):
        return info["_filename"]


def candidate():
    return VisualSearchResult(
        provider_id="jsz2fjVDtEo", name="fixture", kind="video", source="youtube",
        source_page_url="https://www.youtube.com/watch?v=jsz2fjVDtEo",
        creator="", license="", license_url="", attribution="", search_provider="youtube_web",
    )


class YoutubeCachePreflightTests(unittest.TestCase):
    def test_case_distinct_youtube_id_never_reuses_another_ids_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            other_id = "jsz2fjvdtEo"
            other = root / "cache" / "video" / f"web-{web._safe_component(other_id)}-{web._stable_web_id(other_id)[:12]}.mp4"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"wrong-source" * 128)
            with patch.object(web, "_yt_dlp_api", return_value=(FakeYoutubeDL, FakeDownloadError)) as api, patch.object(web, "probe_video_stream"):
                path = web._download_web_video(root, candidate())
            api.assert_called_once()
            self.assertNotEqual(path, other)
            self.assertEqual(path.read_bytes(), FakeYoutubeDL.payload)
            self.assertEqual(other.read_bytes(), b"wrong-source" * 128)

    def test_new_video_is_never_visible_in_final_cache_before_ffprobe_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache" / "video"

            def validate(staged):
                self.assertIn(".part.", staged.name)
                self.assertEqual(list(cache.glob("*.mp4")), [])
                self.assertEqual(staged.read_bytes(), FakeYoutubeDL.payload)

            with patch.object(web, "_yt_dlp_api", return_value=(FakeYoutubeDL, FakeDownloadError)), patch.object(web, "probe_video_stream", side_effect=validate):
                path = web._download_web_video(root, candidate())
            self.assertEqual(path.parent, cache)
            self.assertNotIn(".part.", path.name)
            self.assertEqual(list(cache.iterdir()), [path])

    def test_ffprobe_failure_removes_download_and_staging_without_final_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(web, "_yt_dlp_api", return_value=(FakeYoutubeDL, FakeDownloadError)), patch.object(web, "probe_video_stream", side_effect=RuntimeError("moov atom not found")), self.assertRaisesRegex(VisualSearchError, "moov atom not found"):
                web._download_web_video(root, candidate())
            self.assertEqual(list((root / "cache" / "video").iterdir()), [])

    def test_downloader_failure_keeps_cause_even_if_it_left_an_mp4(self):
        for failure in ("HTTP Error 403: Forbidden", "fragment 7 unavailable", "Requested format is not available"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.object(FakeYoutubeDL, "failure", failure), patch.object(web, "_yt_dlp_api", return_value=(FakeYoutubeDL, FakeDownloadError)), patch.object(web, "probe_video_stream") as probe, self.assertRaisesRegex(VisualSearchError, failure):
                    web._download_web_video(root, candidate())
                probe.assert_not_called()
                self.assertEqual(list((root / "cache" / "video").iterdir()), [])

    def test_nonzero_downloader_exit_code_cannot_promote_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(FakeYoutubeDL, "_download_retcode", 1), patch.object(web, "_yt_dlp_api", return_value=(FakeYoutubeDL, FakeDownloadError)), patch.object(web, "probe_video_stream") as probe, self.assertRaisesRegex(VisualSearchError, "exit code=1"):
                web._download_web_video(root, candidate())
            probe.assert_not_called()
            self.assertEqual(list((root / "cache" / "video").iterdir()), [])

    def test_corrupt_legacy_youtube_cache_is_deleted_then_redownloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "cache" / "video" / "youtube-jsz2fjVDtEo.mp4"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"<html>not-video</html>" * 128)
            with patch.object(web, "_yt_dlp_api", return_value=(FakeYoutubeDL, FakeDownloadError)), patch.object(web, "probe_video_stream", side_effect=[RuntimeError("moov atom not found"), None]):
                path = web._download_web_video(root, candidate())
            self.assertFalse(legacy.exists())
            self.assertEqual(path.read_bytes(), FakeYoutubeDL.payload)

    def test_asset_manager_routes_watch_page_to_existing_ytdlp_not_requests(self):
        info = VideoStreamInfo(10.0, 1280, 720, 30.0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = AssetManager(root / "episode" / "assets", root / "work", 720, 1280, 1, video_cache_dir=root / "cache" / "video")
            asset = AssetSpec("yt", "youtube-jsz2fjVDtEo.mp4", candidate().source_page_url, "", "", .5, .5)
            resolved = root / "cache" / "video" / asset.file
            with patch.object(web, "download_youtube_video", return_value=resolved) as download, patch("engine.assets.probe_video_stream", return_value=info), patch("engine.assets.download_to_cache") as direct:
                self.assertEqual(manager.ensure(asset), resolved)
            direct.assert_not_called()
            download.assert_called_once_with(asset.url, root / "cache" / "video", asset.file)

    def test_direct_video_cache_is_revalidated_and_only_promoted_after_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            destination = cache / "video.mp4"
            destination.write_bytes(b"old-invalid" * 128)
            observations = []

            def validate(path):
                observations.append(path.name)
                if path == destination:
                    raise RuntimeError("moov atom not found")
                self.assertFalse(destination.exists())

            with patch("engine.media_cache.requests.get", return_value=FakeResponse((FakeYoutubeDL.payload,))):
                result = download_to_cache("https://cdn.example/video.mp4", cache, "video.mp4", "video", attempts=1, min_bytes=1024, validator=validate)
            self.assertEqual(observations, ["video.mp4", "video.mp4.part"])
            self.assertEqual(result.read_bytes(), FakeYoutubeDL.payload)


class SafeYoutubeCookieProbeTests(unittest.TestCase):
    def test_working_primary_makes_cookies_not_required_even_when_absent(self):
        self.assertEqual(auth_probe.cookie_status(primary_ok=True, present=False, parseable=False, all_expired=False, fallback_ok=False, fallback_reason="NOT_ATTEMPTED"), "NOT_REQUIRED")

    def test_cookie_expiration_is_never_inferred_from_bot_or_format_failures(self):
        for reason in ("BOT_CHALLENGE", "FORMAT_UNAVAILABLE", "HTTP_403", "NETWORK"):
            self.assertEqual(auth_probe.cookie_status(primary_ok=False, present=True, parseable=True, all_expired=False, fallback_ok=False, fallback_reason=reason), "UNKNOWN")

    def test_explicit_cookie_rejection_is_invalid(self):
        self.assertEqual(auth_probe.cookie_status(primary_ok=False, present=True, parseable=True, all_expired=False, fallback_ok=False, fallback_reason="COOKIES_REJECTED"), "INVALID")

    def test_successful_cookie_fallback_is_valid(self):
        self.assertEqual(auth_probe.cookie_status(primary_ok=False, present=True, parseable=True, all_expired=False, fallback_ok=True, fallback_reason="NONE"), "VALID")

    def test_jar_parse_expiry_and_error_do_not_print_cookie_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.txt"
            output = io.StringIO()
            with redirect_stdout(output), redirect_stderr(output):
                path.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1\tSID\tNEVER_PRINT_THIS\n", encoding="utf-8")
                self.assertEqual(auth_probe.cookie_file_state(path), (True, True, True))
                path.write_text("NEVER_PRINT_THIS", encoding="utf-8")
                self.assertEqual(auth_probe.cookie_file_state(path), (False, False, False))
            self.assertNotIn("NEVER_PRINT_THIS", output.getvalue())

    def test_probe_logs_only_reason_not_exception_or_account_details(self):
        class FailingYoutubeDL:
            def __init__(self, _params):
                raise RuntimeError("cookies are no longer valid: PRIVATE_COOKIE")

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(auth_probe.probe_path(lambda: (FailingYoutubeDL, Exception), {}, candidate().source_page_url), (False, "COOKIES_REJECTED"))
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
