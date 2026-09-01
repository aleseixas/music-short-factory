from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat

from engine.config import load_project_config, load_style_config
from engine.ffmpeg import probe_video_frame_count, run_ffmpeg
from engine.models import (
    AudioResult,
    AssetSpec,
    ResolvedBackgroundMusic,
    ResolvedSfxCue,
    ResolvedVisualFxCue,
    ScriptSegment,
    ShotSpec,
    Story,
    TimelineScene,
    VISUAL_FX_TYPES,
    VisualFxCue,
    WordTiming,
)
from engine.motion import build_motion_filter
from engine.renderer import Renderer
from engine.timeline import build_timeline, load_timeline
from tests.loudnorm_fixture import LOUDNORM_ANALYSIS_OUTPUT


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _story_and_assets(two_shots: bool = False):
    segments = (ScriptSegment("one", "one"),)
    if two_shots:
        segments += (ScriptSegment("two", "two"),)
    story = Story("Test", "test", segments=segments)
    asset = AssetSpec("photo", "photo.png", None, "", "", 0.5, 0.5)
    shots = tuple(
        ShotSpec(f"shot_{segment.id}", segment.id, "photo", "push_in", "cut")
        for segment in segments
    )
    return story, {asset.id: asset}, shots


def _resolved(effect_type: str, intensity: float = 0.5, frames: int = 12):
    return ResolvedVisualFxCue(1, 0, frames, 0, frames, effect_type, intensity)


