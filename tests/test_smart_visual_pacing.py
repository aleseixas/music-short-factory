from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from engine.editorial import EditorialCatalogs
from engine.ffmpeg import VideoStreamInfo
from engine.models import (
    AssetSpec,
    AudioResult,
    Episode,
    FreezeFrameSpec,
    HighlightSpec,
    ResolvedFreezeFrame,
    ResolvedVisualFxCue,
    ScriptSegment,
    ShotSpec,
    SmartVisualPacingSpec,
    Story,
    TimelinePlan,
    TimelineScene,
    WordTiming,
)
from engine.pipeline import build_video
from engine.smart_visual_pacing import (
    analyze_timeline_motion_safely,
    apply_smart_visual_pacing,
)
from engine.visual_search import MotionAnalysis


FPS = 10


def _asset(index: int, media_type: str) -> AssetSpec:
    extension = ".mp4" if media_type == "video" else ".jpg"
    return AssetSpec(
        id=f"asset_{index}",
        file=f"asset_{index}{extension}",
        url=None,
        credit="",
        license="",
        focus_x=0.5,
        focus_y=0.5,
    )


def _story(scene_count: int) -> Story:
    return Story(
        title="Smart pacing test",
        slug="smart-pacing-test",
        segments=tuple(
            ScriptSegment(
                id=f"segment_{index}",
                text=(
                    "Voce nao vai acreditar nesta virada!"
                    if index == 0
                    else f"Contexto visual numero {index}."
                ),
            )
            for index in range(scene_count)
        ),
        target_duration_seconds=75.0,
    )


def _plan(
    frame_counts: tuple[int, ...],
    media_types: tuple[str, ...],
    *,
    crossfades: tuple[bool, ...] | None = None,
    speeds: tuple[float, ...] | None = None,
    freeze_at: int | None = None,
) -> TimelinePlan:
    if len(frame_counts) != len(media_types):
        raise AssertionError("test fixture sizes differ")
    crossfades = crossfades or tuple(False for _ in frame_counts)
    speeds = speeds or tuple(1.0 for _ in frame_counts)
    transition_frames = round(0.2 * FPS)
    scenes: list[TimelineScene] = []
    start = 0

    for index, (frames, media_type) in enumerate(zip(frame_counts, media_types)):
        asset = _asset(index, media_type)
        has_crossfade = bool(crossfades[index] and index < len(frame_counts) - 1)
        resolved_freeze = None
        freeze_spec = None
        if freeze_at == index:
            freeze_spec = FreezeFrameSpec(start_seconds=0.5, duration_seconds=0.6)
            resolved_freeze = ResolvedFreezeFrame(start_frame=5, duration_frames=6)
        shot = ShotSpec(
            id=f"shot_{index}",
            segment_id=f"segment_{index}",
            asset_id=asset.id,
            motion="hold",
            transition_out="crossfade" if has_crossfade else "cut",
            source_start_seconds=1.0 if media_type == "video" else 0.0,
            source_end_seconds=None,
            speed=speeds[index],
            freeze_frame=freeze_spec,
        )
        transition = transition_frames if has_crossfade else 0
        scenes.append(
            TimelineScene(
                index=index + 1,
                shot=shot,
                asset=asset,
                start_frame=start,
                end_frame=start + frames,
                render_frames=frames + transition,
                transition_frames=transition,
                freeze_frame=resolved_freeze,
            )
        )
        start += frames

    return TimelinePlan(
        fps=FPS,
        total_frames=start,
        audio_duration=start / FPS,
        scenes=tuple(scenes),
    )


def _apply(
    plan: TimelinePlan,
    *,
    enabled: bool = True,
    motion: dict[str, object] | None = None,
    video_infos: dict[str, VideoStreamInfo] | None = None,
):
    if video_infos is None:
        video_infos = {
            scene.asset.id: VideoStreamInfo(
                duration=120.0,
                width=1920,
                height=1080,
                fps=30.0,
            )
            for scene in plan.scenes
            if scene.asset.is_video
        }
    return apply_smart_visual_pacing(
        plan,
        _story(len(plan.scenes)),
        enabled=enabled,
        crossfade_seconds=0.2,
        video_motion_by_asset=motion,
        video_infos=video_infos,
    )


