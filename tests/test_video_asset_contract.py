import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from engine.assets import AssetManager
from engine.ffmpeg import VideoStreamInfo, preflight, probe_video_stream
from engine.models import (
    AssetSpec,
    FreezeFrameSpec,
    ResolvedFreezeFrame,
    ScriptSegment,
    ShotSpec,
    Story,
    TimelineScene,
    WordTiming,
)
from engine.timeline import build_timeline, load_timeline, resolve_freeze_frame


def asset(asset_id: str, file_name: str) -> AssetSpec:
    return AssetSpec(asset_id, file_name, None, "", "", 0.5, 0.5)


class VideoAssetClassificationTests(unittest.TestCase):
    def test_mp4_mov_and_webm_are_video_case_insensitively(self):
        for file_name in (
            "clip.mp4",
            "clip.MP4",
            "clip.mov",
            "clip.MoV",
            "clip.webm",
            "clip.WEBM",
        ):
            with self.subTest(file_name=file_name):
                current = asset("clip", file_name)
                self.assertTrue(current.is_video)
                self.assertEqual(current.media_type, "video")

    def test_existing_image_extensions_remain_images(self):
        for file_name in ("photo.jpg", "photo.JPEG", "frame.png", "card.webp"):
            with self.subTest(file_name=file_name):
                current = asset("photo", file_name)
                self.assertFalse(current.is_video)
                self.assertEqual(current.media_type, "image")


