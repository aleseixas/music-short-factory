from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.visual_search import VisualInspection, VisualSearchResult, search_visual
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


if __name__ == "__main__":
    unittest.main()
