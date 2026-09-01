import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.config import load_project_config, load_style_config
from engine.ffmpeg import (
    parse_loudnorm_measurement,
    preflight,
    probe_duration,
    probe_video_frame_count,
    run_ffmpeg,
    run_ffmpeg_capture,
)
from engine.models import AudioResult, ResolvedBackgroundMusic, ResolvedSfxCue
from engine.renderer import Renderer
from tests.test_background_music import write_wave
from tests.loudnorm_fixture import LOUDNORM_ANALYSIS_OUTPUT


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_renderer(root: Path) -> Renderer:
    config = load_project_config(PROJECT_ROOT / "config" / "config.json")
    style = load_style_config(PROJECT_ROOT / "config" / "style.json")
    return Renderer(root, root / "work", root / "output", config, style)


class FinalAudioNormalizationTests(unittest.TestCase):
    def test_two_pass_loudnorm_runs_after_mix_and_uses_measured_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            music = root / "music.wav"
            effect = root / "impact.wav"
            for path in (video, voice, music, effect):
                path.write_bytes(b"fixture")

            calls: list[tuple[str, list[object]]] = []

            def fake_analysis(arguments, cwd=None):
                calls.append(("analysis", list(arguments)))
                return LOUDNORM_ANALYSIS_OUTPUT

            def fake_application(arguments, cwd=None):
                calls.append(("application", list(arguments)))
                Path(arguments[-1]).write_bytes(b"normalized")

            renderer = make_renderer(root)
            with (
                patch(
                    "engine.renderer.run_ffmpeg_capture",
                    side_effect=fake_analysis,
                ) as analysis_call,
                patch(
                    "engine.renderer.run_ffmpeg",
                    side_effect=fake_application,
                ) as application_call,
                patch("engine.renderer.probe_video_frame_count", return_value=60),
                patch("engine.renderer.probe_duration", return_value=2.0),
            ):
                output = renderer.mux_audio(
                    video,
                    AudioResult(voice, 2.0, (), "test", True),
                    "normalized.mp4",
                    background_music=ResolvedBackgroundMusic(
                        "documentary_warm",
                        music,
                        0.12,
                    ),
                    sfx_cues=(
                        ResolvedSfxCue(1, 0.4, "impact", effect, 0.35),
                    ),
                )

            self.assertEqual([name for name, _ in calls], ["analysis", "application"])
            analysis_call.assert_called_once()
            application_call.assert_called_once()
            self.assertEqual(output.read_bytes(), b"normalized")

            analysis_arguments = calls[0][1]
            analysis_graph = analysis_arguments[
                analysis_arguments.index("-filter_complex") + 1
            ]
            final_mix = "[voice][ducked_music][sfx0]amix=inputs=3"
            self.assertIn(final_mix, analysis_graph)
            self.assertLess(analysis_graph.index(final_mix), analysis_graph.index("alimiter="))
            self.assertLess(analysis_graph.index("alimiter="), analysis_graph.index("loudnorm="))
            self.assertIn("loudnorm=I=-15.000:TP=-1.500:LRA=11.000", analysis_graph)
            self.assertIn("print_format=json", analysis_graph)
            self.assertEqual(analysis_arguments[-4:], ["-vn", "-f", "null", "-"])

            application_arguments = calls[1][1]
            application_graph = application_arguments[
                application_arguments.index("-filter_complex") + 1
            ]
            self.assertLess(
                application_graph.index(final_mix),
                application_graph.index("alimiter="),
            )
            self.assertLess(
                application_graph.index("alimiter="),
                application_graph.index("loudnorm="),
            )
            self.assertIn("measured_I=-23.450000", application_graph)
            self.assertIn("measured_TP=-3.210000", application_graph)
            self.assertIn("measured_LRA=4.560000", application_graph)
            self.assertIn("measured_thresh=-34.670000", application_graph)
            self.assertIn("offset=0.890000", application_graph)
            self.assertIn("linear=true", application_graph)
            self.assertEqual(
                application_arguments[application_arguments.index("-map") + 1],
                "0:v:0",
            )
            audio_map_index = application_arguments.index(
                "-map", application_arguments.index("-map") + 1
            )
            self.assertEqual(application_arguments[audio_map_index + 1], "[normalized_audio]")

    def test_invalid_first_pass_measurement_fails_clearly_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            video.write_bytes(b"video")
            voice.write_bytes(b"voice")
            renderer = make_renderer(root)

            with (
                patch(
                    "engine.renderer.run_ffmpeg_capture",
                    return_value="FFmpeg terminou sem JSON loudnorm",
                ) as analysis_call,
                patch("engine.renderer.run_ffmpeg") as application_call,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Falha na normalizacao.*analise.*medicoes esperadas",
                ):
                    renderer.mux_audio(
                        video,
                        AudioResult(voice, 2.0, (), "test", True),
                        "invalid.mp4",
                    )

            analysis_call.assert_called_once()
            application_call.assert_not_called()
            self.assertFalse((root / "output" / "invalid.mp4").exists())
            self.assertFalse((root / "output" / "invalid.part.mp4").exists())


class FinalAudioNormalizationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight(require_background_music=True, require_sfx=True)

    def test_real_mix_reaches_target_and_preserves_video_and_sources(self):
        duration = 3.2
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            music = root / "music.wav"
            effect = root / "impact.wav"
            write_wave(voice, duration, frequency=440.0)
            write_wave(music, 0.8, frequency=220.0)
            write_wave(effect, 0.4, frequency=880.0)
            run_ffmpeg(
                [
                    "-y",
                    "-hide_banner",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black:s=90x160:r=30:d={duration}",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    video,
                ]
            )
            sources = (video, voice, music, effect)
            original_contents = {path: path.read_bytes() for path in sources}
            original_frames = probe_video_frame_count(video)

            output = make_renderer(root).mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "normalized.mp4",
                background_music=ResolvedBackgroundMusic(
                    "synthetic_music",
                    music,
                    0.12,
                ),
                sfx_cues=(
                    ResolvedSfxCue(1, 1.0, "impact", effect, 0.20),
                ),
            )

            measured_output = run_ffmpeg_capture(
                [
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    output,
                    "-map",
                    "0:a:0",
                    "-af",
                    "loudnorm=I=-15:TP=-1:LRA=11:print_format=json",
                    "-f",
                    "null",
                    "-",
                ]
            )
            loudness = parse_loudnorm_measurement(measured_output)

            self.assertAlmostEqual(loudness.input_i, -15.0, delta=0.6)
            # The loudnorm pass reserves AAC headroom so the encoded MP4 honors
            # the public -1 dBTP ceiling as well as the pre-encode PCM mix.
            self.assertLessEqual(loudness.input_tp, -1.0)
            self.assertEqual(probe_video_frame_count(output), original_frames)
            self.assertAlmostEqual(probe_duration(output), duration, delta=0.06)
            for path, original in original_contents.items():
                with self.subTest(source=path.name):
                    self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
