from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.visual_search import VisualSearchResult
import engine.visual_search_web as web


class FakeDownloadError(Exception):
    pass


def candidate() -> VisualSearchResult:
    return VisualSearchResult(
        provider_id="QualityPolicy01",
        name="Quality policy fixture",
        kind="video",
        source="youtube",
        source_page_url="https://www.youtube.com/watch?v=QualityPolicy01",
        creator="Fixture",
        license="",
        license_url="",
        attribution="Fixture",
        search_provider="youtube_web",
    )


def canonical_cache_path(root: Path, suffix: str = ".mp4") -> Path:
    raw_id = candidate().provider_id
    safe_id = web._safe_component(raw_id)[:54]
    digest = web._stable_web_id(raw_id)[:12]
    return root / "cache" / "video" / f"web-{safe_id}-{digest}{suffix}"


class YoutubeQualityPolicyTests(unittest.TestCase):
    def test_downloader_prefers_up_to_1440_without_height_gate(self):
        calls: list[dict] = []

        class SuccessfulYoutubeDL:
            _download_retcode = 0

            def __init__(self, options):
                self.options = options
                calls.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=True):
                self.assert_download = download
                target = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
                target.write_bytes(b"quality-policy-video" * 128)
                return {"id": "QualityPolicy01", "ext": "mp4", "_filename": str(target)}

            def prepare_filename(self, info):
                return info["_filename"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(web, "_yt_dlp_api", return_value=(SuccessfulYoutubeDL, FakeDownloadError)),
                patch.object(web, "probe_video_stream"),
            ):
                downloaded = web._download_web_video(root, candidate())

            self.assertTrue(downloaded.is_file())
            self.assertEqual(len(calls), 1)
            options = calls[0]
            self.assertEqual(options["format"], "bestvideo/best")
            self.assertEqual(options["format_sort"], ["res:1440", "fps:30", "vext:mp4"])
            self.assertNotIn("height", options["format"])
            self.assertEqual(options["max_filesize"], web.MAX_EXTERNAL_VIDEO_BYTES)
            self.assertTrue(web._cache_matches_quality_policy(downloaded))

    def test_current_policy_cache_is_reused_without_ytdlp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cached = canonical_cache_path(root)
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"current-policy-video" * 128)
            web._mark_current_quality_policy(cached)

            output = io.StringIO()
            with (
                patch.object(web, "probe_video_stream"),
                patch.object(
                    web,
                    "_yt_dlp_api",
                    side_effect=AssertionError("yt-dlp must not run for current policy cache"),
                ),
                redirect_stdout(output),
            ):
                resolved = web._download_web_video(root, candidate())

            self.assertEqual(resolved, cached)
            self.assertIn("status=CACHE_HIT", output.getvalue())
            self.assertIn(web.YOUTUBE_QUALITY_POLICY_VERSION, output.getvalue())

    def test_stale_lower_quality_cache_is_safe_fallback_when_refresh_fails(self):
        class FailingYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=True):
                raise FakeDownloadError("network unavailable during quality refresh")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = canonical_cache_path(root)
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"old-720p-cache" * 128)

            output = io.StringIO()
            with (
                patch.object(web, "probe_video_stream"),
                patch.object(web, "_yt_dlp_api", return_value=(FailingYoutubeDL, FakeDownloadError)),
                redirect_stdout(output),
            ):
                resolved = web._download_web_video(root, candidate())

            self.assertEqual(resolved, stale)
            self.assertFalse(web._cache_matches_quality_policy(stale))
            logs = output.getvalue()
            self.assertIn("status=CACHE_STALE", logs)
            self.assertIn("status=CACHE_FALLBACK", logs)
            self.assertNotIn("status=FAIL", logs)


if __name__ == "__main__":
    unittest.main()
