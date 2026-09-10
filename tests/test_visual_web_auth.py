from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import engine.visual_search_web as web_engine
import resolve_visual_candidates_web as web_resolver


class YoutubeResolverAuthTests(unittest.TestCase):
    def test_web_entrypoint_enables_cross_kind_video_priority(self):
        with (
            patch.object(sys, "argv", ["resolve_visual_candidates_web.py", "episode"]),
            patch.object(web_resolver, "_install_patches"),
            patch.object(web_resolver.legacy, "main", return_value=0) as legacy_main,
        ):
            self.assertEqual(web_resolver.main(), 0)

        legacy_main.assert_called_once_with(prefer_video_candidates=True)

    def test_wikimedia_redirect_host_is_approved_for_image_download(self):
        result = web_resolver._candidate_result(
            {
                "kind": "image",
                "file": "gil.jpg",
                "url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Gil.jpg",
            },
            "gil",
            1,
        )

        self.assertEqual(
            set(result.allowed_download_hosts),
            {"commons.wikimedia.org", "upload.wikimedia.org"},
        )

    def test_wikimedia_redirect_reaches_approved_upload_host(self):
        class FakeResponse:
            def __init__(self, status_code, *, headers=None, chunks=()):
                self.status_code = status_code
                self.headers = dict(headers or {})
                self._chunks = tuple(chunks)
                self.closed = False

            def iter_content(self, chunk_size):
                self.chunk_size = chunk_size
                return iter(self._chunks)

            def close(self):
                self.closed = True

        result = web_resolver._candidate_result(
            {
                "kind": "image",
                "file": "gil.jpg",
                "url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Gil.jpg",
            },
            "gil",
            1,
        )
        redirect = FakeResponse(
            302,
            headers={"Location": "https://upload.wikimedia.org/Gil.jpg"},
        )
        downloaded = FakeResponse(200, chunks=(b"synthetic-image",))

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "engine.media_cache.requests.get",
                    side_effect=(redirect, downloaded),
                ) as get,
                patch("engine.media_cache._wait_for_host_slot"),
                patch("engine.visual_search._probe_image", return_value=(1080, 1920)),
            ):
                inspection = web_engine.inspect_visual_result(Path(directory), result)

        self.assertEqual(inspection.width, 1080)
        self.assertEqual(inspection.height, 1920)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in get.call_args_list],
            [
                "https://commons.wikimedia.org/wiki/Special:Redirect/file/Gil.jpg",
                "https://upload.wikimedia.org/Gil.jpg",
            ],
        )
        self.assertTrue(redirect.closed)
        self.assertTrue(downloaded.closed)

    def test_po_provider_options_use_mweb_and_configured_bgutil_endpoint(self):
        options = web_resolver._with_youtube_po_options(
            {"extractor_args": {"youtube": {"player_client": ["default"]}}},
            "http://127.0.0.1:4416",
        )

        self.assertEqual(
            options["extractor_args"]["youtube"]["player_client"],
            ["mweb", "default"],
        )
        self.assertEqual(
            options["extractor_args"]["youtubepot-bgutilhttp"]["base_url"],
            ["http://127.0.0.1:4416"],
        )

    def test_auth_challenge_retries_with_isolated_cookie_fallback(self):
        import resolve_visual_candidates_web_auth as auth_resolver

        class FakeDownloadError(Exception):
            pass

        observed: dict[str, object] = {}

        class PrimaryYoutubeDL:
            def __init__(self, params=None, auto_init=True):
                observed["primary_params"] = params
                observed["primary_auto_init"] = auto_init

            def extract_info(self, _url, *_args, **_kwargs):
                raise FakeDownloadError(
                    "Sign in to confirm you're not a bot cookie=NEVER_PRINT_ME"
                )

        class CookieYoutubeDL:
            def __init__(self, params=None, auto_init=True):
                observed["cookie_params"] = params
                observed["cookie_auto_init"] = auto_init

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, *_args, **_kwargs):
                return {"id": "fixture", "ext": "mp4"}

        def primary_api():
            return PrimaryYoutubeDL, FakeDownloadError

        def cookie_api():
            return CookieYoutubeDL, FakeDownloadError

        cookie_path = Path("runner-temp/cookies.txt")
        output = io.StringIO()
        with (
            patch.object(auth_resolver, "_prepare_cookie_file", return_value=cookie_path),
            patch.object(web_engine, "_yt_dlp_api", primary_api),
            patch.object(web_engine, "_yt_dlp_api_original", cookie_api, create=True),
            redirect_stdout(output),
        ):
            auth_resolver._install_cookie_fallback()
            WrappedYoutubeDL, _DownloadError = web_engine._yt_dlp_api()
            wrapped = WrappedYoutubeDL(
                {
                    "extractor_args": {
                        "youtube": {"player_client": ["mweb"]},
                        "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
                    }
                }
            )
            result = wrapped.extract_info(
                "https://www.youtube.com/watch?v=fixture",
                download=True,
            )

        self.assertEqual(result["id"], "fixture")
        primary_params = observed["primary_params"]
        self.assertIn("node", primary_params["js_runtimes"])
        cookie_params = observed["cookie_params"]
        self.assertEqual(cookie_params["cookiefile"], str(cookie_path))
        self.assertEqual(
            cookie_params["extractor_args"]["youtube"]["player_client"],
            ["web_embedded"],
        )
        self.assertNotIn("youtubepot-bgutilhttp", cookie_params["extractor_args"])
        logs = output.getvalue()
        self.assertIn("YT_DLP_AUTH id=fixture primary=FAIL", logs)
        self.assertIn("YT_DLP_AUTH id=fixture fallback=SUCCESS", logs)
        self.assertNotIn("NEVER_PRINT_ME", logs)

    def test_cookie_fallback_failure_keeps_reasons_and_redacts_credentials(self):
        import resolve_visual_candidates_web_auth as auth_resolver

        class FakeDownloadError(Exception):
            pass

        class PrimaryYoutubeDL:
            def __init__(self, params=None, auto_init=True):
                pass

            def extract_info(self, _url, *_args, **_kwargs):
                raise FakeDownloadError(
                    "Sign in to confirm you're not a bot "
                    "Authorization: Bearer PRIMARY_SECRET"
                )

        class CookieYoutubeDL:
            def __init__(self, params=None, auto_init=True):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, *_args, **_kwargs):
                raise FakeDownloadError(
                    "Requested format is unavailable "
                    "Cookie: SID=COOKIE_SECRET; HSID=SECOND_COOKIE_SECRET"
                )

        output = io.StringIO()
        with (
            patch.object(
                auth_resolver,
                "_prepare_cookie_file",
                return_value=Path("runner-temp/cookies.txt"),
            ),
            patch.object(
                web_engine,
                "_yt_dlp_api",
                return_value=(PrimaryYoutubeDL, FakeDownloadError),
            ),
            patch.object(
                web_engine,
                "_yt_dlp_api_original",
                return_value=(CookieYoutubeDL, FakeDownloadError),
                create=True,
            ),
            redirect_stdout(output),
        ):
            auth_resolver._install_cookie_fallback()
            WrappedYoutubeDL, _DownloadError = web_engine._yt_dlp_api()
            wrapped = WrappedYoutubeDL({})
            with self.assertRaises(FakeDownloadError) as raised:
                wrapped.extract_info(
                    "https://www.youtube.com/watch?v=fixture",
                    download=True,
                )

        combined = output.getvalue() + str(raised.exception)
        self.assertIn("YT_DLP_AUTH id=fixture primary=FAIL", combined)
        self.assertIn("YT_DLP_AUTH id=fixture fallback=FAIL", combined)
        self.assertIn("Requested format is unavailable", combined)
        for secret in (
            "PRIMARY_SECRET",
            "COOKIE_SECRET",
            "SECOND_COOKIE_SECRET",
        ):
            self.assertNotIn(secret, combined)

    def test_non_auth_download_error_does_not_use_cookie_fallback(self):
        import resolve_visual_candidates_web_auth as auth_resolver

        class FakeDownloadError(Exception):
            pass

        class PrimaryYoutubeDL:
            def __init__(self, params=None, auto_init=True):
                pass

            def extract_info(self, _url, *_args, **_kwargs):
                raise FakeDownloadError("Requested format is unavailable")

        class CookieYoutubeDL:
            def __init__(self, params=None, auto_init=True):
                raise AssertionError("cookie fallback must not be constructed")

        with (
            patch.object(
                auth_resolver,
                "_prepare_cookie_file",
                return_value=Path("runner-temp/cookies.txt"),
            ),
            patch.object(
                web_engine,
                "_yt_dlp_api",
                return_value=(PrimaryYoutubeDL, FakeDownloadError),
            ),
            patch.object(
                web_engine,
                "_yt_dlp_api_original",
                return_value=(CookieYoutubeDL, FakeDownloadError),
                create=True,
            ),
        ):
            auth_resolver._install_cookie_fallback()
            WrappedYoutubeDL, _DownloadError = web_engine._yt_dlp_api()
            with self.assertRaisesRegex(FakeDownloadError, "Requested format"):
                WrappedYoutubeDL({}).extract_info(
                    "https://www.youtube.com/watch?v=fixture",
                    download=True,
                )


if __name__ == "__main__":
    unittest.main()
