import json
import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import resolve_visual_candidates as resolver
from engine.visual_repetition import VisualFingerprint
from engine.visual_search import TrimAssessment, VisualInspection, VisualSearchResult


class VisualCandidateResolutionTests(unittest.TestCase):
    def _write_episode(
        self,
        root: Path,
        slug: str,
        pool: dict | None,
        *,
        asset_file: str = "old.webm",
        asset_url: str = "https://upload.wikimedia.org/old.webm",
    ) -> Path:
        episode = root / "episodes" / slug
        episode.mkdir(parents=True)
        (episode / "assets.json").write_text(
            json.dumps({"schema_version": 1, "assets": [{"id": "slot_a", "file": asset_file, "url": asset_url, "credit": "old", "license": "CC0", "focus": {"x": 0.5, "y": 0.5}}]}),
            encoding="utf-8",
        )
        (episode / "timeline.json").write_text(
            json.dumps({"schema_version": 1, "shots": [{"id": "shot_a", "segment": "a", "asset": "slot_a", "motion": "hold", "transition_out": "cut"}]}),
            encoding="utf-8",
        )
        if pool is not None:
            (episode / "visual_candidates.json").write_text(json.dumps(pool), encoding="utf-8")
        return episode

    @staticmethod
    def _successful_candidate(candidate: dict, index: int, *, score: float = 75.0):
        kind = str(candidate["kind"])
        result = VisualSearchResult(
            provider_id=str(candidate.get("provider_id") or index),
            name=str(candidate["name"]),
            kind=kind,
            source="fixture",
            source_page_url=str(candidate["url"]),
            creator="",
            license="",
            license_url="",
            attribution="",
            search_provider=str(candidate.get("search_provider") or "fixture"),
        )
        inspected = VisualInspection(
            result=result,
            path=Path(str(candidate["file"])),
            width=720,
            height=1280,
            aspect_ratio=0.5625,
            duration_seconds=20.0 if kind == "video" else None,
            fps=30.0 if kind == "video" else None,
            motion=None,
            trim=(
                TrimAssessment(True, 0.0, 20.0, 20.0, 8.0, 12.0, "safe")
                if kind == "video"
                else None
            ),
            visual_score=score,
            score_breakdown={"fixture": score},
        )
        return result, inspected, []

    def test_missing_pool_is_backward_compatible(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_episode(root, "demo", None)
            self.assertEqual(resolver.resolve_episode(root, "demo"), 0)

    def test_prefilters_pool_and_selects_best_inspected_candidate(self):
        pool = {
            "schema_version": 1,
            "slots": [
                {
                    "id": "slot_a",
                    "required_seconds": 6.0,
                    "inspect_top": 2,
                    "candidates": [
                        {"name": "small", "kind": "image", "url": "https://upload.wikimedia.org/small.jpg", "file": "small.jpg", "license": "CC0", "width": 400, "height": 600, "editorial_rank": 1},
                        {"name": "good", "kind": "video", "url": "https://upload.wikimedia.org/good.webm", "file": "good.webm", "license": "CC0", "width": 1080, "height": 1920, "duration_seconds": 20, "source_start_seconds": 1, "source_end_seconds": 12, "editorial_rank": 2},
                        {"name": "best", "kind": "video", "url": "https://upload.wikimedia.org/best.webm", "file": "best.webm", "license": "CC0", "width": 1440, "height": 2560, "duration_seconds": 30, "source_start_seconds": 2, "source_end_seconds": 14, "editorial_rank": 3},
                    ],
                }
            ],
        }

        def fake_inspect(_root, result, **kwargs):
            score = 91.0 if result.name == "best" else 78.0
            return VisualInspection(
                result=result,
                path=Path(result.suggested_file or "candidate.webm"),
                aspect_ratio=(result.width or 720) / (result.height or 1280),
                visual_score=score,
                width=result.width or 720,
                height=result.height or 1280,
                duration_seconds=result.duration_seconds,
                fps=30.0 if result.kind == "video" else None,
                motion=None,
                trim=TrimAssessment(
                    safe_for_shot=True,
                    source_start_seconds=kwargs["source_start_seconds"],
                    source_end_seconds=kwargs["source_end_seconds"],
                    available_seconds=(
                        kwargs["source_end_seconds"] - kwargs["source_start_seconds"]
                    ),
                    required_seconds=kwargs["shot_duration_seconds"],
                    margin_seconds=1.0,
                    reason="safe",
                ),
                score_breakdown={"fixture": score},
                fingerprint=VisualFingerprint.from_dict(
                    {"kind": result.kind, "urls": [result.download_url]}
                ),
            )

        with TemporaryDirectory() as tmp, patch.object(resolver, "inspect_visual_result", side_effect=fake_inspect) as inspect:
            root = Path(tmp)
            episode = self._write_episode(root, "demo", pool)
            self.assertEqual(resolver.resolve_episode(root, "demo"), 0)
            self.assertEqual(inspect.call_count, 2)

            assets = json.loads((episode / "assets.json").read_text(encoding="utf-8"))
            chosen = next(asset for asset in assets["assets"] if asset["id"] == "slot_a")
            self.assertEqual(chosen["file"], "best.webm")

            timeline = json.loads((episode / "timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(timeline["shots"][0]["source_start_seconds"], 2.0)
            self.assertEqual(timeline["shots"][0]["source_end_seconds"], 14.0)

            report = json.loads((episode / "visual_resolution_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["selections"]["slot_a"]["visual_score"], 91.0)

    def test_web_mode_inspects_video_and_image_then_prefers_valid_video(self):
        pool = {
            "schema_version": 1,
            "slots": [{
                "id": "slot_a",
                "required_seconds": 4.0,
                "inspect_top": 2,
                "candidates": [
                    {"name": "fallback image", "kind": "image", "url": "https://upload.wikimedia.org/fallback.jpg", "file": "fallback.jpg", "editorial_rank": 1},
                    {"name": "video one", "kind": "video", "url": "https://www.youtube.com/watch?v=video-one", "file": "video-one.mp4", "search_provider": "youtube_web", "provider_id": "video-one", "editorial_rank": 2},
                    {"name": "video two", "kind": "video", "url": "https://www.youtube.com/watch?v=video-two", "file": "video-two.mp4", "search_provider": "youtube_web", "provider_id": "video-two", "editorial_rank": 3},
                ],
            }],
        }
        attempted: list[str] = []

        def fake_inspect(_root, _slot, candidate, index):
            attempted.append(candidate["kind"])
            kind = candidate["kind"]
            score = 99.0 if kind == "image" else (72.0 if index == 2 else 70.0)
            result = VisualSearchResult(
                provider_id=str(candidate.get("provider_id") or index),
                name=candidate["name"],
                kind=kind,
                source="fixture",
                source_page_url=candidate["url"],
                creator="fixture",
                license="",
                license_url="",
                attribution="fixture",
                search_provider=str(candidate.get("search_provider") or "fixture"),
                download_url=candidate["url"],
                allowed_download_hosts=("upload.wikimedia.org",),
                file_format=Path(candidate["file"]).suffix.lstrip("."),
            )
            inspection = VisualInspection(
                result=result,
                path=Path(candidate["file"]),
                aspect_ratio=0.5625,
                visual_score=score,
                width=720,
                height=1280,
                duration_seconds=20.0 if kind == "video" else None,
                fps=30.0 if kind == "video" else None,
                motion=None,
                trim=(
                    TrimAssessment(True, 0.0, 20.0, 20.0, 4.0, 16.0, "safe")
                    if kind == "video"
                    else None
                ),
                score_breakdown={"fixture": score},
            )
            return result, inspection, []

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            episode = self._write_episode(
                root,
                "demo",
                pool,
                asset_file="old.jpg",
                asset_url="https://upload.wikimedia.org/old.jpg",
            )
            output = io.StringIO()
            with (
                patch.object(resolver, "_inspect_candidate", side_effect=fake_inspect),
                patch.object(
                    resolver,
                    "_asset_entry",
                    side_effect=lambda slot_id, candidate, _result: {
                        "id": slot_id,
                        "file": candidate["file"],
                    },
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(
                    resolver.resolve_episode(
                        root,
                        "demo",
                        prefer_video_candidates=True,
                    ),
                    0,
                )

            self.assertEqual(attempted.count("video"), 2)
            self.assertEqual(attempted.count("image"), 1)
            self.assertIn("plano de inspecao video=2, image=1", output.getvalue())
            self.assertIn("VIDEO_ATTEMPT slot=slot_a", output.getvalue())
            self.assertIn("VIDEO_SELECTED slot=slot_a id=video-one", output.getvalue())
            assets = json.loads((episode / "assets.json").read_text(encoding="utf-8"))
            self.assertEqual(assets["assets"][0]["file"], "video-one.mp4")
            timeline = json.loads((episode / "timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(timeline["shots"][0]["source_start_seconds"], 0.0)

    def test_failed_image_does_not_mask_video_and_failure_is_reported(self):
        pool = {
            "schema_version": 1,
            "slots": [{
                "id": "slot_a",
                "inspect_top": 1,
                "candidates": [
                    {"name": "broken image", "kind": "image", "url": "https://upload.wikimedia.org/broken.jpg", "file": "broken.jpg"},
                    {"name": "usable video", "kind": "video", "url": "https://www.youtube.com/watch?v=usable-id", "file": "usable.mp4", "search_provider": "youtube_web", "provider_id": "usable-id"},
                ],
            }],
        }

        def fake_inspect(_root, _slot, candidate, _index):
            if candidate["kind"] == "image":
                raise RuntimeError("HTTP 403 from image provider")
            result = VisualSearchResult(
                provider_id="usable-id",
                name="usable video",
                kind="video",
                source="youtube",
                source_page_url=candidate["url"],
                creator="",
                license="",
                license_url="",
                attribution="",
                search_provider="youtube_web",
            )
            inspected = VisualInspection(
                result=result,
                path=Path("usable.mp4"),
                width=1280,
                height=720,
                aspect_ratio=16 / 9,
                duration_seconds=30.0,
                fps=30.0,
                motion=None,
                trim=TrimAssessment(True, 0.0, 30.0, 30.0, 8.0, 22.0, "safe"),
                visual_score=75.0,
                score_breakdown={"fixture": 75.0},
            )
            return result, inspected, []

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            episode = self._write_episode(
                root,
                "demo",
                pool,
                asset_file="old.jpg",
                asset_url="https://upload.wikimedia.org/old.jpg",
            )
            output = io.StringIO()
            with (
                patch.object(resolver, "_inspect_candidate", side_effect=fake_inspect),
                patch.object(resolver, "_asset_entry", return_value={"id": "slot_a", "file": "usable.mp4"}),
                redirect_stdout(output),
            ):
                resolver.resolve_episode(root, "demo", prefer_video_candidates=True)

            self.assertIn("VIDEO_RESULT slot=slot_a", output.getvalue())
            self.assertIn("Visual candidate FAIL slot_a[1]: HTTP 403", output.getvalue())
            report = json.loads((episode / "visual_resolution_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["selections"]["slot_a"]["kind"], "video")

    def test_first_video_failure_does_not_mask_second_video(self):
        pool = {
            "schema_version": 1,
            "slots": [{
                "id": "slot_a",
                "inspect_top": 2,
                "candidates": [
                    {"name": "blocked video", "kind": "video", "url": "https://www.youtube.com/watch?v=blocked-id", "file": "blocked.mp4", "search_provider": "youtube_web", "provider_id": "blocked-id", "editorial_rank": 1},
                    {"name": "working video", "kind": "video", "url": "https://www.youtube.com/watch?v=working-id", "file": "working.mp4", "search_provider": "youtube_web", "provider_id": "working-id", "editorial_rank": 2},
                    {"name": "fallback image", "kind": "image", "url": "https://upload.wikimedia.org/fallback.jpg", "file": "fallback.jpg", "editorial_rank": 1},
                ],
            }],
        }
        attempts: list[str] = []

        def fake_inspect(_root, _slot, candidate, index):
            attempts.append(str(candidate["name"]))
            if candidate.get("provider_id") == "blocked-id":
                raise RuntimeError("yt-dlp: HTTP 403; player response unavailable")
            return self._successful_candidate(candidate, index)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            episode = self._write_episode(
                root,
                "demo",
                pool,
                asset_file="old.jpg",
                asset_url="https://upload.wikimedia.org/old.jpg",
            )
            output = io.StringIO()
            with (
                patch.object(resolver, "_inspect_candidate", side_effect=fake_inspect),
                patch.object(
                    resolver,
                    "_asset_entry",
                    side_effect=lambda slot_id, candidate, _result: {
                        "id": slot_id,
                        "file": candidate["file"],
                    },
                ),
                redirect_stdout(output),
            ):
                resolver.resolve_episode(root, "demo", prefer_video_candidates=True)

            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                report["selections"]["slot_a"]["name"],
                "working video",
            )
            self.assertEqual(attempts, ["blocked video", "working video", "fallback image"])
            self.assertIn("reason=yt-dlp: HTTP 403", output.getvalue())
            self.assertIn("VIDEO_SELECTED slot=slot_a id=working-id", output.getvalue())

    def test_all_video_failures_fall_back_to_valid_image(self):
        pool = {
            "schema_version": 1,
            "slots": [{
                "id": "slot_a",
                "inspect_top": 2,
                "candidates": [
                    {"name": "video one", "kind": "video", "url": "https://www.youtube.com/watch?v=one-id", "file": "one.mp4", "search_provider": "youtube_web", "provider_id": "one-id", "editorial_rank": 1},
                    {"name": "video two", "kind": "video", "url": "https://www.youtube.com/watch?v=two-id", "file": "two.mp4", "search_provider": "youtube_web", "provider_id": "two-id", "editorial_rank": 2},
                    {"name": "valid image", "kind": "image", "url": "https://upload.wikimedia.org/valid.jpg", "file": "valid.jpg", "editorial_rank": 1},
                ],
            }],
        }

        def fake_inspect(_root, _slot, candidate, index):
            if candidate["kind"] == "video":
                raise RuntimeError(f"download rejected for {candidate['provider_id']}")
            return self._successful_candidate(candidate, index, score=68.0)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            episode = self._write_episode(
                root,
                "demo",
                pool,
                asset_file="old.jpg",
                asset_url="https://upload.wikimedia.org/old.jpg",
            )
            output = io.StringIO()
            with (
                patch.object(resolver, "_inspect_candidate", side_effect=fake_inspect),
                patch.object(
                    resolver,
                    "_asset_entry",
                    side_effect=lambda slot_id, candidate, _result: {
                        "id": slot_id,
                        "file": candidate["file"],
                    },
                ),
                redirect_stdout(output),
            ):
                resolver.resolve_episode(root, "demo", prefer_video_candidates=True)

            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["selections"]["slot_a"]["kind"], "image")
            self.assertEqual(report["selections"]["slot_a"]["name"], "valid image")
            self.assertEqual(output.getvalue().count("status=FAIL reason=download rejected"), 2)

    def test_log_diagnostics_redact_common_credentials(self):
        raw = (
            "download failed Authorization: Bearer BEARER_SECRET "
            "Cookie: SID=COOKIE_ONE; HSID=COOKIE_TWO "
            "po_token=PO_SECRET access_token=ACCESS_SECRET"
        )

        diagnostic = resolver._safe_log_text(RuntimeError(raw))

        self.assertIn("download failed", diagnostic)
        for secret in (
            "BEARER_SECRET",
            "COOKIE_ONE",
            "COOKIE_TWO",
            "PO_SECRET",
            "ACCESS_SECRET",
        ):
            self.assertNotIn(secret, diagnostic)

    def test_selected_youtube_source_is_reserved_for_later_slots(self):
        shared_video = {
            "name": "shared video",
            "kind": "video",
            "url": "https://www.youtube.com/watch?v=shared-id",
            "file": "shared.mp4",
            "search_provider": "youtube_web",
            "provider_id": "shared-id",
        }
        pool = {
            "schema_version": 1,
            "slots": [
                {"id": "slot_a", "inspect_top": 1, "candidates": [shared_video, {"name": "image a", "kind": "image", "url": "https://upload.wikimedia.org/a.jpg", "file": "a.jpg"}]},
                {"id": "slot_b", "inspect_top": 1, "candidates": [shared_video, {"name": "image b", "kind": "image", "url": "https://upload.wikimedia.org/b.jpg", "file": "b.jpg"}]},
            ],
        }

        def fake_inspect(_root, _slot, candidate, index):
            kind = candidate["kind"]
            result = VisualSearchResult(
                provider_id=str(candidate.get("provider_id") or index),
                name=candidate["name"],
                kind=kind,
                source="fixture",
                source_page_url=candidate["url"],
                creator="",
                license="",
                license_url="",
                attribution="",
                search_provider=str(candidate.get("search_provider") or "fixture"),
            )
            score = 80.0 if kind == "video" else 70.0
            inspected = VisualInspection(
                result=result,
                path=Path(candidate["file"]),
                width=720,
                height=1280,
                aspect_ratio=0.5625,
                duration_seconds=20.0 if kind == "video" else None,
                fps=30.0 if kind == "video" else None,
                motion=None,
                trim=(
                    TrimAssessment(True, 0.0, 20.0, 20.0, 8.0, 12.0, "safe")
                    if kind == "video"
                    else None
                ),
                visual_score=score,
                score_breakdown={"fixture": score},
            )
            return result, inspected, []

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            episode = root / "episodes" / "demo"
            episode.mkdir(parents=True)
            (episode / "assets.json").write_text(json.dumps({
                "schema_version": 1,
                "assets": [
                    {"id": "slot_a", "file": "old-a.jpg", "url": "https://upload.wikimedia.org/old-a.jpg"},
                    {"id": "slot_b", "file": "old-b.jpg", "url": "https://upload.wikimedia.org/old-b.jpg"},
                ],
            }), encoding="utf-8")
            (episode / "timeline.json").write_text(json.dumps({
                "schema_version": 1,
                "shots": [
                    {"id": "shot_a", "segment": "a", "asset": "slot_a", "motion": "hold", "transition_out": "cut"},
                    {"id": "shot_b", "segment": "b", "asset": "slot_b", "motion": "hold", "transition_out": "cut"},
                ],
            }), encoding="utf-8")
            (episode / "visual_candidates.json").write_text(json.dumps(pool), encoding="utf-8")
            output = io.StringIO()
            with (
                patch.object(resolver, "_inspect_candidate", side_effect=fake_inspect),
                patch.object(
                    resolver,
                    "_asset_entry",
                    side_effect=lambda slot_id, candidate, _result: {"id": slot_id, "file": candidate["file"]},
                ),
                redirect_stdout(output),
            ):
                resolver.resolve_episode(root, "demo", prefer_video_candidates=True)

            report = json.loads((episode / "visual_resolution_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["selections"]["slot_a"]["kind"], "video")
            self.assertEqual(report["selections"]["slot_b"]["kind"], "image")
            self.assertIn("reason=duplicate_in_current_resolution", output.getvalue())


if __name__ == "__main__":
    unittest.main()
