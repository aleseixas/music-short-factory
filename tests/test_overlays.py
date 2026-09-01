import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import AsyncMock

from PIL import Image, features

from engine.assets import AssetManager
from engine.captions import write_ass_captions
from engine.config import load_project_config, load_style_config
from engine.ffmpeg import probe_duration, probe_video_frame_count, run_ffmpeg
from engine.models import (
    OVERLAY_ANIMATIONS,
    OVERLAY_POSITIONS,
    AssetSpec,
    AudioResult,
    OverlayCue,
    ResolvedBackgroundMusic,
    ResolvedSfxCue,
    ResolvedTextFxCue,
    ScriptSegment,
    ShotSpec,
    Story,
    VisualFxCue,
    WordTiming,
)
from engine.renderer import Renderer, _overlay_position_expressions, _overlay_scale_expression
from engine.pipeline import build_video
from engine.text_fx import write_text_fx_ass
from engine.timeline import build_timeline, load_timeline
from tests.loudnorm_fixture import LOUDNORM_ANALYSIS_OUTPUT


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def asset(asset_id="photo", file_name="photo.png"):
    return AssetSpec(asset_id, file_name, None, "", "", 0.5, 0.5)


class OverlaySchemaTests(unittest.TestCase):
    def setUp(self):
        self.story = Story("Test", "test", (ScriptSegment("hook", "one"),))
        self.assets = {"photo": asset(), "logo": asset("logo", "logo.png")}
        self.shot = {"id": "shot", "segment": "hook", "asset": "photo", "motion": "hold", "transition_out": "cut"}

    def load(self, cues=None):
        data = {"schema_version": 1, "shots": [self.shot]}
        if cues is not None:
            data["overlay_cues"] = cues
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_timeline(path, self.story, self.assets)

    def test_empty_preserves_optional_default(self):
        self.assertEqual(self.load().overlay_cues, ())
        self.assertEqual(self.load([]).overlay_cues, ())

    def test_valid_asset_all_animations_positions_and_defaults(self):
        cues = []
        for index, (animation, position) in enumerate(zip(sorted(OVERLAY_ANIMATIONS), sorted(OVERLAY_POSITIONS) + ["center"])):
            cues.append({"start_seconds": index, "end_seconds": index + 0.8, "asset": "logo", "animation": animation, "position": position})
        parsed = self.load(cues).overlay_cues
        self.assertEqual({cue.animation for cue in parsed}, OVERLAY_ANIMATIONS)
        self.assertTrue(all(cue.scale == 0.38 and cue.opacity == 1.0 for cue in parsed))

    def test_unknown_asset_fails_during_timeline_load(self):
        with self.assertRaisesRegex(RuntimeError, "Asset de overlay desconhecido"):
            self.load([{"start_seconds": 0, "end_seconds": 1, "asset": "missing", "animation": "pop_in", "position": "center"}])

    def test_scale_and_opacity_boundaries_and_invalid_values(self):
        parsed = self.load([
            {"start_seconds": 0, "end_seconds": 1, "asset": "logo", "animation": "fade_in", "position": "center", "scale": 0.1, "opacity": 0},
            {"start_seconds": 1, "end_seconds": 2, "asset": "logo", "animation": "fade_in", "position": "center", "scale": 0.8, "opacity": 1},
        ]).overlay_cues
        self.assertEqual([(cue.scale, cue.opacity) for cue in parsed], [(0.1, 0), (0.8, 1)])
        for field, value, message in (("scale", 0.09, "0.10 e 0.80"), ("scale", 0.81, "0.10 e 0.80"), ("opacity", -0.1, "entre 0 e 1"), ("opacity", 1.1, "entre 0 e 1")):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(RuntimeError, message):
                cue = {"start_seconds": 0, "end_seconds": 1, "asset": "logo", "animation": "pop_in", "position": "center", field: value}
                self.load([cue])

    def test_simultaneous_overlays_are_rejected_but_touching_is_allowed(self):
        base = {"asset": "logo", "animation": "pop_in", "position": "center"}
        self.assertEqual(len(self.load([{**base, "start_seconds": 0, "end_seconds": 1}, {**base, "start_seconds": 1, "end_seconds": 2}]).overlay_cues), 2)
        with self.assertRaisesRegex(RuntimeError, "simultaneos"):
            self.load([{**base, "start_seconds": 0, "end_seconds": 2}, {**base, "start_seconds": 1, "end_seconds": 3}])


