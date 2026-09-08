from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import resolve_visual_candidates as resolver
import resolve_visual_candidates_web as web_resolver
import engine.visual_search_web as web_search
from engine.ffmpeg import VideoStreamInfo
from engine.visual_repetition import (
    RepetitionAssessment,
    VisualFingerprint,
    VisualHistoryEntry,
    assess_repetition,
    load_visual_history,
)
from engine.visual_search import (
    TrimAssessment,
    VisualInspection,
    VisualSearchResult,
    apply_visual_repetition,
    inspect_visual_result,
)


def _result(
    name: str,
    url: str,
    *,
    kind: str = "image",
    license_name: str = "CC0",
) -> VisualSearchResult:
    suffix = Path(url).suffix.lstrip(".") or ("webm" if kind == "video" else "jpg")
    host = "upload.wikimedia.org"
    return VisualSearchResult(
        provider_id=name,
        name=name,
        kind=kind,
        source="wikimedia",
        source_page_url=url,
        creator="Fixture Author",
        license=license_name,
        license_url="",
        attribution="Fixture Author",
        search_provider="fixture",
        width=1080,
        height=1920,
        duration_seconds=30.0 if kind == "video" else None,
        file_format=suffix,
        download_url=url,
        allowed_download_hosts=(host,),
    )


def _inspection(
    candidate: VisualSearchResult,
    score: float,
    *,
    trim: TrimAssessment | None = None,
    fingerprint: VisualFingerprint | None = None,
    repetition: RepetitionAssessment | None = None,
) -> VisualInspection:
    return VisualInspection(
        result=candidate,
        path=Path(candidate.suggested_file or f"{candidate.provider_id}.jpg"),
        width=1080,
        height=1920,
        aspect_ratio=0.5625,
        duration_seconds=30.0 if candidate.kind == "video" else None,
        fps=30.0 if candidate.kind == "video" else None,
        motion=None,
        trim=trim,
        visual_score=score,
        score_breakdown={"fixture": score},
        fingerprint=fingerprint,
        repetition=repetition,
    )


def _history_entry(
    url: str,
    *,
    episode: str = "older_episode",
    recency_rank: int = 0,
    kind: str = "image",
) -> VisualHistoryEntry:
    return VisualHistoryEntry(
        episode=episode,
        shot_id="shot_old",
        asset_id="asset_old",
        fingerprint=VisualFingerprint.from_dict(
            {"kind": kind, "urls": [url]}
        ),
        recorded_at="2026-08-01T12:00:00+00:00",
        recency_rank=recency_rank,
    )