class VideoShotSchemaTests(unittest.TestCase):
    def setUp(self):
        self.story = Story("Test", "test", (ScriptSegment("hook", "one"),))
        self.video = asset("clip", "clip.mp4")
        self.image = asset("photo", "photo.jpg")

    def load(self, shot: dict, selected_asset: AssetSpec | None = None):
        selected_asset = selected_asset or self.video
        data = {"schema_version": 1, "shots": [shot]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_timeline(path, self.story, {selected_asset.id: selected_asset})

    @staticmethod
    def shot(asset_id: str = "clip", **extra) -> dict:
        return {
            "id": "shot_hook",
            "segment": "hook",
            "asset": asset_id,
            "motion": "hold",
            "transition_out": "cut",
            **extra,
        }

    def test_source_window_defaults_and_explicit_values(self):
        default = self.load(self.shot()).shots[0]
        self.assertEqual(default.source_start_seconds, 0.0)
        self.assertIsNone(default.source_end_seconds)
        self.assertEqual(default.speed, 1.0)
        self.assertIsNone(default.freeze_frame)

        explicit = self.load(
            self.shot(
                source_start_seconds=1.25,
                source_end_seconds=4.75,
                speed=1.2,
                freeze_frame={
                    "start_seconds": 0.8,
                    "duration_seconds": 0.6,
                },
            )
        ).shots[0]
        self.assertEqual(explicit.source_start_seconds, 1.25)
        self.assertEqual(explicit.source_end_seconds, 4.75)
        self.assertEqual(explicit.speed, 1.2)
        self.assertEqual(explicit.freeze_frame, FreezeFrameSpec(0.8, 0.6))

    def test_freeze_frame_accepts_safe_boundaries_and_rejects_invalid_values(self):
        for duration in (0.10, 0.6, 2.0):
            with self.subTest(duration=duration):
                freeze = self.load(
                    self.shot(
                        freeze_frame={
                            "start_seconds": 0,
                            "duration_seconds": duration,
                        }
                    )
                ).shots[0].freeze_frame
                self.assertEqual(freeze, FreezeFrameSpec(0.0, duration))

        cases = (
            ([], "objeto ou null"),
            ({}, "precisa definir"),
            ({"start_seconds": 0.2}, "precisa definir"),
            ({"duration_seconds": 0.5}, "precisa definir"),
            (
                {"start_seconds": -0.01, "duration_seconds": 0.5},
                "maior ou igual a zero",
            ),
            (
                {"start_seconds": True, "duration_seconds": 0.5},
                "numero finito",
            ),
            (
                {"start_seconds": float("nan"), "duration_seconds": 0.5},
                "numero finito",
            ),
            (
                {"start_seconds": 0.2, "duration_seconds": 0.09},
                "entre 0.10 e 2.00",
            ),
            (
                {"start_seconds": 0.2, "duration_seconds": 2.01},
                "entre 0.10 e 2.00",
            ),
            (
                {"start_seconds": 0.2, "duration_seconds": True},
                "numero finito",
            ),
            (
                {"start_seconds": 0.2, "duration_seconds": float("inf")},
                "numero finito",
            ),
        )
        for value, message in cases:
            with self.subTest(value=value), self.assertRaisesRegex(
                RuntimeError, message
            ):
                self.load(self.shot(freeze_frame=value))

    def test_freeze_frame_is_rejected_for_image(self):
        with self.assertRaisesRegex(RuntimeError, "freeze_frame.*nao e video"):
            self.load(
                self.shot(
                    "photo",
                    freeze_frame={
                        "start_seconds": 0.2,
                        "duration_seconds": 0.5,
                    },
                ),
                self.image,
            )

    def test_freeze_frame_is_resolved_with_ceil_and_must_fit_logical_shot(self):
        resolved = resolve_freeze_frame(
            FreezeFrameSpec(start_seconds=0.333, duration_seconds=0.101),
            shot_frames=30,
            fps=30,
        )
        self.assertEqual(resolved, ResolvedFreezeFrame(10, 4))
        self.assertEqual(resolved.added_frames, 3)

        shot = self.load(
            self.shot(
                speed=1.5,
                freeze_frame={
                    "start_seconds": 0.2,
                    "duration_seconds": 0.4,
                },
            )
        ).shots[0]
        plan = build_timeline(
            self.story,
            (shot,),
            {"clip": self.video},
            (WordTiming("one", 0.0, 1.0),),
            1.0,
            30,
            0.2,
        )
        scene = plan.scenes[0]
        self.assertEqual(scene.frame_count, 30)
        self.assertEqual(scene.render_frames, 30)
        self.assertEqual(scene.freeze_frame, ResolvedFreezeFrame(6, 12))
        self.assertEqual(scene.source_frame_count, 19)
        self.assertAlmostEqual(scene.required_source_duration(30), 0.95)

        outside = self.load(
            self.shot(
                freeze_frame={
                    "start_seconds": 0.8,
                    "duration_seconds": 0.3,
                }
            )
        ).shots[0]
        with self.assertRaisesRegex(RuntimeError, "nao pode invadir.*crossfade"):
            build_timeline(
                self.story,
                (outside,),
                {"clip": self.video},
                (WordTiming("one", 0.0, 1.0),),
                1.0,
                30,
                0.2,
            )

    def test_speed_accepts_safe_boundaries_and_rejects_invalid_values(self):
        for value in (0.5, 1.0, 2.0):
            with self.subTest(value=value):
                self.assertEqual(
                    self.load(self.shot(speed=value)).shots[0].speed,
                    value,
                )

        cases = (
            (0.49, "entre 0.5 e 2.0"),
            (2.01, "entre 0.5 e 2.0"),
            (0, "entre 0.5 e 2.0"),
            (True, "numero finito"),
            (float("nan"), "numero finito"),
            (float("inf"), "numero finito"),
            ("fast", "numero finito"),
        )
        for value, message in cases:
            with self.subTest(value=value), self.assertRaisesRegex(
                RuntimeError, message
            ):
                self.load(self.shot(speed=value))

    def test_invalid_source_windows_are_rejected(self):
        cases = (
            ({"source_start_seconds": -0.01}, "maior ou igual a zero"),
            ({"source_start_seconds": "not-a-number"}, "numero finito"),
            ({"source_start_seconds": float("nan")}, "finito"),
            (
                {"source_start_seconds": 2, "source_end_seconds": 2},
                "precisa ser maior",
            ),
            (
                {"source_start_seconds": 2, "source_end_seconds": 1.9},
                "precisa ser maior",
            ),
        )
        for values, message in cases:
            with self.subTest(values=values), self.assertRaisesRegex(RuntimeError, message):
                self.load(self.shot(**values))

    def test_source_window_is_rejected_for_image_but_legacy_image_defaults_work(self):
        legacy = self.load(self.shot("photo"), self.image).shots[0]
        self.assertEqual(legacy.source_start_seconds, 0.0)
        self.assertIsNone(legacy.source_end_seconds)
        self.assertEqual(legacy.speed, 1.0)
        self.assertIsNone(legacy.freeze_frame)

        explicit_default = self.load(
            self.shot("photo", speed=1.0), self.image
        ).shots[0]
        self.assertEqual(explicit_default.speed, 1.0)

        for values in (
            {"source_start_seconds": 0.1},
            {"source_end_seconds": 1.0},
            {"speed": 0.8},
        ):
            with self.subTest(values=values), self.assertRaisesRegex(
                RuntimeError, "nao e video"
            ):
                self.load(self.shot("photo", **values), self.image)

    def test_video_asset_is_rejected_as_an_overlay(self):
        data = {
            "schema_version": 1,
            "overlay_cues": [
                {
                    "start_seconds": 0,
                    "end_seconds": 1,
                    "asset": "clip",
                    "animation": "fade_in",
                    "position": "center",
                }
            ],
            "shots": [self.shot()],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "overlays de video nao sao suportados"):
                load_timeline(path, self.story, {"clip": self.video})


class VideoProbeParserTests(unittest.TestCase):
    def probe(self, payload: object):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(b"synthetic")
            with patch("engine.ffmpeg.ffprobe_output", return_value=json.dumps(payload)) as call:
                result = probe_video_stream(path)
            arguments = call.call_args.args[0]
            self.assertIn("v:0", arguments)
            self.assertIn("json", arguments)
            self.assertEqual(Path(arguments[-1]), path.resolve())
            return result

    def test_valid_json_reads_dimensions_fractional_fps_and_format_duration(self):
        info = self.probe(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "avg_frame_rate": "30000/1001",
                        "r_frame_rate": "30/1",
                    }
                ],
                "format": {"duration": "12.345"},
            }
        )
        self.assertEqual((info.width, info.height), (1920, 1080))
        self.assertAlmostEqual(info.fps, 30000 / 1001)
        self.assertEqual(info.duration, 12.345)

    def test_missing_video_stream_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio-only.mp4"
            path.write_bytes(b"synthetic")
            with patch(
                "engine.ffmpeg.ffprobe_output",
                return_value=json.dumps({"streams": [], "format": {"duration": "2"}}),
            ):
                with self.assertRaisesRegex(RuntimeError, "sem stream de video"):
                    probe_video_stream(path)

    def test_invalid_dimensions_are_rejected(self):
        for width, height in ((0, 1080), (1920, 0), ("bad", 1080)):
            payload = {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": width,
                        "height": height,
                        "avg_frame_rate": "30/1",
                        "duration": "2",
                    }
                ]
            }
            with self.subTest(width=width, height=height), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "clip.mp4"
                path.write_bytes(b"synthetic")
                with patch("engine.ffmpeg.ffprobe_output", return_value=json.dumps(payload)):
                    with self.assertRaisesRegex(RuntimeError, "Resolucao de video invalida"):
                        probe_video_stream(path)

    def test_invalid_fps_is_rejected(self):
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "0/0",
                    "r_frame_rate": "not-a-rate",
                    "duration": "2",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(b"synthetic")
            with patch("engine.ffmpeg.ffprobe_output", return_value=json.dumps(payload)):
                with self.assertRaisesRegex(RuntimeError, "FPS de video invalido"):
                    probe_video_stream(path)

    def test_invalid_duration_is_rejected(self):
        for duration in (None, "0", "-1", "not-a-duration"):
            payload = {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 1920,
                        "height": 1080,
                        "avg_frame_rate": "30/1",
                        "duration": duration,
                    }
                ],
                "format": {"duration": duration},
            }
            with self.subTest(duration=duration), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "clip.mp4"
                path.write_bytes(b"synthetic")
                with patch("engine.ffmpeg.ffprobe_output", return_value=json.dumps(payload)):
                    with self.assertRaisesRegex(RuntimeError, "Duracao de video invalida"):
                        probe_video_stream(path)


