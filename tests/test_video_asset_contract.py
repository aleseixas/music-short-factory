import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.assets import AssetManager
from engine.ffmpeg import VideoStreamInfo, preflight, probe_video_stream
from engine.models import AssetSpec, ScriptSegment, ShotSpec, Story, TimelineScene
from engine.timeline import load_timeline


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

        explicit = self.load(
            self.shot(source_start_seconds=1.25, source_end_seconds=4.75)
        ).shots[0]
        self.assertEqual(explicit.source_start_seconds, 1.25)
        self.assertEqual(explicit.source_end_seconds, 4.75)

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

        for values in (
            {"source_start_seconds": 0.1},
            {"source_end_seconds": 1.0},
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
        )
        scene = TimelineScene(
            index=1,
            shot=shot,
            asset=video,
            start_frame=0,
            end_frame=semantic_frames,
            render_frames=semantic_frames + transition_frames,
            transition_frames=transition_frames,
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
