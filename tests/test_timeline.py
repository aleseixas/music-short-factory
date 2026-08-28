import unittest

from engine.models import (
    AssetSpec,
    ScriptSegment,
    ShotSpec,
    Story,
    WordTiming,
)
from engine.timeline import build_timeline


class TimelineTests(unittest.TestCase):
    def test_crossfade_handles_preserve_total_frame_count(self):
        story = Story(
            title="Test",
            slug="test",
            segments=(
                ScriptSegment("one", "one two"),
                ScriptSegment("two", "three four"),
            ),
        )
        asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        assets = {"photo": asset}
        shots = (
            ShotSpec("shot_one", "one", "photo", "push_in", "crossfade"),
            ShotSpec("shot_two", "two", "photo", "hold", "cut"),
        )
        words = (
            WordTiming("one", 0.0, 0.8),
            WordTiming("two", 0.8, 1.8),
            WordTiming("three", 2.0, 2.8),
            WordTiming("four", 2.8, 4.0),
        )
        plan = build_timeline(story, shots, assets, words, 4.0, 30, 0.1)
        rendered_frames = sum(scene.render_frames for scene in plan.scenes)
        overlap_frames = sum(scene.transition_frames for scene in plan.scenes)
        self.assertEqual(plan.total_frames, 120)
        self.assertEqual(rendered_frames - overlap_frames, plan.total_frames)
        self.assertEqual(plan.scenes[0].transition_frames, 3)
        self.assertEqual(plan.scenes[-1].end_frame, 120)

    def test_crossfade_is_disabled_when_next_scene_is_too_short(self):
        story = Story(
            title="Test",
            slug="test",
            segments=(
                ScriptSegment("long", "one two three four five six seven eight nine ten"),
                ScriptSegment("short", "end"),
            ),
        )
        asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        assets = {"photo": asset}
        shots = (
            ShotSpec("shot_long", "long", "photo", "push_in", "crossfade"),
            ShotSpec("shot_short", "short", "photo", "hold", "cut"),
        )
        words = tuple(
            WordTiming(text, index * 0.2, (index + 1) * 0.2)
            for index, text in enumerate(story.narration.split())
        )
        plan = build_timeline(story, shots, assets, words, 2.2, 10, 0.4)
        self.assertLess(plan.scenes[1].frame_count, 3)
        self.assertEqual(plan.scenes[0].transition_frames, 0)

    def test_fractional_audio_duration_produces_contiguous_logical_frames(self):
        story = Story(
            title="Test",
            slug="test",
            segments=(
                ScriptSegment("one", "one"),
                ScriptSegment("two", "two"),
                ScriptSegment("three", "three"),
            ),
        )
        asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        assets = {"photo": asset}
        shots = (
            ShotSpec("shot_one", "one", "photo", "push_in", "crossfade"),
            ShotSpec("shot_two", "two", "photo", "pan_left", "crossfade"),
            ShotSpec("shot_three", "three", "photo", "hold", "cut"),
        )
        words = (
            WordTiming("one", 0.0, 0.30),
            WordTiming("two", 0.34, 0.66),
            WordTiming("three", 0.70, 1.01),
        )

        plan = build_timeline(story, shots, assets, words, 1.01, 30, 0.1)

        self.assertEqual(plan.total_frames, 31)
        self.assertEqual(plan.scenes[0].start_frame, 0)
        self.assertEqual(plan.scenes[-1].end_frame, plan.total_frames)
        self.assertTrue(all(scene.frame_count >= 1 for scene in plan.scenes))
        self.assertTrue(
            all(
                left.end_frame == right.start_frame
                for left, right in zip(plan.scenes, plan.scenes[1:])
            )
        )
        self.assertEqual(
            sum(scene.render_frames for scene in plan.scenes)
            - sum(scene.transition_frames for scene in plan.scenes),
            plan.total_frames,
        )


if __name__ == "__main__":
    unittest.main()
