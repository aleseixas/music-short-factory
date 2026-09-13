from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import engine.visual_search_web as visual_search_web
from engine.visual_search import (
    VisualInspection,
    VisualSearchError,
    VisualSearchResult,
    search_visual,
)
from engine.visual_search_web import (
    RIGHTS_RESTRICTED_PENALTY,
    DuckDuckGoWebImageProvider,
    candidate_rights_status,
    infer_rights_status,
    rank_inspections_for_selection,
    rights_rank_adjustment,
    selection_score,
    serialize_candidate,
)


class FakeResponse:
    def __init__(
        self,
        *,
        payload: object | None = None,
        text: str = "",
        status_code: int = 200,
    ):
        self.payload = payload
        self.text = text
        self.status_code = status_code
        self.closed = False

    def json(self) -> object:
        return self.payload

    def close(self) -> None:
        self.closed = True


def result(
    *,
    provider_id: str,
    name: str,
    license_name: str = "",
    source: str = "web",
    kind: str = "video",
) -> VisualSearchResult:
    return VisualSearchResult(
        provider_id=provider_id,
        name=name,
        kind=kind,
        source=source,
        source_page_url=f"https://example.com/{provider_id}",
        creator="Author",
        license=license_name,
        license_url="",
        attribution="Author",
        search_provider="test",
    )


def inspection(candidate: VisualSearchResult, score: float) -> VisualInspection:
    return VisualInspection(
        result=candidate,
        path=Path(f"/tmp/{candidate.provider_id}.mp4"),
        width=720,
        height=1280,
        aspect_ratio=0.5625,
        duration_seconds=10.0 if candidate.kind == "video" else None,
        fps=30.0 if candidate.kind == "video" else None,
        motion=None,
        trim=None,
        visual_score=score,
        score_breakdown={"resolution": score},
    )


class RightsRankingTests(unittest.TestCase):
    def test_unknown_never_blocks_or_loses_points(self):
        candidate = result(provider_id="unknown", name="Unknown rights")
        payload = serialize_candidate(candidate)

        self.assertEqual(candidate_rights_status(candidate), "unknown")
        self.assertEqual(payload["rights_status"], "unknown")
        self.assertEqual(payload["rights_rank_adjustment"], 0.0)
        self.assertFalse(payload["rights_blocks_selection"])
        self.assertTrue(payload["selection_eligible"])
        self.assertEqual(selection_score(87.5, "unknown"), 87.5)

    def test_restricted_is_penalized_but_remains_eligible(self):
        candidate = result(
            provider_id="restricted",
            name="Restricted metadata",
            license_name="Standard YouTube License",
            source="youtube",
        )
        payload = serialize_candidate(candidate)

        self.assertEqual(candidate_rights_status(candidate), "restricted")
        self.assertEqual(rights_rank_adjustment("restricted"), RIGHTS_RESTRICTED_PENALTY)
        self.assertFalse(payload["rights_blocks_selection"])
        self.assertTrue(payload["selection_eligible"])
        self.assertEqual(
            selection_score(95.0, "restricted"),
            95.0 + RIGHTS_RESTRICTED_PENALTY,
        )

    def test_explicit_open_license_is_verified(self):
        self.assertEqual(
            infer_rights_status(license_name="CC BY-SA 4.0", source="wikimedia"),
            "verified",
        )
        self.assertEqual(
            infer_rights_status(license_name="CC0", source="wikimedia"),
            "verified",
        )

    def test_restricted_candidate_can_still_beat_weaker_unknown_candidate(self):
        restricted = inspection(
            result(
                provider_id="strong",
                name="Strong restricted",
                license_name="All Rights Reserved",
            ),
            100.0,
        )
        unknown = inspection(
            result(provider_id="weak", name="Weaker unknown"),
            70.0,
        )

        ranked = rank_inspections_for_selection((unknown, restricted))

        self.assertEqual(ranked[0].result.provider_id, "strong")
        self.assertGreater(
            selection_score(
                restricted.visual_score,
                candidate_rights_status(restricted.result),
            ),
            selection_score(unknown.visual_score, "unknown"),
        )

    def test_image_and_video_ranking_are_kept_in_separate_groups(self):
        image = inspection(
            result(provider_id="image", name="Image", kind="image"),
            99.0,
        )
        video = inspection(
            result(provider_id="video", name="Video", kind="video"),
            80.0,
        )

        ranked = rank_inspections_for_selection((video, image))

        self.assertEqual({item.result.kind for item in ranked}, {"image", "video"})
        self.assertEqual(len(ranked), 2)


class WebImageProviderTests(unittest.TestCase):
    def test_web_image_is_discovered_as_unknown_and_downloadable(self):
        token_response = FakeResponse(text="var x = {vqd='123-456'};")
        image_response = FakeResponse(
            payload={
                "results": [
                    {
                        "title": "Post Malone concert photo",
                        "image": "https://cdn.example.com/post-malone.jpg",
                        "thumbnail": "https://cdn.example.com/thumb.jpg",
                        "url": "https://music.example.com/post-malone",
                        "source": "music.example.com",
                        "width": 1600,
                        "height": 900,
                    }
                ]
            }
        )
        with patch(
            "engine.visual_search_web.requests.get",
            side_effect=(token_response, image_response),
        ):
            results = DuckDuckGoWebImageProvider().search(
                "Post Malone interview",
                kind="image",
                limit=4,
            )

        self.assertTrue(token_response.closed)
        self.assertTrue(image_response.closed)
        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate.search_provider, "duckduckgo_web_images")
        self.assertEqual(candidate_rights_status(candidate), "unknown")
        self.assertIsNotNone(candidate.candidate_asset_entry)
        self.assertTrue(candidate.download_url.endswith("post-malone.jpg"))

    def test_web_provider_failure_does_not_stop_other_provider(self):
        fallback = result(
            provider_id="fallback",
            name="Fallback",
            kind="image",
        )

        class BrokenWebProvider:
            name = "broken-web"

            def search(self, query: str, kind: str, limit: int):
                raise RuntimeError("web failed")

        class WorkingProvider:
            name = "working"

            def search(self, query: str, kind: str, limit: int):
                return (fallback,)

        with tempfile.TemporaryDirectory() as temp_dir:
            report = search_visual(
                Path(temp_dir),
                ("artist live",),
                kind="image",
                include_external=True,
                providers=(BrokenWebProvider(), WorkingProvider()),
            )

        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].provider_id, "fallback")
        self.assertTrue(report.warnings)


