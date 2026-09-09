from __future__ import annotations

from pathlib import Path
import unittest

from engine.models import (
    AssetSpec,
    Episode,
    ScriptSegment,
    ShotSpec,
    Story,
    TimelinePlan,
    TimelineScene,
)
from engine.visual_pacing import (
    assert_estimated_visual_pacing,
    assert_resolved_visual_pacing,
    evaluate_estimated_visual_pacing,
    evaluate_resolved_visual_pacing,
)


def _asset(asset_id: str, file: str) -> AssetSpec:
    return AssetSpec(
        id=asset_id,
        file=file,
        url=None,
        credit="fixture",
        license="",
        focus_x=0.5,
        focus_y=0.5,
    )


def _shot(shot_id: str, segment_id: str, asset_id: str) -> ShotSpec:
    return ShotSpec(
        id=shot_id,
        segment_id=segment_id,
        asset_id=asset_id,
        motion="hold",
        transition_out="cut",
    )


def _scene(
    *,
    asset: AssetSpec,
    duration_seconds: float,
    fps: int = 30,
    start_seconds: float = 0.0,
    index: int = 1,
) -> TimelineScene:
    start_frame = round(start_seconds * fps)
    end_frame = start_frame + round(duration_seconds * fps)
    shot = _shot(f"shot_{index}", f"seg_{index}", asset.id)
    return TimelineScene(
        index=index,
        shot=shot,
        asset=asset,
        start_frame=start_frame,
        end_frame=end_frame,
        render_frames=end_frame - start_frame,
        transition_frames=0,
    )


class EstimatedVisualPacingTests(unittest.TestCase):
    def _episode(self, first_file: str, target: float = 78.0) -> Episode:
        first = _asset("first", first_file)
        second = _asset("second", "second.mp4")
        story = Story(
            "Fixture",
            "fixture",
            (
                ScriptSegment(
                    "hook",
                    "Fatboy Slim so estreou no Rock in Rio depois de transformar DJ em headliner de festival",
                ),
                ScriptSegment(
                    "body",
                    "Depois a historia continua com contexto suficiente para completar a narracao",
                ),
            ),
            target_duration_seconds=target,
        )
        shots = (
            _shot("shot_1", "hook", "first"),
            _shot("shot_2", "body", "second"),
        )
        return Episode(
            "fixture",
            Path("/tmp/fixture"),
            story,
            {"first": first, "second": second},
            shots,
        )

    def test_long_static_hook_is_blocked_before_media_preflight(self):
        episode = self._episode("first.jpg")
        report = evaluate_estimated_visual_pacing(episode)
        self.assertIn(
            "ESTIMATED_STATIC_HOLD_TOO_LONG",
            {issue.code for issue in report.blocking_issues},
        )
        with self.assertRaisesRegex(RuntimeError, "VISUAL_PACING_BLOCKED stage=estimated"):
            assert_estimated_visual_pacing(episode)

    def test_long_video_hook_is_warning_not_block(self):
        episode = self._episode("first.mp4")
        report = evaluate_estimated_visual_pacing(episode)
        self.assertTrue(report.ok)
        self.assertIn(
            "ESTIMATED_OPENING_VIDEO_LONG_HOLD",
            {issue.code for issue in report.warnings},
        )

    def test_normal_static_duration_estimate_passes(self):
        photo = _asset("photo", "photo.jpg")
        video = _asset("video", "video.mp4")
        story = Story(
            "Fixture",
            "fixture",
            (
                ScriptSegment("a", "one two three four five"),
                ScriptSegment("b", "six seven eight nine ten"),
            ),
            target_duration_seconds=6.0,
        )
        episode = Episode(
            "fixture",
            Path("/tmp/fixture"),
            story,
            {"photo": photo, "video": video},
            (
                _shot("shot_a", "a", "photo"),
                _shot("shot_b", "b", "video"),
            ),
        )
        report = evaluate_estimated_visual_pacing(episode)
        self.assertFalse(report.blocking_issues)


class ResolvedVisualPacingTests(unittest.TestCase):
    def _plan(self, scene: TimelineScene, duration: float | None = None) -> TimelinePlan:
        fps = 30
        total_frames = round(duration * fps) if duration is not None else scene.end_frame
        return TimelinePlan(
            fps=fps,
            total_frames=total_frames,
            audio_duration=total_frames / fps,
            scenes=(scene,),
        )

    def test_static_image_at_two_seconds_passes(self):
        scene = _scene(asset=_asset("photo", "photo.jpg"), duration_seconds=2.0)
        self.assertTrue(evaluate_resolved_visual_pacing(self._plan(scene)).ok)

    def test_static_image_at_four_seconds_passes(self):
        scene = _scene(asset=_asset("photo", "photo.jpg"), duration_seconds=4.0)
        self.assertTrue(evaluate_resolved_visual_pacing(self._plan(scene)).ok)

    def test_static_image_over_four_seconds_is_blocked(self):
        scene = _scene(asset=_asset("photo", "photo.jpg"), duration_seconds=4.1)
        report = evaluate_resolved_visual_pacing(self._plan(scene))
        self.assertIn(
            "STATIC_HOLD_TOO_LONG",
            {issue.code for issue in report.blocking_issues},
        )
        with self.assertRaisesRegex(RuntimeError, "VISUAL_PACING_BLOCKED stage=resolved"):
            assert_resolved_visual_pacing(self._plan(scene))

    def test_static_image_under_two_seconds_is_blocked(self):
        scene = _scene(asset=_asset("photo", "photo.jpg"), duration_seconds=1.9)
        report = evaluate_resolved_visual_pacing(self._plan(scene))
        self.assertIn(
            "STATIC_HOLD_TOO_SHORT",
            {issue.code for issue in report.blocking_issues},
        )

    def test_long_video_is_warning_only(self):
        scene = _scene(asset=_asset("video", "video.mp4"), duration_seconds=7.0)
        report = evaluate_resolved_visual_pacing(self._plan(scene))
        self.assertTrue(report.ok)
        self.assertIn(
            "OPENING_VIDEO_LONG_HOLD",
            {issue.code for issue in report.warnings},
        )

    def test_long_short_with_few_takes_warns(self):
        scene = _scene(asset=_asset("video", "video.mp4"), duration_seconds=60.0)
        report = evaluate_resolved_visual_pacing(self._plan(scene, duration=60.0))
        codes = {issue.code for issue in report.warnings}
        self.assertIn("LOW_TAKE_COUNT", codes)
        self.assertIn("OPENING_LOW_CUT_DENSITY", codes)


if __name__ == "__main__":
    unittest.main()
