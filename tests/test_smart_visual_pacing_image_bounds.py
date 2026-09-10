from __future__ import annotations

import unittest

from engine.ffmpeg import VideoStreamInfo
from engine.models import AssetSpec, ScriptSegment, ShotSpec, Story, TimelinePlan, TimelineScene
from engine.smart_visual_pacing import apply_smart_visual_pacing


FPS = 10


def _asset(index: int, media_type: str) -> AssetSpec:
    extension = ".mp4" if media_type == "video" else ".jpg"
    return AssetSpec(
        id=f"asset_{index}",
        file=f"asset_{index}{extension}",
        url=None,
        credit="",
        license="",
        focus_x=None,
        focus_y=None,
    )


def _plan(frame_counts: tuple[int, ...], media_types: tuple[str, ...]) -> TimelinePlan:
    scenes: list[TimelineScene] = []
    start = 0
    for index, (frames, media_type) in enumerate(zip(frame_counts, media_types)):
        asset = _asset(index, media_type)
        shot = ShotSpec(
            id=f"shot_{index}",
            segment_id=f"segment_{index}",
            asset_id=asset.id,
            motion="hold",
            transition_out="cut",
            source_start_seconds=0.0,
            source_end_seconds=None,
        )
        scenes.append(
            TimelineScene(
                index=index + 1,
                shot=shot,
                asset=asset,
                start_frame=start,
                end_frame=start + frames,
                render_frames=frames,
                transition_frames=0,
            )
        )
        start += frames
    return TimelinePlan(
        fps=FPS,
        total_frames=start,
        audio_duration=start / FPS,
        scenes=tuple(scenes),
    )


def _story(scene_count: int) -> Story:
    return Story(
        title="Image bounds",
        slug="image-bounds",
        target_duration_seconds=10.0,
        segments=tuple(
            ScriptSegment(
                id=f"segment_{index}",
                text="Abertura forte" if index == 0 else "Contexto visual calmo",
            )
            for index in range(scene_count)
        ),
    )


def _apply(plan: TimelinePlan, motion: dict[str, object] | None = None) -> TimelinePlan:
    infos = {
        scene.asset.id: VideoStreamInfo(120.0, 1920, 1080, 30.0)
        for scene in plan.scenes
        if scene.asset.is_video
    }
    adjusted, _report = apply_smart_visual_pacing(
        plan,
        _story(len(plan.scenes)),
        enabled=True,
        crossfade_seconds=0.0,
        video_motion_by_asset=motion,
        video_infos=infos,
    )
    return adjusted


class SmartVisualPacingImageBoundsTests(unittest.TestCase):
    def test_static_image_never_grows_past_four_seconds(self):
        plan = _plan((60, 40), ("video", "image"))

        adjusted = _apply(plan, motion={"asset_0": 2.0})

        self.assertLessEqual(adjusted.scenes[1].frame_count, 4 * FPS)
        self.assertGreaterEqual(adjusted.scenes[1].frame_count, 2 * FPS)
        self.assertEqual(adjusted.total_frames, plan.total_frames)

    def test_static_image_never_shrinks_below_two_seconds(self):
        plan = _plan((20, 20, 20), ("video", "image", "video"))

        adjusted = _apply(plan, motion={"asset_2": 90.0})

        self.assertGreaterEqual(adjusted.scenes[1].frame_count, 2 * FPS)
        self.assertLessEqual(adjusted.scenes[1].frame_count, 4 * FPS)
        self.assertEqual(adjusted.total_frames, plan.total_frames)


if __name__ == "__main__":
    unittest.main()