class VisualRepetitionIntegrationTests(unittest.TestCase):
    def _write_episode(
        self,
        root: Path,
        slug: str,
        candidates: list[dict],
        *,
        inspect_top: int = 4,
        min_visual_score: float = 35.0,
        speed: float | None = None,
        freeze_frame: dict | None = None,
    ) -> Path:
        episode = root / "episodes" / slug
        episode.mkdir(parents=True)
        base_kind = str(candidates[0].get("kind") or "image")
        base_suffix = ".webm" if base_kind == "video" else ".jpg"
        (episode / "assets.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "assets": [
                        {
                            "id": "slot_a",
                            "file": f"base{base_suffix}",
                            "url": f"https://upload.wikimedia.org/base{base_suffix}",
                            "credit": "base",
                            "license": "CC0",
                            "focus": {"x": 0.5, "y": 0.5},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        shot = {
            "id": "shot_a",
            "segment": "a",
            "asset": "slot_a",
            "motion": "hold",
            "transition_out": "cut",
        }
        if speed is not None:
            shot["speed"] = speed
        if freeze_frame is not None:
            shot["freeze_frame"] = freeze_frame
        (episode / "timeline.json").write_text(
            json.dumps({"schema_version": 1, "shots": [shot]}),
            encoding="utf-8",
        )
        (episode / "visual_candidates.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "slots": [
                        {
                            "id": "slot_a",
                            "required_seconds": 4.0,
                            "crossfade_seconds": 0.25,
                            "inspect_top": inspect_top,
                            "min_visual_score": min_visual_score,
                            "candidates": candidates,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return episode

    def _write_history_report(
        self,
        root: Path,
        entry: VisualHistoryEntry,
    ) -> Path:
        episode = root / "episodes" / entry.episode
        episode.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 1,
            "episode": entry.episode,
            "recorded_at": entry.recorded_at,
            "visual_usage": [entry.as_dict()],
        }
        target = episode / "visual_resolution_report.json"
        target.write_text(json.dumps(report), encoding="utf-8")
        return target

    def _mock_inspector(self, scores: dict[str, float]):
        def inspect(project_root: Path, result: VisualSearchResult, **kwargs):
            base = replace(
                _inspection(result, scores[result.name]),
                path=project_root / "fixture-cache" / f"{result.provider_id}.jpg",
            )
            return apply_visual_repetition(
                project_root,
                base,
                kwargs.get("repetition_history"),
                exclude_episode=kwargs.get("exclude_episode"),
            )

        return inspect

    def test_repeated_95_loses_to_novel_80_and_report_keeps_evidence_out_of_schema(self):
        repeated_url = "https://upload.wikimedia.org/repeated.jpg"
        novel_url = "https://upload.wikimedia.org/novel.jpg"
        candidates = [
            {
                "name": "repeated",
                "kind": "image",
                "url": repeated_url,
                "file": "repeated.jpg",
                "width": 2400,
                "height": 1600,
                "editorial_rank": 1,
            },
            {
                "name": "novel",
                "kind": "image",
                "url": novel_url,
                "file": "novel.jpg",
                "width": 1200,
                "height": 1600,
                "editorial_rank": 2,
            },
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_history_report(root, _history_entry(repeated_url))
            episode = self._write_episode(root, "current", candidates, inspect_top=2)

            with patch.object(
                resolver,
                "inspect_visual_result",
                side_effect=self._mock_inspector({"repeated": 95.0, "novel": 80.0}),
            ):
                self.assertEqual(resolver.resolve_episode(root, "current"), 0)

            assets = json.loads((episode / "assets.json").read_text(encoding="utf-8"))
            timeline = json.loads((episode / "timeline.json").read_text(encoding="utf-8"))
            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )
            chosen = next(item for item in assets["assets"] if item["id"] == "slot_a")
            self.assertEqual(chosen["file"], "novel.jpg")

            scored = {item["name"]: item for item in report["candidate_scores"]["slot_a"]}
            repeated = scored["repeated"]
            self.assertEqual(repeated["visual_score"], 95.0)
            self.assertLess(repeated["selection_score"], scored["novel"]["selection_score"])
            self.assertTrue(repeated["repetition"]["repetition_detected"])
            self.assertLess(repeated["repetition"]["penalty"], 0)
            self.assertTrue(repeated["eligible_for_auto_selection"])
            self.assertFalse(repeated["repetition"]["blocked"])
            self.assertEqual(repeated["repetition"]["matches"][0]["episode"], "older_episode")
            self.assertEqual(repeated["repetition"]["matches"][0]["method"], "url")

            for manifest in (assets, timeline):
                serialized = json.dumps(manifest)
                self.assertNotIn("fingerprint", serialized)
                self.assertNotIn("sha256", serialized)
                self.assertNotIn("url_hashes", serialized)
                self.assertNotIn("repetition", serialized)
                self.assertNotIn("visual_score", serialized)
                self.assertNotIn("selection_score", serialized)
                self.assertNotIn("score_breakdown", serialized)
            self.assertTrue(report["visual_usage"])
            self.assertIn("fingerprint", report["visual_usage"][0])

    def test_recency_weakens_penalty_and_only_repeated_candidate_remains_fallback(self):
        repeated_url = "https://upload.wikimedia.org/repeated-only.jpg"
        fingerprint = VisualFingerprint.from_dict(
            {"kind": "image", "urls": [repeated_url]}
        )
        recent = assess_repetition(fingerprint, (_history_entry(repeated_url),))
        old_entry = _history_entry(repeated_url, recency_rank=12)
        older = assess_repetition(fingerprint, (old_entry,))
        self.assertTrue(recent.is_repeated)
        self.assertGreater(older.penalty, recent.penalty)
        self.assertFalse(older.blocked)

        candidate = {
            "name": "only-repeat",
            "kind": "image",
            "url": repeated_url,
            "file": "only-repeat.jpg",
            "width": 1200,
            "height": 1600,
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode = self._write_episode(
                root,
                "current",
                [candidate],
                inspect_top=1,
                min_visual_score=100.0,
            )
            with (
                patch.object(resolver, "load_visual_history", return_value=((old_entry,), ())),
                patch.object(
                    resolver,
                    "inspect_visual_result",
                    side_effect=self._mock_inspector({"only-repeat": 90.0}),
                ),
            ):
                self.assertEqual(resolver.resolve_episode(root, "current"), 0)

            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )
            selection = report["selections"]["slot_a"]
            self.assertEqual(selection["status"], "selected")
            self.assertEqual(selection["selection_mode"], "best_same_kind_fallback")
            self.assertTrue(selection["repetition"]["repetition_detected"])
            self.assertFalse(selection["repetition"]["blocked"])

    def test_corrupt_or_unavailable_history_never_aborts_resolution(self):
        candidate = {
            "name": "usable",
            "kind": "image",
            "url": "https://upload.wikimedia.org/usable.jpg",
            "file": "usable.jpg",
            "width": 1200,
            "height": 1600,
        }
        with self.subTest("corrupt report"), TemporaryDirectory() as directory:
            root = Path(directory)
            broken = root / "episodes" / "broken"
            broken.mkdir(parents=True)
            (broken / "visual_resolution_report.json").write_text("{broken", encoding="utf-8")
            episode = self._write_episode(root, "current", [candidate])
            with patch.object(
                resolver,
                "inspect_visual_result",
                side_effect=self._mock_inspector({"usable": 80.0}),
            ):
                self.assertEqual(resolver.resolve_episode(root, "current"), 0)
            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["selections"]["slot_a"]["status"], "selected")
            self.assertTrue(
                any("corrompido" in warning for warning in report["repetition_history_warnings"])
            )

        with self.subTest("loader error"), TemporaryDirectory() as directory:
            root = Path(directory)
            episode = self._write_episode(root, "current", [candidate])
            with (
                patch.object(resolver, "load_visual_history", side_effect=RuntimeError("boom")),
                patch.object(
                    resolver,
                    "inspect_visual_result",
                    side_effect=self._mock_inspector({"usable": 80.0}),
                ),
            ):
                self.assertEqual(resolver.resolve_episode(root, "current"), 0)
            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["selections"]["slot_a"]["status"], "selected")
            self.assertIn("Historico visual indisponivel", report["repetition_history_warnings"][0])

    def test_url_repeat_is_removed_from_top_one_shortlist_before_download(self):
        repeated_url = "https://upload.wikimedia.org/top-metadata-repeat.jpg"
        candidates = [
            {
                "name": "metadata-strong-repeat",
                "kind": "image",
                "url": repeated_url,
                "file": "repeat.jpg",
                "width": 5000,
                "height": 7000,
                "editorial_rank": 1,
            },
            {
                "name": "metadata-weaker-novel",
                "kind": "image",
                "url": "https://upload.wikimedia.org/weaker-novel.jpg",
                "file": "novel.jpg",
                "width": 700,
                "height": 1000,
                "editorial_rank": 9,
            },
        ]
        inspected_names: list[str] = []

        def inspect(project_root: Path, result: VisualSearchResult, **kwargs):
            inspected_names.append(result.name)
            return self._mock_inspector({result.name: 70.0})(project_root, result, **kwargs)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_history_report(root, _history_entry(repeated_url))
            episode = self._write_episode(root, "current", candidates, inspect_top=1)
            with patch.object(resolver, "inspect_visual_result", side_effect=inspect):
                self.assertEqual(resolver.resolve_episode(root, "current"), 0)

            self.assertEqual(inspected_names, ["metadata-weaker-novel"])
            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )
            outside = next(
                item
                for item in report["candidate_scores"]["slot_a"]
                if item["name"] == "metadata-strong-repeat"
            )
            self.assertEqual(outside["inspection_status"], "outside_shortlist")
            self.assertTrue(outside["repetition"]["repetition_detected"])

    def test_generated_report_can_be_reloaded_as_history(self):
        selected_url = "https://upload.wikimedia.org/report-roundtrip.jpg"
        candidate = {
            "name": "roundtrip",
            "kind": "image",
            "url": selected_url,
            "file": "roundtrip.jpg",
            "width": 1200,
            "height": 1600,
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode = self._write_episode(root, "current", [candidate])
            with patch.object(
                resolver,
                "inspect_visual_result",
                side_effect=self._mock_inspector({"roundtrip": 82.0}),
            ):
                self.assertEqual(resolver.resolve_episode(root, "current"), 0)
                self.assertEqual(resolver.resolve_episode(root, "current"), 0)

            history, warnings = load_visual_history(root)
            self.assertFalse(warnings)
            self.assertEqual(len(history), 1)
            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(report["visual_usage"]), 1)
            self.assertFalse(report["selections"]["slot_a"]["repetition"]["is_repeated"])
            current = next(entry for entry in history if entry.episode == "current")
            self.assertEqual(current.shot_id, "shot_a")
            self.assertEqual(current.asset_id, "slot_a")
            self.assertTrue(current.fingerprint.url_hashes)
            self.assertTrue(
                assess_repetition(
                    VisualFingerprint.from_dict(
                        {"kind": "image", "urls": [selected_url]}
                    ),
                    history,
                ).is_repeated
            )

    def test_inspection_fingerprints_only_real_required_source_window(self):
        candidate = _result(
            "long-video",
            "https://upload.wikimedia.org/long-video.webm",
            kind="video",
        )
        fingerprint_value = VisualFingerprint.from_dict(
            {
                "kind": "video",
                "urls": [candidate.download_url],
                "source_start_seconds": 10.0,
                "source_duration_seconds": 6.75,
            }
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cached = root / "long-video.webm"
            cached.write_bytes(b"fixture")
            with (
                patch("engine.visual_search.download_to_cache", return_value=cached),
                patch(
                    "engine.visual_search.probe_video_stream",
                    return_value=VideoStreamInfo(100.0, 1920, 1080, 30.0),
                ),
                patch(
                    "engine.visual_search.analyze_video_motion",
                    side_effect=RuntimeError("fixture has no real frames"),
                ),
                patch(
                    "engine.visual_search.build_visual_fingerprint",
                    return_value=fingerprint_value,
                ) as build,
            ):
                inspected = inspect_visual_result(
                    root,
                    candidate,
                    shot_duration_seconds=4.0,
                    source_start_seconds=10.0,
                    source_end_seconds=90.0,
                    crossfade_seconds=0.5,
                    speed=1.5,
                    repetition_history=(),
                )

            self.assertEqual(inspected.trim.required_seconds, 6.75)
            self.assertEqual(build.call_args.kwargs["source_start_seconds"], 10.0)
            self.assertEqual(build.call_args.kwargs["source_duration_seconds"], 6.75)
            self.assertNotEqual(
                build.call_args.kwargs["source_duration_seconds"],
                90.0 - 10.0,
            )

    def test_legacy_episode_without_report_still_supplies_url_history(self):
        legacy_url = "https://upload.wikimedia.org/legacy.jpg?utm_source=old"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode = root / "episodes" / "legacy"
            episode.mkdir(parents=True)
            (episode / "assets.json").write_text(
                json.dumps(
                    {
                        "assets": [
                            {"id": "legacy_asset", "file": "legacy.jpg", "url": legacy_url}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (episode / "timeline.json").write_text(
                json.dumps(
                    {
                        "shots": [
                            {"id": "legacy_shot", "asset": "legacy_asset"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            candidate = _result(
                "legacy-candidate",
                "https://upload.wikimedia.org/legacy.jpg?utm_medium=new",
            )
            repeated = apply_visual_repetition(
                root,
                _inspection(candidate, 88.0),
                exclude_episode="current",
            )

            self.assertIsNotNone(repeated.repetition)
            self.assertTrue(repeated.repetition.is_repeated)
            self.assertEqual(repeated.repetition.matches[0].episode, "legacy")
            self.assertEqual(repeated.repetition.matches[0].method, "url")

    def test_rendered_usage_overrides_resolution_and_legacy_without_duplicate_penalty(self):
        selected_url = "https://upload.wikimedia.org/selected.jpg"
        rendered_url = "https://upload.wikimedia.org/rendered.jpg"
        candidate = {
            "name": "selected",
            "kind": "image",
            "url": selected_url,
            "file": "selected.jpg",
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode = self._write_episode(root, "prior", [candidate])
            selected = replace(
                _history_entry(selected_url, episode="prior"),
                shot_id="shot_a",
                asset_id="slot_a",
            )
            self._write_history_report(root, selected)
            rendered = replace(
                selected,
                fingerprint=VisualFingerprint.from_dict(
                    {"kind": "image", "urls": [rendered_url]}
                ),
            )
            (episode / "visual_usage.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "episode": "prior",
                        "recorded_at": rendered.recorded_at,
                        "visual_usage": [rendered.as_dict()],
                    }
                ),
                encoding="utf-8",
            )

            history, warnings = load_visual_history(root, exclude_episode="current")

        self.assertFalse(warnings)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].fingerprint, rendered.fingerprint)
        self.assertFalse(assess_repetition(selected.fingerprint, history).is_repeated)
        assessed = assess_repetition(rendered.fingerprint, history)
        self.assertEqual(assessed.penalty, assess_repetition(rendered.fingerprint, (rendered,)).penalty)
        self.assertEqual(len(assessed.matches), 1)

    def test_fingerprint_failures_keep_url_penalty_and_technical_score(self):
        candidate = _result("unhashable", "https://upload.wikimedia.org/unhashable.jpg")
        base = _inspection(candidate, 93.0)
        history = (_history_entry(candidate.download_url),)
        for error in (RuntimeError("decoder failed"), OSError("cache unreadable")):
            with self.subTest(error=type(error).__name__), TemporaryDirectory() as directory, patch(
                "engine.visual_search.build_visual_fingerprint", side_effect=error
            ):
                assessed = apply_visual_repetition(Path(directory), base, history)

            self.assertEqual(assessed.visual_score, 93.0)
            self.assertEqual(assessed.score_breakdown, base.score_breakdown)
            self.assertTrue(assessed.repetition.is_repeated)
            self.assertFalse(assessed.repetition.blocked)
            self.assertLess(assessed.selection_score, assessed.visual_score)
            self.assertEqual(assessed.repetition.matches[0].method, "url")
            self.assertTrue(any("Fingerprint visual indisponivel" in warning for warning in assessed.warnings))
            self.assertIsNone(base.fingerprint)
            self.assertIsNone(base.repetition)

    def test_core_and_web_fingerprint_speed_freeze_and_crossfade_consumed_interval(self):
        # A 12-frame freeze at 24 fps adds 11 frames. The original frame is still
        # consumed from the source: (4 + 0.5 - 11/24) * 1.5 = 6.0625 seconds.
        expected_duration = 6.0625
        for route in ("core", "web_direct", "web_youtube"):
            with self.subTest(route=route), TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory)
                candidate = _result(
                    "freeze-video", "https://upload.wikimedia.org/freeze-video.webm", kind="video"
                )
                inspector = inspect_visual_result if route == "core" else web_search.inspect_candidate
                module = "engine.visual_search"
                downloader = "engine.visual_search.download_to_cache"
                if route == "web_youtube":
                    candidate = replace(
                        candidate,
                        source_page_url="https://www.youtube.com/watch?v=fixture",
                        download_url=None,
                        search_provider="youtube_web",
                    )
                    module = "engine.visual_search_web"
                    downloader = "engine.visual_search_web._download_web_video"
                identity = candidate.download_url or candidate.source_page_url
                fingerprint = VisualFingerprint.from_dict(
                    {
                        "kind": "video",
                        "urls": [identity],
                        "source_start_seconds": 10.0,
                        "source_duration_seconds": expected_duration,
                    }
                )
                prior = replace(_history_entry(identity, kind="video"), fingerprint=fingerprint)
                cached = root / "freeze-video.webm"
                cached.write_bytes(b"fixture")
                stack.enter_context(patch(downloader, return_value=cached))
                stack.enter_context(
                    patch(f"{module}.probe_video_stream", return_value=VideoStreamInfo(100.0, 1920, 1080, 30.0))
                )
                stack.enter_context(
                    patch(f"{module}.analyze_video_motion", side_effect=RuntimeError("no fixture frames"))
                )
                build = stack.enter_context(
                    patch("engine.visual_search.build_visual_fingerprint", return_value=fingerprint)
                )

                inspected = inspector(
                    root,
                    candidate,
                    shot_duration_seconds=4.0,
                    source_start_seconds=10.0,
                    source_end_seconds=90.0,
                    crossfade_seconds=0.5,
                    speed=1.5,
                    freeze_start_seconds=1.0,
                    freeze_duration_seconds=0.5,
                    output_fps=24,
                    repetition_history=(prior,),
                    exclude_episode="current",
                )

                self.assertTrue(inspected.trim.safe_for_shot)
                self.assertEqual(inspected.trim.required_seconds, expected_duration)
                self.assertEqual(inspected.trim.available_seconds, 80.0)
                self.assertEqual(inspected.trim.freeze_duration_frames, 12)
                build.assert_called_once_with(
                    cached,
                    "video",
                    urls=(identity,),
                    source_start_seconds=10.0,
                    source_duration_seconds=expected_duration,
                )
                self.assertTrue(inspected.repetition.is_repeated)
                self.assertFalse(inspected.repetition.blocked)

    def test_inspection_without_known_shot_duration_never_hashes_entire_video(self):
        candidate = _result("unbounded-video", "https://upload.wikimedia.org/unbounded.webm", kind="video")
        with TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            cached = root / "unbounded.webm"
            cached.write_bytes(b"fixture")
            stack.enter_context(patch("engine.visual_search.download_to_cache", return_value=cached))
            stack.enter_context(
                patch("engine.visual_search.probe_video_stream", return_value=VideoStreamInfo(100.0, 1920, 1080, 30.0))
            )
            stack.enter_context(
                patch("engine.visual_search.analyze_video_motion", side_effect=RuntimeError("no fixture frames"))
            )
            build = stack.enter_context(patch("engine.visual_search.build_visual_fingerprint"))
            inspected = inspect_visual_result(root, candidate, source_start_seconds=10.0, repetition_history=())

        build.assert_not_called()
        self.assertIsNone(inspected.fingerprint.source_duration_seconds)
        self.assertTrue(inspected.fingerprint.url_hashes)
        self.assertTrue(any("duracao de shot/trim seguro" in warning for warning in inspected.warnings))

    def test_repetition_history_failure_preserves_technical_ranking(self):
        candidate = _result(
            "history-error",
            "https://upload.wikimedia.org/history-error.jpg",
        )
        base = _inspection(candidate, 77.0)
        with TemporaryDirectory() as directory, patch(
            "engine.visual_search.load_visual_history",
            side_effect=RuntimeError("history unavailable"),
        ):
            assessed = apply_visual_repetition(Path(directory), base)

        self.assertEqual(assessed.selection_score, 77.0)
        self.assertIsNone(assessed.repetition)
        self.assertTrue(
            any("Anti-repeticao visual indisponivel" in item for item in assessed.warnings)
        )

    def test_web_inspection_forwards_playback_and_composes_rights_with_repetition(self):
        history = (_history_entry("https://example.com/prior.webm", kind="video"),)
        slot = {
            "id": "slot_a",
            "required_seconds": 4.0,
            "crossfade_seconds": 0.25,
            "_shot": {
                "speed": 1.5,
                "freeze_frame": {"start_seconds": 1.0, "duration_seconds": 0.5},
            },
            "_output_fps": 24,
            "_repetition_history": history,
            "_episode": "current",
        }
        candidate = {
            "name": "web-video",
            "kind": "video",
            "url": "https://cdn.example.com/web-video.webm",
            "file": "web-video.webm",
            "license": "All Rights Reserved",
            "rights_status": "restricted",
            "source_start_seconds": 2.0,
            "source_end_seconds": 12.0,
            "width": 1080,
            "height": 1920,
        }

        def inspected(_root: Path, result: VisualSearchResult, **_kwargs):
            return _inspection(
                result,
                95.0,
                trim=TrimAssessment(True, 2.0, 12.0, 10.0, 6.0, 4.0, "safe"),
                repetition=RepetitionAssessment(True, -20.0),
            )

        with TemporaryDirectory() as directory, patch.object(
            web_resolver,
            "inspect_web_candidate",
            side_effect=inspected,
        ) as inspect:
            result, ranked, reasons = web_resolver._inspect_candidate(
                Path(directory), slot, candidate, 1
            )

        forwarded = inspect.call_args.kwargs
        self.assertEqual(forwarded["speed"], 1.5)
        self.assertEqual(forwarded["freeze_start_seconds"], 1.0)
        self.assertEqual(forwarded["freeze_duration_seconds"], 0.5)
        self.assertEqual(forwarded["output_fps"], 24)
        self.assertIs(forwarded["repetition_history"], history)
        self.assertEqual(forwarded["exclude_episode"], "current")
        self.assertFalse(reasons)
        self.assertEqual(result.name, "web-video")
        self.assertEqual(ranked.technical_visual_score, 95.0)
        self.assertEqual(ranked.base.selection_score, 75.0)
        self.assertEqual(ranked.rights_rank_adjustment, -12.0)
        self.assertEqual(ranked.visual_score, 63.0)
        record = web_resolver._score_record(1, candidate, result, ranked, reasons)
        self.assertEqual(record["visual_score"], 95.0)
        self.assertEqual(record["selection_score"], 63.0)
        self.assertTrue(record["repetition"]["repetition_detected"])
        self.assertEqual(record["rights_status"], "restricted")

    def test_web_resolver_selects_novel_visual_and_reports_advisory_adjustments(self):
        repeated_url = "https://cdn.example.com/repeated.jpg"
        candidates = [
            {
                "name": "repeated",
                "kind": "image",
                "url": repeated_url,
                "file": "repeated.jpg",
                "license": "All Rights Reserved",
                "rights_status": "restricted",
                "width": 5000,
                "height": 7000,
                "editorial_rank": 1,
            },
            {
                "name": "novel",
                "kind": "image",
                "url": "https://cdn.example.com/novel.jpg",
                "file": "novel.jpg",
                "width": 1000,
                "height": 1400,
                "editorial_rank": 2,
            },
        ]
        for inspect_top in (1, 2):
            with self.subTest(inspect_top=inspect_top), TemporaryDirectory() as directory:
                root = Path(directory)
                self._write_history_report(root, _history_entry(repeated_url))
                episode = self._write_episode(root, "current", candidates, inspect_top=inspect_top)
                # These are the same resolver hooks installed by the web CLI.
                # Scope them to this test so later core resolver tests stay isolated.
                with (
                    patch.multiple(
                        resolver,
                        _candidate_result=web_resolver._candidate_result,
                        _inspect_candidate=web_resolver._inspect_candidate,
                        _score_record=web_resolver._score_record,
                        _asset_entry=web_resolver._asset_entry,
                    ),
                    patch.object(
                        web_resolver,
                        "inspect_web_candidate",
                        side_effect=self._mock_inspector({"repeated": 95.0, "novel": 80.0}),
                    ) as inspect,
                ):
                    self.assertEqual(resolver.resolve_episode(root, "current"), 0)

                self.assertEqual(inspect.call_count, inspect_top)
                report = json.loads(
                    (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
                )
                selection = report["selections"]["slot_a"]
                self.assertEqual(selection["name"], "novel")
                self.assertEqual(selection["selection_mode"], "eligible")
                self.assertEqual(selection["visual_score"], 80.0)
                self.assertEqual(selection["selection_score"], 80.0)
                repeated = next(
                    record for record in report["candidate_scores"]["slot_a"]
                    if record["name"] == "repeated"
                )
                self.assertTrue(repeated["repetition"]["is_repeated"])
                self.assertFalse(repeated["repetition"]["blocked"])
                if inspect_top == 1:
                    self.assertEqual(repeated["inspection_status"], "outside_shortlist")
                else:
                    self.assertEqual(repeated["visual_score"], 95.0)
                    self.assertEqual(repeated["rights_rank_adjustment"], -12.0)
                    self.assertEqual(
                        repeated["selection_score"],
                        95.0 + repeated["repetition"]["penalty"] - 12.0,
                    )
                    self.assertTrue(repeated["eligible_for_auto_selection"])
                    self.assertFalse(repeated["rights_blocks_selection"])
                for filename in ("assets.json", "timeline.json"):
                    manifest = (episode / filename).read_text(encoding="utf-8")
                    for private_key in ("fingerprint", "repetition", "visual_score", "selection_score"):
                        self.assertNotIn(private_key, manifest)

    def test_failed_candidate_inspection_keeps_available_repeat_as_eligible(self):
        repeated_url = "https://upload.wikimedia.org/available-repeat.jpg"
        candidates = [
            {
                "name": "broken",
                "kind": "image",
                "url": "https://upload.wikimedia.org/broken.jpg",
                "file": "broken.jpg",
            },
            {
                "name": "available-repeat",
                "kind": "image",
                "url": repeated_url,
                "file": "available-repeat.jpg",
            },
        ]

        def inspect(project_root: Path, result: VisualSearchResult, **kwargs):
            if result.name == "broken":
                raise RuntimeError("fixture download failed")
            return self._mock_inspector({"available-repeat": 90.0})(project_root, result, **kwargs)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_history_report(root, _history_entry(repeated_url))
            episode = self._write_episode(root, "current", candidates, min_visual_score=80.0)
            with patch.object(resolver, "inspect_visual_result", side_effect=inspect):
                self.assertEqual(resolver.resolve_episode(root, "current"), 0)

            report = json.loads(
                (episode / "visual_resolution_report.json").read_text(encoding="utf-8")
            )

        selection = report["selections"]["slot_a"]
        self.assertEqual(selection["status"], "selected")
        self.assertEqual(selection["name"], "available-repeat")
        self.assertEqual(selection["selection_mode"], "eligible")
        self.assertLess(selection["selection_score"], 80.0)
        self.assertFalse(selection["repetition"]["blocked"])
        self.assertEqual(len(report["inspection_failures"]), 1)
        self.assertIn("fixture download failed", report["inspection_failures"][0])


if __name__ == "__main__":
    unittest.main()
