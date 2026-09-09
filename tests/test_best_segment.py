from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from PIL import Image

from engine.best_segment import (
    MAX_CANDIDATE_WINDOWS,
    SegmentWindowMetrics,
    analyze_segment_window,
    candidate_window_starts,
    choose_best_segment,
    score_segment_window,
    select_best_segment,
    select_best_segments_safely,
)
from engine.ffmpeg import VideoStreamInfo, run_ffmpeg
from engine.models import (
    AssetSpec,
    ResolvedFreezeFrame,
    ShotSpec,
    TimelinePlan,
    TimelineScene,
)
from engine.visual_repetition import VisualFingerprint, VisualHistoryEntry


def _metrics(
    start: float,
    duration: float = 3.0,
    *,
    motion: float = 70.0,
    sharpness: float = 70.0,
    exposure: float = 70.0,
    subject: float = 70.0,
    stability: float = 70.0,
    scene_changes: float = 70.0,
    samples: int = 7,
    practically_static: bool = False,
) -> SegmentWindowMetrics:
    return SegmentWindowMetrics(
        start_seconds=start,
        end_seconds=start + duration,
        motion_score=motion,
        sharpness_score=sharpness,
        exposure_score=exposure,
        subject_visibility_score=subject,
        stability_score=stability,
        scene_change_score=scene_changes,
        sample_count=samples,
        practically_static=practically_static,
    )


def _score(metrics: SegmentWindowMetrics):
    return score_segment_window(metrics)


def _asset(asset_id: str, file_name: str) -> AssetSpec:
    return AssetSpec(asset_id, file_name, None, "", "", 0.5, 0.5)


def _video_history_entry(
    fingerprint: VisualFingerprint,
    *,
    recency_rank: int = 0,
) -> VisualHistoryEntry:
    return VisualHistoryEntry(
        episode="older_episode",
        shot_id="shot_old",
        asset_id="asset_old",
        fingerprint=fingerprint,
        recorded_at="2026-08-01T12:00:00+00:00",
        recency_rank=recency_rank,
    )