class SmartVisualPacingTests(unittest.TestCase):
    def test_disabled_preserves_legacy_timeline_exactly(self):
        plan = _plan((60, 60, 20), ("image", "image", "video"))

        adjusted, _report = _apply(
            plan,
            enabled=False,
            motion={"asset_2": 100.0},
        )

        self.assertEqual(adjusted, plan)

    def test_slow_static_sequence_gives_time_to_moving_video(self):
        plan = _plan((70, 65, 20), ("image", "image", "video"))

        adjusted, _report = _apply(plan, motion={"asset_2": 92.0})

        self.assertLess(
            sum(scene.frame_count for scene in adjusted.scenes[:2]),
            sum(scene.frame_count for scene in plan.scenes[:2]),
        )
        self.assertGreater(adjusted.scenes[2].frame_count, plan.scenes[2].frame_count)

    def test_long_run_of_static_images_is_reduced_conservatively(self):
        plan = _plan((50, 50, 50, 20), ("image", "image", "image", "video"))

        adjusted, _report = _apply(plan, motion={"asset_3": 88.0})

        original_static_frames = sum(scene.frame_count for scene in plan.scenes[:3])
        adjusted_static_frames = sum(scene.frame_count for scene in adjusted.scenes[:3])
        self.assertLess(adjusted_static_frames, original_static_frames)
        # A safe pacing pass cannot manufacture extra cuts or reorder visuals.
        self.assertEqual(
            [scene.asset.id for scene in adjusted.scenes],
            [scene.asset.id for scene in plan.scenes],
        )
        self.assertEqual(len(adjusted.scenes), len(plan.scenes))

    def test_reused_asset_is_reported_as_a_repetition_signal(self):
        plan = _plan((60, 60, 20), ("image", "image", "video"))
        repeated_scene = replace(
            plan.scenes[1],
            asset=plan.scenes[0].asset,
            shot=replace(
                plan.scenes[1].shot,
                asset_id=plan.scenes[0].asset.id,
            ),
        )
        plan = replace(plan, scenes=(plan.scenes[0], repeated_scene, plan.scenes[2]))

        _adjusted, report = _apply(plan, motion={"asset_2": 90.0})

        self.assertIn("repeated_asset", report.scenes[0].signals)
        self.assertIn("repeated_asset", report.scenes[1].signals)

    def test_highlight_scene_is_never_shortened(self):
        plan = _plan((60, 20), ("image", "video"))
        highlighted = replace(
            plan.scenes[0],
            shot=replace(
                plan.scenes[0].shot,
                highlight=HighlightSpec(
                    text="MOMENTO IMPORTANTE",
                    start_seconds=5.0,
                    duration_seconds=1.0,
                ),
            ),
        )
        plan = replace(plan, scenes=(highlighted, plan.scenes[1]))

        adjusted, _report = _apply(plan, motion={"asset_1": 99.0})

        self.assertGreaterEqual(
            adjusted.scenes[0].frame_count,
            plan.scenes[0].frame_count,
        )

    def test_visual_fx_stays_on_the_same_scenes_after_boundary_shift(self):
        plan = _plan((60, 30), ("image", "video"))
        cue = ResolvedVisualFxCue(
            index=1,
            start_frame=55,
            end_frame=65,
            local_start_frame=55,
            local_end_frame=60,
            type="punch_zoom",
            intensity=0.5,
        )
        left = replace(plan.scenes[0], visual_fx_cues=(cue,))
        right = replace(
            plan.scenes[1],
            visual_fx_cues=(
                replace(cue, local_start_frame=0, local_end_frame=5),
            ),
        )
        plan = replace(plan, scenes=(left, right))

        adjusted, _report = _apply(plan, motion={"asset_1": 95.0})

        self.assertNotEqual(adjusted.scenes[0].end_frame, 60)
        for scene in adjusted.scenes:
            self.assertEqual(len(scene.visual_fx_cues), 1)
            attached = scene.visual_fx_cues[0]
            self.assertEqual((attached.start_frame, attached.end_frame), (55, 65))
            self.assertGreater(attached.local_end_frame, attached.local_start_frame)

    def test_video_with_real_motion_can_sustain_more_time_than_static_video(self):
        plan = _plan((45, 45), ("image", "video"))
        moving, _ = _apply(
            plan,
            motion={
                "asset_1": SimpleNamespace(
                    motion_score=90.0,
                    is_practically_static=False,
                )
            },
        )
        static, _ = _apply(
            plan,
            motion={
                "asset_1": SimpleNamespace(
                    motion_score=2.0,
                    is_practically_static=True,
                )
            },
        )

        self.assertGreater(
            moving.scenes[1].frame_count,
            static.scenes[1].frame_count,
        )

    def test_total_duration_continuity_and_crossfade_math_are_preserved(self):
        plan = _plan(
            (65, 25, 50),
            ("image", "video", "image"),
            crossfades=(True, True, False),
        )

        adjusted, _report = _apply(plan, motion={"asset_1": 85.0})

        self.assertEqual(adjusted.total_frames, plan.total_frames)
        self.assertEqual(adjusted.audio_duration, plan.audio_duration)
        self.assertEqual(adjusted.scenes[0].start_frame, 0)
        self.assertEqual(adjusted.scenes[-1].end_frame, plan.total_frames)
        for previous, current in zip(adjusted.scenes, adjusted.scenes[1:]):
            self.assertEqual(previous.end_frame, current.start_frame)
        for scene in adjusted.scenes:
            self.assertEqual(
                scene.render_frames,
                scene.frame_count + scene.transition_frames,
            )
        self.assertEqual(
            sum(scene.render_frames for scene in adjusted.scenes)
            - sum(scene.transition_frames for scene in adjusted.scenes),
            plan.total_frames,
        )


