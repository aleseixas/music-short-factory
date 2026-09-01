import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageStat

from engine.captions import write_ass_captions
from engine.config import load_project_config, load_style_config
from engine.ffmpeg import (
    ffmpeg_output,
    probe_duration,
    probe_video_frame_count,
    run_ffmpeg,
)
from engine.models import (
    AssetSpec,
    HighlightSpec,
    OverlayCue,
    ResolvedVisualFxCue,
    ScriptSegment,
    ShotSpec,
    Story,
    TextFxCue,
    TimelinePlan,
    TimelineScene,
    VisualFxCue,
    WordTiming,
)
from engine.renderer import Renderer
from engine.text_fx import write_text_fx_ass
from engine.timeline import build_timeline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def asset(asset_id: str, file_name: str) -> AssetSpec:
    return AssetSpec(asset_id, file_name, None, "", "", 0.5, 0.5)


class VideoAssetRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project_config = load_project_config(PROJECT_ROOT / "config" / "config.json")
        cls.config = replace(
            project_config,
            render=replace(
                project_config.render,
                width=90,
                height=160,
                fps=12,
                working_scale=1,
                intermediate_preset="ultrafast",
                preset="ultrafast",
            ),
        )
        cls.style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        cls.fixture_directory = tempfile.TemporaryDirectory()
        fixture_root = Path(cls.fixture_directory.name)

        cls.temporal_video = fixture_root / "temporal.mp4"
        run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=180x100:r=12:d=1",
                "-f",
                "lavfi",
                "-i",
                "color=c=lime:s=180x100:r=12:d=1",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=180x100:r=12:d=1",
                "-filter_complex",
                "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                "-map",
                "[v]",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                cls.temporal_video,
            ]
        )

        cls.landscape_audio_video = fixture_root / "landscape-audio.mp4"
        run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-f",
                "lavfi",
                "-i",
                "color=c=lime:s=180x100:r=7:d=2,"
                "drawbox=x=0:y=0:w=60:h=100:color=red:t=fill,"
                "drawbox=x=120:y=0:w=60:h=100:color=blue:t=fill,"
                "drawgrid=w=18:h=10:t=1:color=white@0.35",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=997:sample_rate=48000:duration=2",
                "-shortest",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                cls.landscape_audio_video,
            ]
        )

        cls.dark_video = fixture_root / "dark.mp4"
        run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x101010:s=180x100:r=12:d=2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                cls.dark_video,
            ]
        )

    @classmethod
    def tearDownClass(cls):
        cls.fixture_directory.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.work.mkdir()
        self.renderer = Renderer(
            self.root,
            self.work,
            self.output,
            self.config,
            self.style,
        )

    def tearDown(self):
        self.directory.cleanup()

    def single_plan(
        self,
        source: Path,
        *,
        duration: float = 1.0,
        source_start: float = 0.0,
        source_end: float | None = None,
        highlight: HighlightSpec | None = None,
        visual_fx: tuple[VisualFxCue, ...] = (),
    ) -> TimelinePlan:
        video = asset("clip", source.name)
        shot = ShotSpec(
            "shot",
            "hook",
            "clip",
            "hold",
            "cut",
            highlight=highlight,
            source_start_seconds=source_start,
            source_end_seconds=source_end,
        )
        return build_timeline(
            Story("Test", "test", (ScriptSegment("hook", "one"),)),
            (shot,),
            {"clip": video},
            (WordTiming("one", 0, duration),),
            duration,
            self.config.render.fps,
            0,
            visual_fx,
        )

    def extract_frame(self, video: Path, timestamp: float, name: str) -> Path:
        frame = self.root / name
        run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-ss",
                timestamp,
                "-i",
                video,
                "-frames:v",
                1,
                frame,
            ]
        )
        return frame

    def test_video_start_end_trim_uses_requested_source_window_without_loop(self):
        plan = self.single_plan(
            self.temporal_video,
            source_start=1.0,
            source_end=2.1,
        )
        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(
                plan.scenes[0], self.temporal_video, None
            )

        arguments = ffmpeg_call.call_args.args[0]
        video_filter = arguments[arguments.index("-vf") + 1]
        self.assertIn("trim=start=1.000000:duration=1.000000", video_filter)
        self.assertNotIn("-loop", arguments)
        self.assertNotIn("-stream_loop", arguments)
        self.assertIn("-an", arguments)
        self.assertEqual(arguments[arguments.index("-r") + 1], 12)
        self.assertEqual(probe_video_frame_count(output), 12)
        self.assertAlmostEqual(probe_duration(output), 1.0, delta=0.06)

        frame = self.extract_frame(output, 0.45, "trimmed.png")
        with Image.open(frame) as opened:
            self.assertEqual(opened.size, (90, 160))
            red, green, blue = ImageStat.Stat(opened.convert("RGB")).mean
        self.assertGreater(green, red + 60)
        self.assertGreater(green, blue + 60)

    def test_landscape_video_is_center_cropped_fps_normalized_and_audio_is_discarded(self):
        plan = self.single_plan(self.landscape_audio_video)
        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(
                plan.scenes[0], self.landscape_audio_video, None
            )

        arguments = ffmpeg_call.call_args.args[0]
        video_filter = arguments[arguments.index("-vf") + 1]
        self.assertIn("fps=12", video_filter)
        self.assertIn("force_original_aspect_ratio=increase", video_filter)
        self.assertIn("crop=90:160:(iw-ow)/2:(ih-oh)/2", video_filter)
        self.assertEqual(probe_video_frame_count(output), 12)
        self.assertNotIn("Audio:", ffmpeg_output(["-hide_banner", "-i", output]))

        frame = self.extract_frame(output, 0.5, "center-crop.png")
        with Image.open(frame) as opened:
            self.assertEqual(opened.size, (90, 160))
            red, green, blue = ImageStat.Stat(opened.convert("RGB")).mean
        self.assertGreater(green, red + 45)
        self.assertGreater(green, blue + 45)

    def test_visual_fx_is_rendered_on_video_after_normalization(self):
        plan = self.single_plan(
            self.landscape_audio_video,
            visual_fx=(VisualFxCue(0.1, 0.9, "slow_zoom_in", 0.6),),
        )
        self.assertEqual(plan.scenes[0].visual_fx_cues[0].type, "slow_zoom_in")
        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(
                plan.scenes[0], self.landscape_audio_video, None
            )

        arguments = ffmpeg_call.call_args.args[0]
        video_filter = arguments[arguments.index("-vf") + 1]
        self.assertIn("perspective=", video_filter)
        self.assertIn("force_original_aspect_ratio=increase", video_filter)
        self.assertEqual(probe_video_frame_count(output), 12)

    def test_text_highlight_subtitles_and_image_overlay_compose_over_video(self):
        plan = self.single_plan(
            self.dark_video,
            highlight=HighlightSpec("DESTAQUE", 0.1, 0.7),
        )
        highlight = self.work / "highlight.png"
        highlight_image = Image.new("RGBA", (90, 160), (0, 0, 0, 0))
        ImageDraw.Draw(highlight_image).rectangle(
            (8, 126, 82, 148), fill=(255, 220, 0, 230)
        )
        highlight_image.save(highlight)

        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as render_call:
            scene_clip = self.renderer.render_scene(
                plan.scenes[0], self.dark_video, highlight
            )
        render_arguments = render_call.call_args.args[0]
        render_graph = render_arguments[render_arguments.index("-filter_complex") + 1]
        self.assertIn("[base][title]overlay=", render_graph)

        captions = self.work / "captions.ass"
        write_ass_captions(
            (WordTiming("legenda", 0, 1),),
            captions,
            90,
            160,
            self.style.captions,
        )
        text_fx = self.work / "text_fx.ass"
        write_text_fx_ass(
            (TextFxCue(0.15, 0.85, "VIDEO", "pop_in"),),
            text_fx,
            90,
            160,
            self.style.captions,
            self.style.highlights,
            1.0,
        )
        graphic = self.work / "graphic.png"
        Image.new("RGBA", (22, 16), (255, 0, 0, 255)).save(graphic)
        overlays = (
            (OverlayCue(0.2, 0.8, "graphic", "fade_in", "upper_center"), graphic),
        )

        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as compose_call:
            output = self.renderer.compose_timeline(
                [scene_clip], plan, captions, text_fx, overlays
            )
        compose_arguments = compose_call.call_args.args[0]
        compose_graph = compose_arguments[
            compose_arguments.index("-filter_complex") + 1
        ]
        self.assertIn("graphic0", compose_graph)
        self.assertIn("text_fx.ass", compose_graph)
        self.assertIn("captions.ass", compose_graph)
        self.assertEqual(probe_video_frame_count(output), 12)

        frame = self.extract_frame(output, 0.5, "editorial-layers.png")
        with Image.open(frame) as opened:
            rgb = opened.convert("RGB")
            pixels = rgb.load()
            overlay_visible = any(
                pixels[x, y][0] > 150
                and pixels[x, y][1] < 90
                and pixels[x, y][2] < 90
                for y in range(rgb.height)
                for x in range(rgb.width)
            )
        self.assertTrue(
            overlay_visible,
            "O image overlay vermelho nao apareceu sobre o shot de video.",
        )

    def test_cut_and_crossfade_cover_image_video_and_video_video(self):
        image = asset("image", "image.jpg")
        video = asset("video", self.landscape_audio_video.name)
        sequence = (image, video, image, video, image, video, video, video)
        transitions = (
            "cut",
            "crossfade",
            "crossfade",
            "cut",
            "cut",
            "cut",
            "crossfade",
            "cut",
        )
        # Two frames matches the project's real low-resolution crossfade
        # (round(0.14 * 12)); a one-frame xfade is an FFmpeg edge case rather
        # than a duration used by the production configuration.
        transition_frames = (0, 2, 2, 0, 0, 0, 2, 0)
        scenes = []
        for index, (current_asset, transition, handle) in enumerate(
            zip(sequence, transitions, transition_frames), start=1
        ):
            start_frame = (index - 1) * 6
            shot = ShotSpec(
                f"shot_{index}",
                f"segment_{index}",
                current_asset.id,
                "hold",
                transition,
            )
            scenes.append(
                TimelineScene(
                    index=index,
                    shot=shot,
                    asset=current_asset,
                    start_frame=start_frame,
                    end_frame=start_frame + 6,
                    render_frames=6 + handle,
                    transition_frames=handle,
                )
            )
        plan = TimelinePlan(
            fps=12,
            total_frames=48,
            audio_duration=4.0,
            scenes=tuple(scenes),
        )

        prepared_image = self.work / "image.jpg"
        Image.new("RGB", (90, 160), (225, 180, 30)).save(prepared_image)
        clips = [
            self.renderer.render_scene(
                scene,
                self.landscape_audio_video if scene.asset.is_video else prepared_image,
                None,
            )
            for scene in plan.scenes
        ]
        captions = self.work / "captions.ass"
        write_ass_captions(
            (WordTiming("one", 0, 4),),
            captions,
            90,
            160,
            self.style.captions,
        )

        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as compose_call:
            output = self.renderer.compose_timeline(clips, plan, captions)
        graph = compose_call.call_args.args[0][
            compose_call.call_args.args[0].index("-filter_complex") + 1
        ]
        self.assertEqual(graph.count("xfade=transition=fade"), 3)
        self.assertEqual(graph.count("concat=n=2:v=1:a=0"), 4)
        self.assertEqual(probe_video_frame_count(output), 48)
        self.assertAlmostEqual(probe_duration(output), 4.0, delta=0.06)


if __name__ == "__main__":
    unittest.main()