class SegmentScoringTests(unittest.TestCase):
    def test_motion_only_gain_does_not_replace_multidimensional_baseline(self):
        baseline = _score(_metrics(0.0, motion=10.0))
        motion_only = _score(_metrics(5.0, motion=100.0))

        self.assertGreater(motion_only.score - baseline.score, 12.0)
        decision = choose_best_segment(0.0, None, 3.0, (baseline, motion_only))

        self.assertFalse(decision.changed)
        self.assertEqual(decision.reason, "motion_only_gain")
        self.assertEqual(decision.selected_start_seconds, 0.0)
        self.assertIsNone(decision.selected_end_seconds)

    def test_clearly_better_multidimensional_window_replaces_baseline(self):
        baseline = _score(
            _metrics(
                1.0,
                motion=32.0,
                sharpness=42.0,
                exposure=44.0,
                subject=38.0,
                stability=48.0,
                scene_changes=55.0,
            )
        )
        alternative = _score(
            _metrics(
                8.0,
                motion=78.0,
                sharpness=91.0,
                exposure=86.0,
                subject=88.0,
                stability=84.0,
                scene_changes=90.0,
            )
        )

        decision = choose_best_segment(1.0, 7.0, 3.0, (baseline, alternative))
        payload = decision.as_dict()

        self.assertTrue(decision.changed)
        self.assertEqual(decision.reason, "clear_multidimensional_gain")
        self.assertEqual(decision.selected_start_seconds, 8.0)
        self.assertEqual(decision.selected_end_seconds, 11.0)
        self.assertGreaterEqual(decision.gain, 12.0)
        self.assertGreaterEqual(decision.confidence, 0.75)
        self.assertEqual(payload["original_trim"]["source_start_seconds"], 1.0)
        self.assertEqual(payload["original_trim"]["source_end_seconds"], 7.0)
        self.assertEqual(payload["selected_trim"]["consumed_end_seconds"], 11.0)
        self.assertEqual(payload["original_score"], baseline.score)
        self.assertEqual(payload["new_score"], alternative.score)

    def test_small_gain_preserves_authored_start_and_none_end_exactly(self):
        original_start = 1.23456789

        def analyzer(_path, start, duration, **_kwargs):
            value = 75.0 if math.isclose(start, original_start, abs_tol=1e-5) else 81.0
            return _metrics(start, duration, motion=value, sharpness=value,
                            exposure=value, subject=value, stability=value,
                            scene_changes=value)

        decision = select_best_segment(
            Path("not-opened.mp4"),
            12.0,
            original_start_seconds=original_start,
            original_end_seconds=None,
            required_seconds=2.5,
            source_fps=30.0,
            analyzer=analyzer,
        )

        self.assertFalse(decision.changed)
        self.assertEqual(decision.reason, "gain_below_threshold")
        self.assertEqual(decision.selected_start_seconds, original_start)
        self.assertIsNone(decision.selected_end_seconds)
        self.assertEqual(decision.original_start_seconds, original_start)
        self.assertIsNone(decision.original_end_seconds)

    def test_low_confidence_keeps_original_despite_large_score_gain(self):
        baseline = _score(_metrics(1.0, motion=60.0, sharpness=60.0,
                                   exposure=60.0, subject=60.0,
                                   stability=60.0, scene_changes=60.0))
        incomplete = _score(_metrics(6.0, motion=95.0, sharpness=95.0,
                                     exposure=95.0, subject=95.0,
                                     stability=95.0, scene_changes=95.0,
                                     samples=4))

        decision = choose_best_segment(1.0, 5.0, 3.0, (baseline, incomplete))

        self.assertLess(incomplete.confidence, 0.75)
        self.assertFalse(decision.changed)
        self.assertEqual(decision.reason, "low_confidence")
        self.assertEqual(decision.selected_start_seconds, 1.0)
        self.assertEqual(decision.selected_end_seconds, 5.0)

    def test_practically_static_source_keeps_original(self):
        baseline = _score(_metrics(0.0, motion=5.0, practically_static=True))
        alternative = _score(
            _metrics(4.0, motion=8.0, sharpness=95.0, exposure=95.0,
                     subject=95.0, stability=95.0, scene_changes=95.0,
                     practically_static=True)
        )

        decision = choose_best_segment(0.0, None, 3.0, (baseline, alternative))

        self.assertFalse(decision.changed)
        self.assertEqual(decision.reason, "source_practically_static")
        self.assertLessEqual(baseline.score, 42.0)
        self.assertLessEqual(alternative.score, 42.0)

    def test_candidate_windows_are_bounded_by_real_media_duration(self):
        starts = candidate_window_starts(
            10.0,
            3.0,
            2.333333,
            source_fps=24.0,
        )

        self.assertLessEqual(len(starts), MAX_CANDIDATE_WINDOWS)
        self.assertIn(2.333333, starts)
        self.assertTrue(all(start >= 0.0 for start in starts))
        self.assertTrue(all(start + 3.0 <= 10.0 + 1e-6 for start in starts))
        self.assertLess(max(starts) + 3.0, 10.0)

    def test_frame_analyzer_uses_vertical_viewport_fast_seek_and_detects_static(self):
        calls = []

        def extract(arguments):
            calls.append(arguments)
            pattern = Path(arguments[-1])
            for index in range(1, 8):
                Image.new("RGB", (180, 320), (8, 8, 8)).save(
                    Path(str(pattern).replace("%03d", f"{index:03d}"))
                )

        with TemporaryDirectory() as directory:
            source = Path(directory) / "clip.mp4"
            source.write_bytes(b"mock source")
            with patch("engine.best_segment.run_ffmpeg", side_effect=extract):
                metrics = analyze_segment_window(source, 2.0, 4.0)

        arguments = calls[0]
        video_filter = arguments[arguments.index("-vf") + 1]
        self.assertLess(arguments.index("-ss"), arguments.index("-i"))
        self.assertIn("scale=180:320", video_filter)
        self.assertIn("force_original_aspect_ratio=increase", video_filter)
        self.assertIn("crop=180:320", video_filter)
        self.assertEqual(metrics.sample_count, 7)
        self.assertTrue(metrics.practically_static)
        self.assertEqual(metrics.unique_frame_ratio, 0.0)
        self.assertLess(metrics.exposure_score, 25.0)

    def test_ffmpeg_analyzer_handles_a_synthetic_static_landscape_video(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "static.mp4"
            run_ffmpeg(
                [
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=640x360:r=30:d=4",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    source,
                ]
            )
            metrics = analyze_segment_window(source, 0.25, 3.0)

        self.assertEqual(metrics.sample_count, 7)
        self.assertTrue(metrics.practically_static)
        self.assertEqual(metrics.unique_frame_ratio, 0.0)
        self.assertLess(metrics.exposure_score, 25.0)


class SegmentPlanIntegrationTests(unittest.TestCase):
    @staticmethod
    def _single_video_plan(
        *,
        start: float = 4.0,
        end: float | None = None,
        url: str | None = None,
    ) -> tuple[AssetSpec, TimelinePlan]:
        video = AssetSpec("clip", "clip.mp4", url, "", "", 0.5, 0.5)
        scene = TimelineScene(
            1,
            ShotSpec(
                "shot",
                "hook",
                video.id,
                "hold",
                "cut",
                source_start_seconds=start,
                source_end_seconds=end,
            ),
            video,
            0,
            30,
            30,
            0,
        )
        return video, TimelinePlan(10, 30, 3.0, (scene,))

    @staticmethod
    def _prefer_window(target_start: float):
        def analyzer(_path, start, duration, **_kwargs):
            value = 95.0 if math.isclose(start, target_start, abs_tol=1e-5) else 35.0
            return _metrics(
                start,
                duration,
                motion=value,
                sharpness=value,
                exposure=value,
                subject=value,
                stability=value,
                scene_changes=value,
            )

        return analyzer

    def test_plan_selection_uses_speed_freeze_and_crossfade_source_consumption(self):
        video = _asset("clip", "clip.mp4")
        image = _asset("photo", "photo.jpg")
        freeze = ResolvedFreezeFrame(start_frame=10, duration_frames=5)
        video_scene = TimelineScene(
            index=1,
            shot=ShotSpec(
                "shot_video",
                "hook",
                video.id,
                "hold",
                "crossfade",
                source_start_seconds=1.0,
                source_end_seconds=5.65,
                speed=1.5,
            ),
            asset=video,
            start_frame=0,
            end_frame=30,
            render_frames=35,
            transition_frames=5,
            freeze_frame=freeze,
        )
        image_scene = TimelineScene(
            index=2,
            shot=ShotSpec("shot_image", "body", image.id, "hold", "cut"),
            asset=image,
            start_frame=30,
            end_frame=50,
            render_frames=20,
            transition_frames=0,
        )
        plan = TimelinePlan(10, 50, 5.0, (video_scene, image_scene))
        required = video_scene.required_source_duration(plan.fps)
        analyzed_durations: list[float] = []

        def analyzer(_path, start, duration, **_kwargs):
            analyzed_durations.append(duration)
            value = 35.0 if math.isclose(start, 1.0, abs_tol=1e-5) else 92.0
            return _metrics(start, duration, motion=value, sharpness=value,
                            exposure=value, subject=value, stability=value,
                            scene_changes=value)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"mocked video; analyzer never opens it")
            report_path = root / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {video.id: source},
                {video.id: VideoStreamInfo(20.0, 1920, 1080, 25.0)},
                report_path,
                analyzer=analyzer,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertAlmostEqual(required, 4.65)
        self.assertTrue(analyzed_durations)
        self.assertTrue(all(math.isclose(value, required) for value in analyzed_durations))
        selected_scene = selected.scenes[0]
        self.assertNotEqual(selected_scene.shot.source_start_seconds, 1.0)
        self.assertAlmostEqual(
            selected_scene.shot.source_end_seconds
            - selected_scene.shot.source_start_seconds,
            required,
        )
        self.assertEqual(selected.total_frames, plan.total_frames)
        self.assertEqual(selected.audio_duration, plan.audio_duration)
        self.assertEqual(selected_scene.start_frame, video_scene.start_frame)
        self.assertEqual(selected_scene.end_frame, video_scene.end_frame)
        self.assertEqual(selected_scene.render_frames, video_scene.render_frames)
        self.assertEqual(selected_scene.transition_frames, video_scene.transition_frames)
        self.assertEqual(selected_scene.freeze_frame, freeze)
        self.assertEqual(selected_scene.shot.speed, 1.5)
        self.assertEqual(selected.scenes[1], image_scene)
        self.assertEqual(report["changed_shots"], 1)
        record = report["shots"][0]
        self.assertEqual(record["required_source_seconds"], 4.65)
        self.assertEqual(record["crossfade_seconds"], 0.5)
        self.assertEqual(record["speed"], 1.5)
        self.assertEqual(record["freeze_frame"], {"start_frame": 10, "duration_frames": 5})

    def test_image_only_plan_is_a_noop_and_never_calls_analyzer(self):
        image = _asset("photo", "photo.jpg")
        scene = TimelineScene(
            1,
            ShotSpec("shot", "hook", image.id, "hold", "cut"),
            image,
            0,
            30,
            30,
            0,
        )
        plan = TimelinePlan(30, 30, 1.0, (scene,))
        analyzer = Mock(side_effect=AssertionError("image must not be analyzed"))

        with TemporaryDirectory() as directory:
            report_path = Path(directory) / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {},
                {},
                report_path,
                analyzer=analyzer,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(selected, plan)
        analyzer.assert_not_called()
        self.assertEqual(report["changed_shots"], 0)
        self.assertEqual(report["shots"], [])

    def test_analyzer_failure_is_non_blocking_and_report_keeps_original_trim(self):
        secret = "https://example.invalid/video.mp4?token=do-not-log"
        video = _asset("clip", "clip.mp4")
        scene = TimelineScene(
            1,
            ShotSpec(
                "shot",
                "hook",
                video.id,
                "hold",
                "cut",
                source_start_seconds=2.0,
                source_end_seconds=None,
            ),
            video,
            0,
            30,
            30,
            0,
        )
        plan = TimelinePlan(10, 30, 3.0, (scene,))

        def failing_analyzer(*_args, **_kwargs):
            raise RuntimeError(secret)

        output = io.StringIO()
        with TemporaryDirectory() as directory, redirect_stdout(output):
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"mocked video")
            report_path = root / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {video.id: source},
                {video.id: VideoStreamInfo(12.0, 1280, 720, 30.0)},
                report_path,
                analyzer=failing_analyzer,
            )
            serialized = report_path.read_text(encoding="utf-8")
            report = json.loads(serialized)

        self.assertEqual(selected.scenes[0].shot.source_start_seconds, 2.0)
        self.assertIsNone(selected.scenes[0].shot.source_end_seconds)
        self.assertEqual(selected.scenes[0].render_frames, 30)
        self.assertEqual(report["changed_shots"], 0)
        self.assertEqual(report["shots"][0]["reason"], "analysis_unavailable")
        self.assertEqual(
            report["shots"][0]["selected_trim"],
            {
                "source_start_seconds": 2.0,
                "source_end_seconds": None,
                "consumed_end_seconds": 5.0,
            },
        )
        self.assertNotIn(secret, serialized)
        self.assertNotIn(secret, output.getvalue())

    def test_two_shots_of_same_source_do_not_converge_on_same_window(self):
        video = _asset("clip", "clip.mp4")
        scenes = tuple(
            TimelineScene(
                index,
                ShotSpec(
                    f"shot_{index}",
                    f"segment_{index}",
                    video.id,
                    "hold",
                    "cut",
                    source_start_seconds=start,
                ),
                video,
                (index - 1) * 30,
                index * 30,
                30,
                0,
            )
            for index, start in ((1, 4.0), (2, 8.0))
        )
        plan = TimelinePlan(10, 60, 6.0, scenes)

        def analyzer(_path, start, duration, **_kwargs):
            value = 95.0 if math.isclose(start, 0.0) else 35.0
            return _metrics(
                start,
                duration,
                motion=value,
                sharpness=value,
                exposure=value,
                subject=value,
                stability=value,
                scene_changes=value,
            )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"mocked video")
            report_path = root / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {video.id: source},
                {video.id: VideoStreamInfo(20.0, 1920, 1080, 30.0)},
                report_path,
                analyzer=analyzer,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        starts = [scene.shot.source_start_seconds for scene in selected.scenes]
        self.assertEqual(starts.count(0.0), 1)
        self.assertEqual(starts[1], 8.0)
        self.assertEqual(report["shots"][1]["reason"], "overlaps_another_shot")

    def test_recent_overlapping_same_source_segment_rejects_better_window(self):
        video, plan = self._single_video_plan()
        candidate_start = 6.266667
        source_digest = "a" * 64
        history = (
            _video_history_entry(
                VisualFingerprint(
                    kind="video",
                    sha256=source_digest,
                    source_start_seconds=6.4,
                    source_duration_seconds=3.0,
                )
            ),
        )

        def fingerprint_builder(_path, _kind, **kwargs):
            return VisualFingerprint(
                kind="video",
                sha256=source_digest,
                source_start_seconds=kwargs["source_start_seconds"],
                source_duration_seconds=kwargs["source_duration_seconds"],
            )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"mocked video")
            report_path = root / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {video.id: source},
                {video.id: VideoStreamInfo(20.0, 1920, 1080, 30.0)},
                report_path,
                analyzer=self._prefer_window(candidate_start),
                visual_history=history,
                fingerprint_builder=fingerprint_builder,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(selected.scenes[0].shot.source_start_seconds, 4.0)
        self.assertIsNone(selected.scenes[0].shot.source_end_seconds)
        self.assertEqual(report["changed_shots"], 0)
        record = report["shots"][0]
        self.assertEqual(record["reason"], "repeated_recent_segment")
        repetition = record["historical_repetition_check"]
        self.assertTrue(repetition["checked"])
        self.assertTrue(repetition["strong_match"])
        self.assertEqual(repetition["reason"], "strong_recent_match")
        self.assertEqual(repetition["matches"][0]["method"], "source_range_overlap")

    def test_disjoint_same_source_segment_allows_better_window(self):
        video, plan = self._single_video_plan()
        candidate_start = 6.266667
        source_digest = "b" * 64
        history = (
            _video_history_entry(
                VisualFingerprint(
                    kind="video",
                    sha256=source_digest,
                    source_start_seconds=13.0,
                    source_duration_seconds=3.0,
                )
            ),
        )

        def fingerprint_builder(_path, _kind, **kwargs):
            return VisualFingerprint(
                kind="video",
                sha256=source_digest,
                source_start_seconds=kwargs["source_start_seconds"],
                source_duration_seconds=kwargs["source_duration_seconds"],
            )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"mocked video")
            report_path = root / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {video.id: source},
                {video.id: VideoStreamInfo(20.0, 1920, 1080, 30.0)},
                report_path,
                analyzer=self._prefer_window(candidate_start),
                visual_history=history,
                fingerprint_builder=fingerprint_builder,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertAlmostEqual(
            selected.scenes[0].shot.source_start_seconds,
            candidate_start,
        )
        self.assertAlmostEqual(selected.scenes[0].shot.source_end_seconds, 9.266667)
        self.assertEqual(report["changed_shots"], 1)
        record = report["shots"][0]
        self.assertEqual(record["reason"], "clear_multidimensional_gain")
        repetition = record["historical_repetition_check"]
        self.assertTrue(repetition["checked"])
        self.assertFalse(repetition["strong_match"])
        self.assertEqual(repetition["reason"], "no_strong_match")
        self.assertEqual(repetition["matches"], [])

    def test_matching_frames_reject_reencoded_recent_segment(self):
        video, plan = self._single_video_plan()
        candidate_start = 6.266667
        frame_hashes = ("a5" * 8,) * 8
        history = (
            _video_history_entry(
                VisualFingerprint(
                    kind="video",
                    sha256="d" * 64,
                    video_frame_hashes=frame_hashes,
                    source_start_seconds=1.0,
                    source_duration_seconds=3.0,
                )
            ),
        )

        def fingerprint_builder(_path, _kind, **kwargs):
            return VisualFingerprint(
                kind="video",
                sha256="e" * 64,
                video_frame_hashes=frame_hashes,
                source_start_seconds=kwargs["source_start_seconds"],
                source_duration_seconds=kwargs["source_duration_seconds"],
            )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"mocked video")
            report_path = root / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {video.id: source},
                {video.id: VideoStreamInfo(20.0, 1920, 1080, 30.0)},
                report_path,
                analyzer=self._prefer_window(candidate_start),
                visual_history=history,
                fingerprint_builder=fingerprint_builder,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(selected.scenes[0].shot.source_start_seconds, 4.0)
        self.assertEqual(report["shots"][0]["reason"], "repeated_recent_segment")
        self.assertEqual(
            report["shots"][0]["historical_repetition_check"]["matches"][0]["method"],
            "video_frames",
        )

    def test_fingerprint_failure_keeps_exact_baseline_without_leaking_secret(self):
        original_start = 4.125
        original_end = 8.25
        video, plan = self._single_video_plan(start=original_start, end=original_end)
        secret = "https://private.invalid/video.mp4?token=never-print-this"
        history = (
            _video_history_entry(
                VisualFingerprint(
                    kind="video",
                    sha256="c" * 64,
                    source_start_seconds=0.0,
                    source_duration_seconds=3.0,
                )
            ),
        )

        def failing_fingerprint_builder(*_args, **_kwargs):
            raise RuntimeError(secret)

        def analyzer(_path, start, duration, **_kwargs):
            value = 35.0 if math.isclose(start, original_start, abs_tol=1e-5) else 95.0
            return _metrics(
                start,
                duration,
                motion=value,
                sharpness=value,
                exposure=value,
                subject=value,
                stability=value,
                scene_changes=value,
            )

        output = io.StringIO()
        with TemporaryDirectory() as directory, redirect_stdout(output):
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"mocked video")
            report_path = root / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {video.id: source},
                {video.id: VideoStreamInfo(20.0, 1920, 1080, 30.0)},
                report_path,
                analyzer=analyzer,
                visual_history=history,
                fingerprint_builder=failing_fingerprint_builder,
            )
            serialized = report_path.read_text(encoding="utf-8")
            report = json.loads(serialized)

        shot = selected.scenes[0].shot
        self.assertEqual(shot.source_start_seconds, original_start)
        self.assertEqual(shot.source_end_seconds, original_end)
        self.assertEqual(report["changed_shots"], 0)
        record = report["shots"][0]
        self.assertEqual(record["reason"], "repetition_check_unavailable")
        self.assertEqual(
            record["selected_trim"],
            {
                "source_start_seconds": original_start,
                "source_end_seconds": original_end,
                "consumed_end_seconds": original_start + 3.0,
            },
        )
        self.assertFalse(record["historical_repetition_check"]["available"])
        self.assertEqual(
            record["historical_repetition_check"]["reason"],
            "check_unavailable",
        )
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(secret, serialized)

    def test_remote_match_outside_recent_history_window_does_not_block(self):
        remote_url = "https://cdn.example.invalid/archive/clip.mp4"
        video, plan = self._single_video_plan(url=remote_url)
        candidate_start = 6.266667
        history = (
            _video_history_entry(
                VisualFingerprint.from_dict(
                    {
                        "kind": "video",
                        "urls": [remote_url],
                        "source_start_seconds": candidate_start,
                        "source_duration_seconds": 3.0,
                    }
                ),
                recency_rank=12,
            ),
        )

        def fingerprint_builder(_path, _kind, **kwargs):
            return VisualFingerprint.from_dict(
                {
                    "kind": "video",
                    "urls": kwargs["urls"],
                    "source_start_seconds": kwargs["source_start_seconds"],
                    "source_duration_seconds": kwargs["source_duration_seconds"],
                }
            )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"mocked video")
            report_path = root / "best_segment_selection.json"
            selected = select_best_segments_safely(
                plan,
                {video.id: source},
                {video.id: VideoStreamInfo(20.0, 1920, 1080, 30.0)},
                report_path,
                analyzer=self._prefer_window(candidate_start),
                visual_history=history,
                fingerprint_builder=fingerprint_builder,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertAlmostEqual(
            selected.scenes[0].shot.source_start_seconds,
            candidate_start,
        )
        repetition = report["shots"][0]["historical_repetition_check"]
        self.assertTrue(repetition["checked"])
        self.assertFalse(repetition["strong_match"])
        self.assertEqual(repetition["matches"], [])


if __name__ == "__main__":
    unittest.main()