class _StopAfterPacing(RuntimeError):
    pass


class SmartVisualPacingPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_episode_passes_adjusted_plan_to_render_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            episode_dir = root / "episodes" / "demo"
            episode_dir.mkdir(parents=True)
            first = _asset(0, "image")
            second = _asset(1, "image")
            story = Story(
                title="Demo",
                slug="demo",
                target_duration_seconds=9.0,
                segments=(
                    ScriptSegment("hook", "Hook!", "hook"),
                    ScriptSegment("context", "Contexto."),
                ),
            )
            episode = Episode(
                name="demo",
                directory=episode_dir,
                story=story,
                assets={first.id: first, second.id: second},
                shots=(
                    ShotSpec("shot_hook", "hook", first.id, "hold", "cut"),
                    ShotSpec("shot_context", "context", second.id, "hold", "cut"),
                ),
                smart_visual_pacing=SmartVisualPacingSpec(enabled=True),
            )
            config = SimpleNamespace(
                paths=SimpleNamespace(
                    episodes_dir="episodes",
                    work_dir="work",
                    output_dir="output",
                    cache_dir="cache",
                ),
                duration=SimpleNamespace(target_tolerance_seconds=2.0),
                render=SimpleNamespace(
                    width=720,
                    height=1280,
                    working_scale=1,
                    fps=FPS,
                ),
                tts=object(),
            )
            style = SimpleNamespace(
                transitions=SimpleNamespace(crossfade_seconds=0.2),
                captions=object(),
                highlights=SimpleNamespace(default_duration=1.5),
            )
            audio = AudioResult(
                path=root / "voice.wav",
                duration=9.0,
                words=(
                    WordTiming("Hook", 0.0, 6.0),
                    WordTiming("Contexto", 6.2, 9.0),
                ),
                source="test",
                exact_timings=True,
            )
            captured: list[TimelinePlan] = []

            def stop_after_pacing(plan, *_args, **_kwargs):
                captured.append(plan)
                raise _StopAfterPacing("pacing reached preflight")

            with (
                patch("engine.pipeline.load_project_config", return_value=config),
                patch("engine.pipeline.load_style_config", return_value=style),
                patch("engine.pipeline.load_episode", return_value=episode),
                patch("engine.pipeline.resolve_background_music", return_value=None),
                patch("engine.pipeline.resolve_sfx_cues", return_value=()),
                patch("engine.pipeline.preflight"),
                patch(
                    "engine.pipeline.resolve_audio",
                    new=AsyncMock(return_value=audio),
                ),
                patch(
                    "engine.pipeline.load_editorial_catalogs",
                    return_value=EditorialCatalogs(),
                ),
                patch("engine.pipeline.AssetManager") as manager_class,
                patch("engine.pipeline.write_smart_visual_pacing_report") as writer,
                patch(
                    "engine.pipeline.select_best_segments_safely",
                    side_effect=stop_after_pacing,
                ),
            ):
                manager_class.return_value.ensure_all.return_value = {
                    first.id: root / first.file,
                    second.id: root / second.file,
                }
                with self.assertRaises(_StopAfterPacing):
                    await build_video(root, "demo")

        self.assertEqual(len(captured), 1)
        adjusted = captured[0]
        self.assertEqual(adjusted.total_frames, 90)
        self.assertLess(adjusted.scenes[0].frame_count, 61)
        writer.assert_called_once()

    def test_motion_analysis_uses_only_the_effective_video_trim(self):
        plan = _plan((20,), ("video",))
        analysis = MotionAnalysis(80.0, 75.0, 0.0, False, 4, ((1.0, 2.0),))
        analyzer = Mock(return_value=analysis)
        source = Path("clip.mp4")
        info = VideoStreamInfo(10.0, 1920, 1080, 30.0)

        results, warnings = analyze_timeline_motion_safely(
            plan,
            {"asset_0": source},
            {"asset_0": info},
            analyzer=analyzer,
        )

        self.assertEqual(results, {"shot_0": analysis})
        self.assertEqual(warnings, ())
        analyzer.assert_called_once_with(
            source,
            info.duration,
            source_start_seconds=1.0,
            source_end_seconds=3.0,
        )

    def test_motion_analysis_failure_is_advisory(self):
        plan = _plan((20,), ("video",))

        results, warnings = analyze_timeline_motion_safely(
            plan,
            {"asset_0": Path("clip.mp4")},
            {"asset_0": VideoStreamInfo(10.0, 1920, 1080, 30.0)},
            analyzer=Mock(side_effect=RuntimeError("private source detail")),
        )

        self.assertEqual(results, {})
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("private source detail", warnings[0])

    def test_speed_and_freeze_never_make_source_trim_insufficient(self):
        plan = _plan(
            (60, 30),
            ("image", "video"),
            speeds=(1.0, 2.0),
            freeze_at=1,
        )
        video_scene = plan.scenes[1]
        available_source = video_scene.required_source_duration(FPS)
        bounded_shot = replace(
            video_scene.shot,
            source_end_seconds=(
                video_scene.shot.source_start_seconds + available_source
            ),
        )
        bounded_scene = replace(video_scene, shot=bounded_shot)
        plan = TimelinePlan(
            fps=plan.fps,
            total_frames=plan.total_frames,
            audio_duration=plan.audio_duration,
            scenes=(plan.scenes[0], bounded_scene),
        )

        adjusted, _report = _apply(
            plan,
            motion={"asset_1": 98.0},
            video_infos={
                "asset_1": VideoStreamInfo(
                    duration=20.0,
                    width=1920,
                    height=1080,
                    fps=30.0,
                )
            },
        )

        adjusted_video = adjusted.scenes[1]
        self.assertLessEqual(
            adjusted_video.required_source_duration(FPS),
            available_source + 1e-9,
        )
        self.assertEqual(adjusted_video.shot.speed, 2.0)
        self.assertIsNotNone(adjusted_video.freeze_frame)
        self.assertEqual(adjusted.total_frames, plan.total_frames)


if __name__ == "__main__":
    unittest.main()