class VisualFxSchemaTests(unittest.TestCase):
    def _load(self, cues):
        story, assets, _ = _story_and_assets()
        data = {
            "schema_version": 1,
            "visual_fx_cues": cues,
            "shots": [
                {
                    "id": "shot_one",
                    "segment": "one",
                    "asset": "photo",
                    "motion": "hold",
                    "transition_out": "cut",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "timeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_timeline(path, story, assets)

    def test_default_intensity_and_all_supported_types_load(self):
        for effect_type in VISUAL_FX_TYPES:
            with self.subTest(effect_type=effect_type):
                timeline = self._load(
                    [{"start_seconds": 0, "end_seconds": 1, "type": effect_type}]
                )
                self.assertEqual(timeline.visual_fx_cues[0].intensity, 0.5)

    def test_invalid_type_time_and_intensity_fail(self):
        cases = (
            ({"start_seconds": 0, "end_seconds": 1, "type": "shake"}, "desconhecido"),
            ({"start_seconds": -0.1, "end_seconds": 1, "type": "pan_left"}, "maior ou igual"),
            ({"start_seconds": 1, "end_seconds": 1, "type": "pan_left"}, "maior que"),
            ({"start_seconds": 0, "end_seconds": 1, "type": "pan_left", "intensity": -0.01}, "entre 0 e 1"),
            ({"start_seconds": 0, "end_seconds": 1, "type": "pan_left", "intensity": 1.01}, "entre 0 e 1"),
            ({"start_seconds": 0, "end_seconds": 1, "type": "pan_left", "intensity": float("nan")}, "finito"),
        )
        for cue, message in cases:
            with self.subTest(cue=cue):
                with self.assertRaisesRegex(RuntimeError, message):
                    self._load([cue])

    def test_every_global_overlap_is_rejected_but_touching_is_allowed(self):
        first = {"start_seconds": 0, "end_seconds": 1, "type": "slow_zoom_in"}
        overlap = {"start_seconds": 0.5, "end_seconds": 2, "type": "pan_right"}
        with self.assertRaisesRegex(RuntimeError, "sobrepostas"):
            self._load([first, overlap])
        touching = {"start_seconds": 1, "end_seconds": 2, "type": "pan_right"}
        self.assertEqual(len(self._load([first, touching]).visual_fx_cues), 2)


class VisualFxTimelineTests(unittest.TestCase):
    def _two_shot_plan(self, cues):
        story, assets, shots = _story_and_assets(two_shots=True)
        words = (WordTiming("one", 0, 3.9), WordTiming("two", 4.1, 8.0))
        return build_timeline(story, shots, assets, words, 8.0, 10, 0, cues)

    def test_cue_is_split_and_clamped_at_shot_boundaries(self):
        plan = self._two_shot_plan((VisualFxCue(2, 7, "slow_zoom_in", 0.6),))
        left, right = (scene.visual_fx_cues[0] for scene in plan.scenes)
        self.assertEqual((left.start_frame, left.end_frame), (20, 70))
        self.assertEqual((left.local_start_frame, left.local_end_frame), (20, 40))
        self.assertEqual((right.start_frame, right.end_frame), (20, 70))
        self.assertEqual((right.local_start_frame, right.local_end_frame), (0, 30))

    def test_partial_cue_clamps_and_late_cue_warns_and_is_ignored(self):
        plan = self._two_shot_plan((VisualFxCue(6, 10, "pan_down"),))
        self.assertEqual(plan.scenes[0].visual_fx_cues, ())
        cue = plan.scenes[1].visual_fx_cues[0]
        self.assertEqual((cue.start_frame, cue.end_frame), (60, 80))
        self.assertEqual((cue.local_start_frame, cue.local_end_frame), (20, 40))
        with patch("builtins.print") as logged:
            late = self._two_shot_plan((VisualFxCue(8, 9, "pan_up"),))
        self.assertTrue(all(not scene.visual_fx_cues for scene in late.scenes))
        self.assertIn("ignorada", str(logged.call_args_list))

    def test_two_sequential_cues_in_one_shot_fail_predictably(self):
        story, assets, shots = _story_and_assets()
        with self.assertRaisesRegex(RuntimeError, "maximo uma cue visual por plano"):
            build_timeline(
                story,
                shots,
                assets,
                (WordTiming("one", 0, 2),),
                2,
                10,
                0,
                (VisualFxCue(0, 0.5, "pan_left"), VisualFxCue(1, 1.5, "pan_right")),
            )

    def test_fractional_start_is_quantized_without_starting_early(self):
        story, assets, shots = _story_and_assets()
        plan = build_timeline(
            story, shots, assets, (WordTiming("one", 0, 1),), 1, 10, 0,
            (VisualFxCue(0.01, 0.5, "slow_zoom_in"),),
        )
        cue = plan.scenes[0].visual_fx_cues[0]
        self.assertEqual(cue.start_frame, 1)
        self.assertEqual(cue.local_start_frame, 1)
        self.assertGreaterEqual(cue.start_frame / plan.fps, 0.01)

    def test_crossfade_handles_freeze_outgoing_pose_and_share_global_progress(self):
        story, assets, shots = _story_and_assets(two_shots=True)
        shots = (replace(shots[0], transition_out="crossfade"), shots[1])
        plan = build_timeline(
            story, shots, assets,
            (WordTiming("one", 0, 3.9), WordTiming("two", 4.1, 8)),
            8, 10, 0.2, (VisualFxCue(2, 7, "slow_zoom_in"),),
        )
        left, right = plan.scenes
        self.assertEqual(left.transition_frames, 2)
        self.assertEqual(left.render_frames, 42)
        style = load_style_config(PROJECT_ROOT / "config" / "style.json").motion
        graphs = [
            build_motion_filter(
                scene.shot.motion, scene.render_frames, 10, 90, 160, 1, 0.5, 0.5,
                style, scene.visual_fx_cues, scene.start_frame,
            )
            for scene in plan.scenes
        ]
        self.assertIn("min(max((0+on-1),20),40)", graphs[0])
        self.assertIn("min(max((40+on-1),40),70)", graphs[1])
        self.assertIn("-20)/50.000000", graphs[0])
        self.assertIn("-20)/50.000000", graphs[1])


class VisualFxFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.style = load_style_config(PROJECT_ROOT / "config" / "style.json").motion

    def _filter(self, effect_type, intensity=0.5):
        return build_motion_filter(
            "push_in", 12, 12, 90, 160, 1, 0.5, 0.5, self.style,
            (_resolved(effect_type, intensity),), 0,
        )

    def test_empty_cues_preserve_the_exact_legacy_filter(self):
        omitted = build_motion_filter("push_in", 12, 12, 90, 160, 1, 0.5, 0.5, self.style)
        explicit = build_motion_filter("push_in", 12, 12, 90, 160, 1, 0.5, 0.5, self.style, ())
        self.assertEqual(omitted, explicit)

    def test_all_seven_effects_build_one_safe_perspective_filter(self):
        for effect_type in VISUAL_FX_TYPES:
            with self.subTest(effect_type=effect_type):
                graph = self._filter(effect_type)
                self.assertEqual(graph.count("perspective="), 1)
                self.assertIn("sense=source:eval=frame:interpolation=cubic", graph)
                self.assertIn("scale=90:160", graph)

    def test_intensity_endpoints_map_to_conservative_ranges(self):
        self.assertIn("0.030000", self._filter("slow_zoom_in", 0))
        self.assertIn("0.120000", self._filter("slow_zoom_in", 1))
        self.assertIn("1.040000", self._filter("pan_left", 0))
        self.assertIn("1.080000", self._filter("pan_left", 1))
        self.assertIn("0.080000", self._filter("punch_zoom", 0))
        self.assertIn("0.150000", self._filter("punch_zoom", 1))

    def test_pose_is_clamped_before_and_after_the_semantic_slice(self):
        cue = ResolvedVisualFxCue(1, 10, 30, 10, 20, "slow_zoom_in", 0.5)
        graph = build_motion_filter(
            "pan_left", 25, 10, 90, 160, 1, 0.5, 0.5, self.style, (cue,), 0
        )
        self.assertIn("min(max((0+on-1),10),20)", graph)

    def test_pan_expressions_have_unambiguous_real_directions(self):
        right = self._filter("pan_right", 1)
        left = self._filter("pan_left", 1)
        down = self._filter("pan_down", 1)
        up = self._filter("pan_up", 1)
        eased = "(0.5-0.5*cos(PI*"
        self.assertIn(f"0.200000+0.600000*{eased}", right)
        self.assertIn(f"0.800000-0.600000*{eased}", left)
        self.assertIn(f"0.200000+0.600000*{eased}", down)
        self.assertIn(f"0.800000-0.600000*{eased}", up)
        # Horizontal movement appears in x0; vertical movement appears in y0.
        self.assertLess(right.index("0.200000+"), right.index(":y0="))
        self.assertGreater(down.index("0.200000+"), down.index(":y0="))


class VisualFxRenderTests(unittest.TestCase):
    def test_all_effects_keep_frames_resolution_and_safe_borders(self):
        base = load_project_config(PROJECT_ROOT / "config" / "config.json")
        config = replace(base, render=replace(base.render, width=90, height=160, fps=12, working_scale=1, intermediate_preset="ultrafast"))
        style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        asset = AssetSpec("photo", "photo.png", None, "", "", 0.5, 0.5)
        shot = ShotSpec("shot", "one", "photo", "pull_out", "cut")
        for effect_type in VISUAL_FX_TYPES:
            with self.subTest(effect_type=effect_type), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source = root / "source.png"
                synthetic = Image.new("RGB", (90, 160))
                pixels = synthetic.load()
                for y in range(160):
                    for x in range(90):
                        pixels[x, y] = (
                            64 + round(126 * x / 89),
                            64 + round(126 * y / 159),
                            64 + 96 * ((x // 12 + y // 16) % 2),
                        )
                synthetic.save(source)
                scene = TimelineScene(
                    1, shot, asset, 0, 12, 12, 0, (_resolved(effect_type, 1),)
                )
                clip = Renderer(root, root / "work", root / "output", config, style).render_scene(scene, source, None)
                self.assertEqual(probe_video_frame_count(clip), 12)
                frames = []
                for number in (0, 6, 11):
                    image_path = root / f"frame-{number}.png"
                    run_ffmpeg(["-y", "-hide_banner", "-i", clip, "-vf", f"select=eq(n\\,{number})", "-frames:v", 1, image_path])
                    with Image.open(image_path) as opened:
                        frame = opened.convert("RGB")
                        self.assertEqual(frame.size, (90, 160))
                        for channel_minimum, _ in frame.getextrema():
                            self.assertGreater(channel_minimum, 5)
                        frames.append(frame.copy())
                self.assertIsNotNone(ImageChops.difference(frames[0], frames[-1]).getbbox())

    def test_punch_peak_is_stronger_than_the_settled_final_pose(self):
        base = load_project_config(PROJECT_ROOT / "config" / "config.json")
        config = replace(
            base,
            render=replace(
                base.render, width=90, height=160, fps=24, working_scale=1,
                intermediate_preset="ultrafast",
            ),
        )
        style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        asset = AssetSpec("photo", "photo.png", None, "", "", 0.5, 0.5)
        shot = ShotSpec("shot", "one", "photo", "hold", "cut")
        scene = TimelineScene(
            1, shot, asset, 0, 24, 24, 0, (_resolved("punch_zoom", 1, 24),)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "radial.png"
            image = Image.new("RGB", (90, 160), (70, 70, 70))
            pixels = image.load()
            for y in range(50, 110):
                for x in range(25, 65):
                    pixels[x, y] = (230, 230, 230)
            image.save(source)
            clip = Renderer(root, root / "work", root / "output", config, style).render_scene(
                scene, source, None
            )
            means = []
            for number in (11, 23):
                frame_path = root / f"punch-{number}.png"
                run_ffmpeg([
                    "-y", "-hide_banner", "-i", clip, "-vf",
                    f"select=eq(n\\,{number})", "-frames:v", 1, frame_path,
                ])
                with Image.open(frame_path) as opened:
                    means.append(ImageStat.Stat(opened.convert("L")).mean[0])
            self.assertGreater(means[0], means[1] + 2)

    def test_visual_fx_coexists_with_music_sfx_ducking_and_limiter(self):
        config = load_project_config(PROJECT_ROOT / "config" / "config.json")
        style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        asset = AssetSpec("photo", "photo.png", None, "", "", 0.5, 0.5)
        shot = ShotSpec("shot", "one", "photo", "push_in", "cut")
        scene = TimelineScene(
            1, shot, asset, 0, 60, 60, 0,
            (_resolved("punch_zoom", 0.65, 60),),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            source.write_bytes(b"image")
            renderer = Renderer(root, root / "work", root / "output", config, style)
            with patch("engine.renderer.run_ffmpeg") as render_call:
                visual_video = renderer.render_scene(scene, source, None)
            render_arguments = render_call.call_args.args[0]
            visual_graph = render_arguments[render_arguments.index("-vf") + 1]
            self.assertIn("perspective=", visual_graph)
            self.assertIn("0.125500", visual_graph)

            visual_video.write_bytes(b"video")
            voice = root / "voice.wav"
            music = root / "music.wav"
            effect = root / "effect.wav"
            for path in (voice, music, effect):
                path.write_bytes(b"audio")

            def fake_ffmpeg(arguments, cwd=None):
                Path(arguments[-1]).write_bytes(b"muxed")

            with (
                patch("engine.renderer.run_ffmpeg", side_effect=fake_ffmpeg) as mux_call,
                patch(
                    "engine.renderer.run_ffmpeg_capture",
                    return_value=LOUDNORM_ANALYSIS_OUTPUT,
                ),
                patch("engine.renderer.probe_video_frame_count", return_value=60),
                patch("engine.renderer.probe_duration", return_value=2.0),
            ):
                renderer.mux_audio(
                    visual_video,
                    AudioResult(voice, 2.0, (), "test", True),
                    "combined.mp4",
                    background_music=ResolvedBackgroundMusic("ambient", music, 0.12),
                    sfx_cues=(ResolvedSfxCue(1, 0.4, "impact", effect, 0.35),),
                )
            mux_arguments = mux_call.call_args.args[0]
            graph = mux_arguments[mux_arguments.index("-filter_complex") + 1]
            sidechain = "[music][duck_control]sidechaincompress="
            final_mix = "[voice][ducked_music][sfx0]amix=inputs=3"
            self.assertIn(sidechain, graph)
            self.assertIn(final_mix, graph)
            self.assertLess(graph.index(sidechain), graph.index(final_mix))
            self.assertLess(graph.index(final_mix), graph.index("alimiter="))
            self.assertIn("-c:v", mux_arguments)
            self.assertEqual(mux_arguments[mux_arguments.index("-c:v") + 1], "copy")


if __name__ == "__main__":
    unittest.main()