class VideoWindowPreflightTests(unittest.TestCase):
    def make_manager_and_scene(
        self,
        root: Path,
        *,
        source_start: float,
        source_end: float | None,
        semantic_frames: int,
        transition_frames: int,
        speed: float = 1.0,
        freeze_frame: ResolvedFreezeFrame | None = None,
    ):
        assets_dir = root / "assets"
        assets_dir.mkdir()
        (assets_dir / "clip.mp4").write_bytes(b"synthetic")
        video = asset("clip", "clip.mp4")
        shot = ShotSpec(
            "shot",
            "hook",
            "clip",
            "hold",
            "cut",
            source_start_seconds=source_start,
            source_end_seconds=source_end,
            speed=speed,
        )
        scene = TimelineScene(
            index=1,
            shot=shot,
            asset=video,
            start_frame=0,
            end_frame=semantic_frames,
            render_frames=semantic_frames + transition_frames,
            transition_frames=transition_frames,
            freeze_frame=freeze_frame,
        )
        manager = AssetManager(assets_dir, root / "work", 90, 160, 1)
        return manager, scene

    def test_valid_source_window_uses_mocked_probe_and_is_cached(self):
        info = VideoStreamInfo(duration=10.0, width=1920, height=1080, fps=23.976)
        with tempfile.TemporaryDirectory() as directory:
            manager, scene = self.make_manager_and_scene(
                Path(directory),
                source_start=1.0,
                source_end=2.2,
                semantic_frames=10,
                transition_frames=2,
            )
            with patch("engine.assets.probe_video_stream", return_value=info) as probe:
                self.assertEqual(manager.preflight_video_scene(scene, fps=10), info)
                self.assertEqual(manager.preflight_video_scene(scene, fps=10), info)
            probe.assert_called_once()

    def test_insufficient_window_reports_crossfade_handle_and_no_loop(self):
        info = VideoStreamInfo(duration=10.0, width=1920, height=1080, fps=24.0)
        with tempfile.TemporaryDirectory() as directory:
            manager, scene = self.make_manager_and_scene(
                Path(directory),
                source_start=1.0,
                source_end=2.1,
                semantic_frames=10,
                transition_frames=2,
            )
            with patch("engine.assets.probe_video_stream", return_value=info):
                with self.assertRaisesRegex(
                    RuntimeError,
                    r"Trecho de video insuficiente.*necessario=1\.200s.*"
                    r"0\.200s de handle de crossfade.*Loop nao e permitido",
                ):
                    manager.preflight_video_scene(scene, fps=10)

    def test_speed_changes_source_window_and_crossfade_handle_requirements(self):
        info = VideoStreamInfo(duration=10.0, width=1920, height=1080, fps=24.0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fast_manager, fast_scene = self.make_manager_and_scene(
                root,
                source_start=1.0,
                source_end=3.4,
                semantic_frames=10,
                transition_frames=2,
                speed=2.0,
            )
            with patch("engine.assets.probe_video_stream", return_value=info):
                self.assertEqual(
                    fast_manager.preflight_video_scene(fast_scene, fps=10), info
                )

            fast_scene = replace(
                fast_scene,
                shot=replace(fast_scene.shot, source_end_seconds=3.39),
            )
            with self.assertRaisesRegex(
                RuntimeError,
                r"necessario=2\.400s.*speed=2\.000x.*0\.400s de fonte.*"
                r"0\.200s de handle de crossfade.*Loop nao e permitido",
            ):
                fast_manager.preflight_video_scene(fast_scene, fps=10)

        with tempfile.TemporaryDirectory() as directory:
            slow_manager, slow_scene = self.make_manager_and_scene(
                Path(directory),
                source_start=1.0,
                source_end=1.6,
                semantic_frames=10,
                transition_frames=2,
                speed=0.5,
            )
            with patch("engine.assets.probe_video_stream", return_value=info):
                self.assertEqual(
                    slow_manager.preflight_video_scene(slow_scene, fps=10), info
                )

    def test_freeze_reduces_source_window_with_speed_and_preserves_crossfade_handle(self):
        info = VideoStreamInfo(duration=10.0, width=1920, height=1080, fps=24.0)
        with tempfile.TemporaryDirectory() as directory:
            manager, scene = self.make_manager_and_scene(
                Path(directory),
                source_start=1.0,
                source_end=2.8,
                semantic_frames=10,
                transition_frames=2,
                speed=2.0,
                freeze_frame=ResolvedFreezeFrame(
                    start_frame=3,
                    duration_frames=4,
                ),
            )
            self.assertEqual(scene.source_frame_count, 9)
            self.assertAlmostEqual(scene.required_source_duration(10), 1.8)
            with patch("engine.assets.probe_video_stream", return_value=info):
                self.assertEqual(manager.preflight_video_scene(scene, fps=10), info)

            insufficient = replace(
                scene,
                shot=replace(scene.shot, source_end_seconds=2.79),
            )
            with self.assertRaisesRegex(
                RuntimeError,
                r"necessario=1\.800s.*speed=2\.000x.*"
                r"handle de crossfade.*Loop nao e permitido",
            ):
                manager.preflight_video_scene(insufficient, fps=10)

    def test_window_cannot_exceed_probed_duration(self):
        info = VideoStreamInfo(duration=3.0, width=1280, height=720, fps=30.0)
        with tempfile.TemporaryDirectory() as directory:
            manager, scene = self.make_manager_and_scene(
                Path(directory),
                source_start=1.0,
                source_end=3.1,
                semantic_frames=10,
                transition_frames=0,
            )
            with patch("engine.assets.probe_video_stream", return_value=info):
                with self.assertRaisesRegex(RuntimeError, "ultrapassa a duracao"):
                    manager.preflight_video_scene(scene, fps=10)

    def test_missing_video_without_url_is_never_opened_as_an_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = AssetManager(root / "assets", root / "work", 90, 160, 1)
            missing_video = AssetSpec(
                "clip",
                "clip.mp4",
                None,
                "",
                "",
                0.5,
                0.5,
            )
            with (
                patch.object(manager, "_download") as download,
                patch.object(manager, "_verify_image") as verify_image,
                patch("engine.assets.download_to_cache") as download_to_cache,
            ):
                with self.assertRaisesRegex(RuntimeError, "video ausente e sem URL"):
                    manager.ensure(missing_video)
            download.assert_not_called()
            verify_image.assert_not_called()
            download_to_cache.assert_not_called()


class VideoFFmpegPreflightTests(unittest.TestCase):
    def test_ffprobe_is_required_only_when_video_assets_are_present(self):
        def fake_ffmpeg_output(arguments):
            if "-filters" in arguments:
                return (
                    "perspective xfade subtitles overlay loudnorm "
                    "crop fps scale setpts trim"
                )
            return "libx264"

        with (
            patch("engine.ffmpeg.ffmpeg_output", side_effect=fake_ffmpeg_output),
            patch("engine.ffmpeg.ffprobe_executable") as ffprobe,
        ):
            preflight(require_video_assets=False)
            ffprobe.assert_not_called()
            preflight(require_video_assets=True)
            ffprobe.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
