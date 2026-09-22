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
    FreezeFrameSpec,
    HighlightSpec,
    OverlayCue,
    ResolvedTextFxCue,
    ResolvedVisualFxCue,
    ScriptSegment,
    ShotSpec,
    Story,
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
                intermediate_crf=0,
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

        cls.moving_video = fixture_root / "moving.mp4"
        run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=180x100:rate=12:duration=3",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                0,
                "-pix_fmt",
                "yuv420p",
                cls.moving_video,
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

        cls.portrait_video = fixture_root / "portrait.mp4"
        run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=100x180:rate=12:duration=2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                0,
                "-pix_fmt",
                "yuv420p",
                cls.portrait_video,
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
        speed: float = 1.0,
        freeze_frame: FreezeFrameSpec | None = None,
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
            speed=speed,
            freeze_frame=freeze_frame,
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

    def frame_checksums(self, video: Path) -> list[str]:
        output = ffmpeg_output(
            [
                "-hide_banner",
                "-i",
                video,
                "-map",
                "0:v:0",
                "-f",
                "framemd5",
                "-",
            ]
        )
        checksums: list[str] = []
        for line in output.splitlines():
            fields = line.split(",")
            if len(fields) == 6 and fields[0].strip().isdigit():
                checksums.append(fields[-1].strip())
        return checksums

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
        self.assertIsNone(plan.scenes[0].freeze_frame)
        self.assertEqual(plan.scenes[0].source_frame_count, 12)
        self.assertAlmostEqual(plan.scenes[0].required_source_duration(12), 1.0)
        self.assertIn("trim=start=1.000000:duration=1.000000", video_filter)
        self.assertIn("setpts=PTS-STARTPTS", video_filter)
        self.assertNotIn("setpts=(PTS-STARTPTS)/", video_filter)
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

    def test_freeze_repeats_exact_frames_and_keeps_shot_duration(self):
        plan = self.single_plan(
            self.moving_video,
            freeze_frame=FreezeFrameSpec(
                start_seconds=0.25,
                duration_seconds=0.5,
            ),
        )
        scene = plan.scenes[0]
        self.assertEqual(scene.freeze_frame.start_frame, 3)
        self.assertEqual(scene.freeze_frame.duration_frames, 6)
        self.assertEqual(scene.freeze_frame.added_frames, 5)
        self.assertEqual(scene.source_frame_count, 7)
        self.assertAlmostEqual(scene.required_source_duration(12), 7 / 12)

        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(scene, self.moving_video, None)

        arguments = ffmpeg_call.call_args.args[0]
        self.assertNotIn("-loop", arguments)
        self.assertNotIn("-stream_loop", arguments)
        self.assertIn("-an", arguments)
        self.assertEqual(probe_video_frame_count(output), 12)
        self.assertAlmostEqual(probe_duration(output), 1.0, delta=0.06)

        checksums = self.frame_checksums(output)
        self.assertEqual(len(checksums), 12)
        self.assertEqual(len(set(checksums[3:9])), 1)
        self.assertNotEqual(checksums[2], checksums[3])
        self.assertNotEqual(checksums[8], checksums[9])

    def test_terminal_freeze_uses_no_loop_and_keeps_exact_frame_count(self):
        plan = self.single_plan(
            self.moving_video,
            freeze_frame=FreezeFrameSpec(
                start_seconds=0.75,
                duration_seconds=0.25,
            ),
        )
        scene = plan.scenes[0]
        self.assertEqual(scene.freeze_frame.start_frame, 9)
        self.assertEqual(scene.freeze_frame.duration_frames, 3)
        self.assertEqual(scene.source_frame_count, 10)

        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(scene, self.moving_video, None)

        arguments = ffmpeg_call.call_args.args[0]
        self.assertNotIn("-loop", arguments)
        self.assertNotIn("-stream_loop", arguments)
        self.assertEqual(probe_video_frame_count(output), 12)
        self.assertAlmostEqual(probe_duration(output), 1.0, delta=0.06)
        checksums = self.frame_checksums(output)
        self.assertEqual(len(set(checksums[9:12])), 1)
        self.assertNotEqual(checksums[8], checksums[9])

    def test_freeze_and_speed_share_frame_based_source_duration(self):
        plan = self.single_plan(
            self.moving_video,
            duration=1.5,
            speed=1.5,
            freeze_frame=FreezeFrameSpec(
                start_seconds=0.25,
                duration_seconds=0.5,
            ),
        )
        scene = plan.scenes[0]
        self.assertEqual(scene.render_frames, 18)
        self.assertEqual(scene.source_frame_count, 13)
        self.assertAlmostEqual(scene.required_source_duration(12), 1.625)

        output = self.renderer.render_scene(scene, self.moving_video, None)

        self.assertEqual(probe_video_frame_count(output), 18)
        self.assertAlmostEqual(probe_duration(output), 1.5, delta=0.06)
        checksums = self.frame_checksums(output)
        self.assertEqual(len(set(checksums[3:9])), 1)
        self.assertNotEqual(checksums[8], checksums[9])

    def test_speed_accelerates_only_video_and_keeps_shot_duration(self):
        plan = self.single_plan(self.temporal_video, speed=2.0)
        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(
                plan.scenes[0], self.temporal_video, None
            )

        arguments = ffmpeg_call.call_args.args[0]
        video_filter = arguments[arguments.index("-vf") + 1]
        self.assertIn("trim=start=0.000000:duration=2.000000", video_filter)
        self.assertIn("setpts=(PTS-STARTPTS)/2.000000", video_filter)
        self.assertIn("-an", arguments)
        self.assertNotIn("-loop", arguments)
        self.assertNotIn("-stream_loop", arguments)
        self.assertEqual(probe_video_frame_count(output), 12)
        self.assertAlmostEqual(probe_duration(output), 1.0, delta=0.06)

        frame = self.extract_frame(output, 0.75, "accelerated.png")
        with Image.open(frame) as opened:
            red, green, blue = ImageStat.Stat(opened.convert("RGB")).mean
        self.assertGreater(green, red + 60)
        self.assertGreater(green, blue + 60)

    def test_speed_slows_video_and_keeps_shot_duration(self):
        plan = self.single_plan(self.temporal_video, speed=0.5)
        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(
                plan.scenes[0], self.temporal_video, None
            )

        arguments = ffmpeg_call.call_args.args[0]
        video_filter = arguments[arguments.index("-vf") + 1]
        self.assertIn("trim=start=0.000000:duration=0.500000", video_filter)
        self.assertIn("setpts=(PTS-STARTPTS)/0.500000", video_filter)
        self.assertEqual(probe_video_frame_count(output), 12)
        self.assertAlmostEqual(probe_duration(output), 1.0, delta=0.06)

        frame = self.extract_frame(output, 0.75, "slowed.png")
        with Image.open(frame) as opened:
            red, green, blue = ImageStat.Stat(opened.convert("RGB")).mean
        self.assertGreater(red, green + 60)
        self.assertGreater(red, blue + 60)

    def test_landscape_video_uses_neutral_contain_when_cover_crop_is_too_aggressive(self):
        plan = self.single_plan(self.landscape_audio_video)
        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(
                plan.scenes[0], self.landscape_audio_video, None
            )

        arguments = ffmpeg_call.call_args.args[0]
        video_filter = arguments[arguments.index("-vf") + 1]
        self.assertIn("fps=12", video_filter)
        self.assertIn("force_original_aspect_ratio=decrease", video_filter)
        self.assertIn("pad=90:160:(ow-iw)/2:(oh-ih)/2:color=0x0b0f14", video_filter)
        self.assertNotIn("crop=90:160", video_filter)
        self.assertEqual(probe_video_frame_count(output), 12)
        self.assertNotIn("Audio:", ffmpeg_output(["-hide_banner", "-i", output]))

        frame = self.extract_frame(output, 0.5, "neutral-contain.png")
        with Image.open(frame) as opened:
            self.assertEqual(opened.size, (90, 160))
            rgb = opened.convert("RGB")
            red, green, blue = ImageStat.Stat(rgb.crop((35, 65, 55, 95))).mean
            background = rgb.getpixel((5, 5))
        self.assertGreater(green, red + 45)
        self.assertGreater(green, blue + 45)
        self.assertLess(sum(background), 70)

    def test_near_vertical_video_keeps_existing_cover_crop_path(self):
        plan = self.single_plan(self.portrait_video)
        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as ffmpeg_call:
            output = self.renderer.render_scene(
                plan.scenes[0], self.portrait_video, None
            )

        arguments = ffmpeg_call.call_args.args[0]
        video_filter = arguments[arguments.index("-vf") + 1]
        self.assertIn("force_original_aspect_ratio=increase", video_filter)
        self.assertIn("crop=90:160:(iw-ow)/2:(ih-oh)/2", video_filter)
        self.assertNotIn("pad=90:160", video_filter)
        self.assertEqual(probe_video_frame_count(output), 12)

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
        self.assertIn("force_original_aspect_ratio=decrease", video_filter)
        self.assertIn("pad=90:160:(ow-iw)/2:(oh-ih)/2:color=0x0b0f14", video_filter)
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
            (ResolvedTextFxCue(0.15, 0.85, "VIDEO", "pop_in"),),
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

    def test_speed_preserves_video_to_video_crossfade_timing(self):
        first_asset = asset("first", self.landscape_audio_video.name)
        second_asset = asset("second", self.landscape_audio_video.name)
        first = TimelineScene(
            index=1,
            shot=ShotSpec(
                "shot_1",
                "segment_1",
                "first",
                "hold",
                "crossfade",
                speed=1.5,
            ),
            asset=first_asset,
            start_frame=0,
            end_frame=12,
            render_frames=14,
            transition_frames=2,
        )
        second = TimelineScene(
            index=2,
            shot=ShotSpec(
                "shot_2",
                "segment_2",
                "second",
                "hold",
                "cut",
                speed=0.75,
            ),
            asset=second_asset,
            start_frame=12,
            end_frame=24,
            render_frames=12,
            transition_frames=0,
        )
        plan = TimelinePlan(
            fps=12,
            total_frames=24,
            audio_duration=2.0,
            scenes=(first, second),
        )

        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as render_calls:
            clips = [
                self.renderer.render_scene(
                    scene, self.landscape_audio_video, None
                )
                for scene in plan.scenes
            ]
        first_arguments = render_calls.call_args_list[0].args[0]
        first_filter = first_arguments[first_arguments.index("-vf") + 1]
        second_arguments = render_calls.call_args_list[1].args[0]
        second_filter = second_arguments[second_arguments.index("-vf") + 1]
        self.assertIn("trim=start=0.000000:duration=1.750000", first_filter)
        self.assertIn("setpts=(PTS-STARTPTS)/1.500000", first_filter)
        self.assertIn("trim=start=0.000000:duration=0.750000", second_filter)
        self.assertIn("setpts=(PTS-STARTPTS)/0.750000", second_filter)

        captions = self.work / "speed-crossfade.ass"
        write_ass_captions(
            (WordTiming("one", 0, 2),),
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
        self.assertIn("xfade=transition=fade:duration=0.166667", graph)
        self.assertEqual(probe_video_frame_count(output), 24)
        self.assertAlmostEqual(probe_duration(output), 2.0, delta=0.06)

    def test_freeze_with_speed_trim_and_crossfade_keeps_timeline_duration(self):
        first_asset = asset("first", self.moving_video.name)
        second_asset = asset("second", self.moving_video.name)
        first_shot = ShotSpec(
            "shot_1",
            "segment_1",
            "first",
            "hold",
            "crossfade",
            source_start_seconds=0.5,
            source_end_seconds=1.625,
            speed=1.5,
            freeze_frame=FreezeFrameSpec(0.25, 0.5),
        )
        second_shot = ShotSpec(
            "shot_2",
            "segment_2",
            "second",
            "hold",
            "cut",
        )
        plan = build_timeline(
            Story(
                "Test",
                "test",
                (
                    ScriptSegment("segment_1", "one"),
                    ScriptSegment("segment_2", "two"),
                ),
            ),
            (first_shot, second_shot),
            {"first": first_asset, "second": second_asset},
            (
                WordTiming("one", 0.0, 0.9),
                WordTiming("two", 1.1, 2.0),
            ),
            2.0,
            12,
            2 / 12,
        )
        first = plan.scenes[0]
        self.assertEqual(first.frame_count, 12)
        self.assertEqual(first.render_frames, 14)
        self.assertEqual(first.transition_frames, 2)
        self.assertEqual(first.freeze_frame.start_frame, 3)
        self.assertEqual(first.freeze_frame.duration_frames, 6)
        self.assertEqual(first.source_frame_count, 9)
        self.assertAlmostEqual(first.required_source_duration(12), 1.125)
        self.assertAlmostEqual(
            first.shot.source_end_seconds - first.shot.source_start_seconds,
            first.required_source_duration(12),
        )

        with patch("engine.renderer.run_ffmpeg", wraps=run_ffmpeg) as render_calls:
            clips = [
                self.renderer.render_scene(scene, self.moving_video, None)
                for scene in plan.scenes
            ]
        first_arguments = render_calls.call_args_list[0].args[0]
        self.assertNotIn("-loop", first_arguments)
        self.assertNotIn("-stream_loop", first_arguments)
        self.assertEqual(probe_video_frame_count(clips[0]), 14)
        first_checksums = self.frame_checksums(clips[0])
        self.assertEqual(len(set(first_checksums[3:9])), 1)
        self.assertNotEqual(first_checksums[8], first_checksums[9])

        captions = self.work / "freeze-speed-crossfade.ass"
        write_ass_captions(
            (WordTiming("one", 0, 1), WordTiming("two", 1, 2)),
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
        self.assertIn("xfade=transition=fade:duration=0.166667", graph)
        self.assertEqual(probe_video_frame_count(output), 24)
        self.assertAlmostEqual(probe_duration(output), 2.0, delta=0.06)


if __name__ == "__main__":
    unittest.main()