class WebVideoDownloadTests(unittest.TestCase):
    @staticmethod
    def _candidate() -> VisualSearchResult:
        return VisualSearchResult(
            provider_id="-safe-video-id",
            name="Fixture video",
            kind="video",
            source="youtube",
            source_page_url="https://www.youtube.com/watch?v=-safe-video-id",
            creator="Fixture",
            license="",
            license_url="",
            attribution="Fixture",
            search_provider="youtube_web",
        )

    def test_yt_dlp_failure_keeps_concrete_reason_and_redacts_secrets(self):
        class FakeDownloadError(Exception):
            pass

        class FailingYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=True):
                self.assert_download = download
                raise FakeDownloadError(
                    "Requested format is not available at https://cdn.example/file "
                    "Authorization: Bearer BEARER_SECRET "
                    "Cookie: SID=COOKIE_SECRET; HSID=SECOND_COOKIE_SECRET "
                    "po_token=PO_SECRET access_token=ACCESS_SECRET"
                )

        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with (
                patch.object(
                    visual_search_web,
                    "_yt_dlp_api",
                    return_value=(FailingYoutubeDL, FakeDownloadError),
                ),
                redirect_stdout(output),
                self.assertRaisesRegex(VisualSearchError, "Requested format is not available"),
            ):
                visual_search_web._download_web_video(
                    Path(directory),
                    self._candidate(),
                )

        logs = output.getvalue()
        self.assertIn("YT_DLP_ATTEMPT id=-safe-video-id", logs)
        self.assertIn("YT_DLP_RESULT id=-safe-video-id status=FAIL", logs)
        self.assertIn("Requested format is not available", logs)
        self.assertNotIn("cdn.example", logs)
        for secret in (
            "BEARER_SECRET",
            "COOKIE_SECRET",
            "SECOND_COOKIE_SECRET",
            "PO_SECRET",
            "ACCESS_SECRET",
        ):
            self.assertNotIn(secret, logs)

    def test_success_is_cached_by_youtube_id_without_another_yt_dlp_call(self):
        calls: list[dict] = []

        class FakeDownloadError(Exception):
            pass

        class SuccessfulYoutubeDL:
            def __init__(self, options):
                self.options = options
                calls.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=True):
                target = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
                target.write_bytes(b"synthetic-video" * 128)
                return {"id": "-safe-video-id", "ext": "mp4", "_filename": str(target)}

            def prepare_filename(self, info):
                return info["_filename"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_logs = io.StringIO()
            with (
                patch.object(
                    visual_search_web,
                    "_yt_dlp_api",
                    return_value=(SuccessfulYoutubeDL, FakeDownloadError),
                ),
                patch.object(visual_search_web, "probe_video_stream"),
                redirect_stdout(first_logs),
            ):
                first = visual_search_web._download_web_video(root, self._candidate())
            self.assertTrue(first.is_file())
            self.assertEqual(len(calls), 1)
            self.assertIn("status=DOWNLOADED", first_logs.getvalue())

            second_logs = io.StringIO()
            with (
                patch.object(
                    visual_search_web,
                    "_yt_dlp_api",
                    side_effect=AssertionError("yt-dlp must not be loaded on cache hit"),
                ),
                patch.object(visual_search_web, "probe_video_stream"),
                redirect_stdout(second_logs),
            ):
                second = visual_search_web._download_web_video(root, self._candidate())
            self.assertEqual(second, first)
            self.assertIn("status=CACHE_HIT", second_logs.getvalue())

    def test_corrupt_cache_is_removed_and_downloaded_again(self):
        calls: list[dict] = []

        class FakeDownloadError(Exception):
            pass

        class SuccessfulYoutubeDL:
            def __init__(self, options):
                self.options = options
                calls.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def extract_info(self, _url, download=True):
                target = Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
                target.write_bytes(b"fresh-video" * 128)
                return {"id": "-safe-video-id", "ext": "mp4", "_filename": str(target)}

            def prepare_filename(self, info):
                return info["_filename"]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = self._candidate()
            safe_id = visual_search_web._safe_component(candidate.provider_id)[:54]
            digest = visual_search_web._stable_web_id(candidate.provider_id)[:12]
            cached = root / "cache" / "video" / f"web-{safe_id}-{digest}.mp4"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"corrupt-cache" * 128)
            output = io.StringIO()
            with (
                patch.object(
                    visual_search_web,
                    "probe_video_stream",
                    side_effect=[RuntimeError("ffprobe found no video stream"), None],
                ),
                patch.object(
                    visual_search_web,
                    "_yt_dlp_api",
                    return_value=(SuccessfulYoutubeDL, FakeDownloadError),
                ),
                redirect_stdout(output),
            ):
                downloaded = visual_search_web._download_web_video(root, candidate)

            self.assertEqual(downloaded, cached)
            self.assertEqual(downloaded.read_bytes(), b"fresh-video" * 128)
            self.assertEqual(len(calls), 1)
            self.assertIn("status=CACHE_INVALID", output.getvalue())
            self.assertIn("status=DOWNLOADED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