class OverlayAssetTests(unittest.TestCase):
    def test_png_transparency_jpg_and_webp_are_prepared_as_contained_png(self):
        formats = [("logo.png", "PNG", "RGBA"), ("photo.jpg", "JPEG", "RGB")]
        if features.check("webp"):
            formats.append(("card.webp", "WEBP", "RGBA"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets_dir = root / "assets"; assets_dir.mkdir()
            manager = AssetManager(assets_dir, root / "work", 720, 1280, 2)
            for index, (file_name, image_format, mode) in enumerate(formats):
                with self.subTest(file_name=file_name):
                    source = assets_dir / file_name
                    color = (255, 0, 0, 80) if mode == "RGBA" else (255, 0, 0)
                    Image.new(mode, (400, 200), color).save(source, format=image_format)
                    prepared = manager.prepare_overlay(asset(f"asset{index}", file_name), 0.38)
                    with Image.open(prepared) as opened:
                        self.assertEqual(opened.mode, "RGBA")
                        self.assertEqual(opened.size, (274, 137))
                        self.assertEqual(opened.width / opened.height, 2)
                        if mode == "RGBA":
                            self.assertLess(opened.getpixel((0, 0))[3], 255)

    def test_missing_and_unsupported_overlay_fail_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = AssetManager(root / "assets", root / "work", 720, 1280, 1)
            with patch.object(manager, "_download") as download:
                with self.assertRaisesRegex(RuntimeError, "Asset de overlay ausente"):
                    manager.prepare_overlay(asset("logo", "logo.png"), 0.38)
                download.assert_not_called()
            bad = root / "assets" / "logo.gif"
            Image.new("RGB", (10, 10)).save(bad)
            with self.assertRaisesRegex(RuntimeError, "Formato de overlay nao suportado"):
                manager.prepare_overlay(asset("logo", "logo.gif"), 0.38)


class OverlayPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_local_overlay_fails_before_tts_or_heavy_render(self):
        config = load_project_config(PROJECT_ROOT / "config" / "config.json")
        style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            episode_dir = root / "episodes" / "demo"
            episode_dir.mkdir(parents=True)
            episode = SimpleNamespace(
                name="demo",
                directory=episode_dir,
                story=SimpleNamespace(title="Demo"),
                assets_dir=episode_dir / "assets",
                assets={"logo": asset("logo", "missing.png")},
                overlay_cues=(OverlayCue(0, 1, "logo", "pop_in", "center"),),
                background_music=None,
                sfx_cues=(),
            )
            with (
                patch("engine.pipeline.load_project_config", return_value=config),
                patch("engine.pipeline.load_style_config", return_value=style),
                patch("engine.pipeline.load_episode", return_value=episode),
                patch("engine.pipeline.resolve_background_music", return_value=None),
                patch("engine.pipeline.resolve_sfx_cues", return_value=()),
                patch("engine.pipeline.preflight"),
                patch("engine.pipeline.resolve_audio", new_callable=AsyncMock) as resolve_audio,
            ):
                with self.assertRaisesRegex(RuntimeError, "Asset de overlay ausente"):
                    await build_video(root, "demo")
                resolve_audio.assert_not_awaited()


class OverlayRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_project_config(PROJECT_ROOT / "config" / "config.json")
        cls.style = load_style_config(PROJECT_ROOT / "config" / "style.json")

    def make_plan(self, duration=1.0, fps=12):
        background = asset()
        shot = ShotSpec("shot", "hook", "photo", "hold", "cut")
        story = Story("T", "t", (ScriptSegment("hook", "one"),))
        plan = build_timeline(story, (shot,), {"photo": background}, (WordTiming("one", 0, duration),), duration, fps, 0)
        return plan

    def test_all_animation_and_position_expressions_are_present_and_safe(self):
        for animation in OVERLAY_ANIMATIONS:
            cue = OverlayCue(0.1, 1, "logo", animation, "center")
            expression = _overlay_scale_expression(cue)
            with self.subTest(animation=animation):
                self.assertTrue(expression)
                self.assertNotIn("0.380000", expression)  # scale is applied only once during contain.
        for position in OVERLAY_POSITIONS:
            x, y = _overlay_position_expressions(OverlayCue(0.1, 1, "logo", "slide_up", position), 720, 1280, 0.18)
            with self.subTest(position=position):
                self.assertTrue(x and y)
        upper = _overlay_position_expressions(OverlayCue(0, 1, "logo", "fade_in", "upper_center"), 720, 1280, 0.18)[1]
        lower = _overlay_position_expressions(OverlayCue(0, 1, "logo", "fade_in", "lower_center"), 720, 1280, 0.18)[1]
        self.assertIn("307", upper)
        self.assertIn("-282", lower)

    def test_empty_overlay_keeps_legacy_graph_and_layer_order_is_correct(self):
        config = replace(self.config, render=replace(self.config.render, width=90, height=160, fps=12))
        plan = self.make_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); work = root / "work"; work.mkdir()
            clip = root / "clip.mp4"; clip.write_bytes(b"clip")
            captions = work / "captions.ass"; captions.write_text("", encoding="utf-8")
            text_fx = work / "text_fx.ass"; text_fx.write_text("", encoding="utf-8")
            graphic = work / "graphic.png"; graphic.write_bytes(b"png")
            highlight = work / "highlight.png"; highlight.write_bytes(b"png")
            renderer = Renderer(root, work, root / "output", config, self.style)
            with patch("engine.renderer.run_ffmpeg") as ffmpeg, patch("engine.renderer.probe_video_frame_count", return_value=12):
                renderer.compose_timeline([clip], plan, captions)
                args = ffmpeg.call_args.args[0]; legacy = args[args.index("-filter_complex") + 1]
                renderer.compose_timeline([clip], plan, captions, text_fx, ((OverlayCue(0.1, 0.9, "logo", "pop_in", "center"), graphic),), ((highlight, 0.1, 0.8, 0.1),))
                args = ffmpeg.call_args.args[0]; graph = args[args.index("-filter_complex") + 1]
        self.assertNotIn("graphic0", legacy)
        self.assertLess(graph.index("graphic0"), graph.index("text_fx.ass"))
        self.assertLess(graph.index("text_fx.ass"), graph.index("highlight0"))
        self.assertLess(graph.index("highlight0"), graph.index("captions.ass"))
        self.assertIn("colorchannelmixer=aa=1.000000", graph)

    def test_real_overlay_render_preserves_frames_resolution_duration_alpha_and_safe_area(self):
        config = replace(self.config, render=replace(self.config.render, width=90, height=160, fps=12, preset="ultrafast"))
        plan = self.make_plan(duration=3.0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); work = root / "work"; work.mkdir()
            clip = root / "clip.mp4"
            run_ffmpeg(["-y", "-hide_banner", "-f", "lavfi", "-i", "color=c=blue:s=90x160:r=12", "-frames:v", 36, "-pix_fmt", "yuv420p", clip])
            captions = work / "captions.ass"
            write_ass_captions((WordTiming("one", 0, 3),), captions, 90, 160, self.style.captions)
            source = root / "source.png"
            Image.new("RGBA", (30, 20), (255, 0, 0, 140)).save(source)
            positions = sorted(OVERLAY_POSITIONS) + ["center"]
            overlays = tuple(
                (
                    OverlayCue(index * 0.45 + 0.05, index * 0.45 + 0.40, "logo", animation, positions[index], 0.38, 0.8),
                    source,
                )
                for index, animation in enumerate(sorted(OVERLAY_ANIMATIONS))
            )
            output = Renderer(root, work, root / "output", config, self.style).compose_timeline(
                [clip], plan, captions, None, overlays,
            )
            self.assertEqual(probe_video_frame_count(output), 36)
            self.assertAlmostEqual(probe_duration(output), 3.0, delta=0.05)
            safe_top, safe_bottom, margin_x = round(160 * 0.24), round(160 * 0.22), max(4, round(90 * 0.075))
            for index in range(len(overlays)):
                frame = root / f"frame-{index}.png"
                run_ffmpeg(["-y", "-hide_banner", "-ss", index * 0.45 + 0.23, "-i", output, "-frames:v", 1, frame])
                with Image.open(frame) as opened:
                    self.assertEqual(opened.size, (90, 160))
                    red = opened.convert("RGB").point(lambda value: value)
                    pixels = red.load()
                    points = [(x, y) for y in range(160) for x in range(90) if pixels[x, y][0] > 50 and pixels[x, y][1] < 60]
                    self.assertTrue(points, f"overlay {index} nao detectado")
                    xs, ys = zip(*points)
                    self.assertGreaterEqual(min(xs), margin_x - 2)
                    self.assertLessEqual(max(xs), 90 - margin_x + 2)
                    self.assertGreaterEqual(min(ys), safe_top - 2)
                    self.assertLessEqual(max(ys), 160 - safe_bottom + 2)

    def test_overlay_graph_coexists_with_text_visual_and_audio_features_without_mutating_them(self):
        config = replace(self.config, render=replace(self.config.render, width=90, height=160, fps=12))
        plan = self.make_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); work = root / "work"; work.mkdir()
            clip = root / "clip.mp4"; clip.write_bytes(b"clip")
            captions = work / "captions.ass"; captions.write_text("", encoding="utf-8")
            text_fx = work / "text_fx.ass"
            write_text_fx_ass((ResolvedTextFxCue(0.1, 0.9, "IMPACTO", "pop_in"),), text_fx, 90, 160, self.style.captions, self.style.highlights, 1)
            graphic = work / "graphic.png"; graphic.write_bytes(b"png")
            renderer = Renderer(root, work, root / "output", config, self.style)
            with patch("engine.renderer.run_ffmpeg") as ffmpeg, patch("engine.renderer.probe_video_frame_count", return_value=12):
                renderer.compose_timeline([clip], plan, captions, text_fx, ((OverlayCue(0.1, 0.9, "logo", "scale_bounce", "center"), graphic),))
            args = ffmpeg.call_args.args[0]; graph = args[args.index("-filter_complex") + 1]
            self.assertIn("graphic0", graph)
            self.assertIn("text_fx.ass", graph)
            self.assertEqual(plan.total_frames, 12)

    def test_overlay_punch_zoom_text_music_and_sfx_share_the_existing_pipeline(self):
        config = replace(self.config, render=replace(self.config.render, width=90, height=160, fps=12))
        background = asset()
        shot = ShotSpec("shot", "hook", "photo", "hold", "cut")
        story = Story("T", "t", (ScriptSegment("hook", "one"),))
        plan = build_timeline(
            story, (shot,), {"photo": background}, (WordTiming("one", 0, 1),),
            1, 12, 0, (VisualFxCue(0.1, 0.9, "punch_zoom", 0.6),),
        )
        self.assertEqual(plan.scenes[0].visual_fx_cues[0].type, "punch_zoom")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); work = root / "work"; work.mkdir()
            renderer = Renderer(root, work, root / "output", config, self.style)
            source = root / "source.png"; source.write_bytes(b"image")
            with patch("engine.renderer.run_ffmpeg") as render_call:
                scene_video = renderer.render_scene(plan.scenes[0], source, None)
            render_args = render_call.call_args.args[0]
            self.assertIn("perspective=", render_args[render_args.index("-vf") + 1])

            clip = root / "clip.mp4"; clip.write_bytes(b"video")
            captions = work / "captions.ass"; captions.write_text("", encoding="utf-8")
            text_fx = work / "text_fx.ass"
            write_text_fx_ass((ResolvedTextFxCue(0.1, 0.9, "41 SEMANAS", "pop_in"),), text_fx, 90, 160, self.style.captions, self.style.highlights, 1)
            graphic = work / "graphic.png"; graphic.write_bytes(b"png")
            with patch("engine.renderer.run_ffmpeg") as compose_call, patch("engine.renderer.probe_video_frame_count", return_value=12):
                renderer.compose_timeline([clip], plan, captions, text_fx, ((OverlayCue(0.1, 0.9, "logo", "pop_in", "center"), graphic),))
            compose_args = compose_call.call_args.args[0]
            compose_graph = compose_args[compose_args.index("-filter_complex") + 1]
            self.assertIn("graphic0", compose_graph)
            self.assertIn("text_fx.ass", compose_graph)

            scene_video.write_bytes(b"video")
            voice = root / "voice.wav"; music = root / "music.wav"; effect = root / "effect.wav"
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
                patch("engine.renderer.probe_video_frame_count", return_value=12),
                patch("engine.renderer.probe_duration", return_value=1.0),
            ):
                renderer.mux_audio(
                    scene_video,
                    AudioResult(voice, 1.0, (), "test", True),
                    "combined.mp4",
                    background_music=ResolvedBackgroundMusic("music", music, 0.12),
                    sfx_cues=(ResolvedSfxCue(1, 0.2, "impact", effect, 0.3),),
                )
            mux_args = mux_call.call_args.args[0]
            audio_graph = mux_args[mux_args.index("-filter_complex") + 1]
            self.assertIn("sidechaincompress=", audio_graph)
            self.assertIn("[sfx0]amix=inputs=3", audio_graph)


if __name__ == "__main__":
    unittest.main()
