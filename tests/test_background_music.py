from dataclasses import replace
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import wave

from engine.config import load_project_config, load_style_config
from engine.ffmpeg import (
    preflight,
    probe_duration,
    probe_video_frame_count,
    run_ffmpeg,
)
from engine.models import AudioResult, BackgroundMusicSpec, ResolvedBackgroundMusic
from engine.music import resolve_background_music
from engine.renderer import Renderer
from tests.loudnorm_fixture import LOUDNORM_ANALYSIS_OUTPUT


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_catalog(root: Path, profiles: dict[str, list[str]]) -> Path:
    music_root = root / "assets" / "audio" / "music"
    music_root.mkdir(parents=True, exist_ok=True)
    catalog = music_root / "catalog.json"
    catalog.write_text(
        json.dumps({"schema_version": 1, "profiles": profiles}),
        encoding="utf-8",
    )
    return music_root


def write_wave(
    path: Path,
    duration: float,
    frequency: float | None,
    sample_rate: int = 48_000,
    active_start: float = 0.0,
    active_end: float | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = round(duration * sample_rate)
    frames = bytearray()
    active_end = duration if active_end is None else active_end
    for index in range(frame_count):
        sample = 0
        time_seconds = index / sample_rate
        if frequency is not None and active_start <= time_seconds < active_end:
            sample = round(
                16_000 * math.sin(2 * math.pi * frequency * index / sample_rate)
            )
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(bytes(frames))


def wave_rms(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        raw_samples = source.readframes(source.getnframes())
    samples = struct.unpack(f"<{len(raw_samples) // 2}h", raw_samples)
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def make_renderer(root: Path, config=None) -> Renderer:
    config = config or load_project_config(PROJECT_ROOT / "config" / "config.json")
    style = load_style_config(PROJECT_ROOT / "config" / "style.json")
    return Renderer(root, root / "work", root / "output", config, style)


class MusicProfileTests(unittest.TestCase):
    def test_absent_background_music_does_not_require_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = resolve_background_music(Path(temp_dir), None, "old_episode")

        self.assertIsNone(result)

    def test_existing_profile_resolves_local_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music_root = write_catalog(
                root,
                {"latin_pop_uplifting": ["latin_pop_uplifting/track.wav"]},
            )
            track = music_root / "latin_pop_uplifting" / "track.wav"
            track.parent.mkdir()
            track.write_bytes(b"local fixture")

            result = resolve_background_music(
                root,
                BackgroundMusicSpec("latin_pop_uplifting", 0.12),
                "demo_episode",
            )

        self.assertEqual(result.profile, "latin_pop_uplifting")
        self.assertEqual(result.path, track.resolve())
        self.assertEqual(result.volume, 0.12)

    def test_multiple_tracks_are_selected_deterministically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music_root = write_catalog(root, {"documentary_warm": ["b.wav", "a.wav"]})
            (music_root / "a.wav").write_bytes(b"a")
            (music_root / "b.wav").write_bytes(b"b")
            spec = BackgroundMusicSpec("documentary_warm", 0.1)

            first = resolve_background_music(root, spec, "episode_slug")
            write_catalog(root, {"documentary_warm": ["a.wav", "b.wav"]})
            second = resolve_background_music(root, spec, "episode_slug")

        self.assertEqual(first.path, second.path)

    def test_unknown_profile_has_clear_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_catalog(root, {})

            with self.assertRaisesRegex(
                RuntimeError,
                "Profile de background music inexistente: 'missing_profile'",
            ):
                resolve_background_music(
                    root,
                    BackgroundMusicSpec("missing_profile", 0.1),
                    "demo",
                )

    def test_profile_cannot_escape_local_music_library(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_catalog(root, {"unsafe": ["../../../outside.wav"]})
            (root / "outside.wav").write_bytes(b"outside")

            with self.assertRaisesRegex(RuntimeError, "local e relativo"):
                resolve_background_music(
                    root,
                    BackgroundMusicSpec("unsafe", 0.1),
                    "demo",
                )

    def test_profile_rejects_absolute_and_remote_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = (root / "outside.wav").resolve()
            outside.write_bytes(b"outside")
            cases = (str(outside), "https://example.com/music.mp3")

            for value in cases:
                with self.subTest(value=value):
                    write_catalog(root, {"unsafe": [value]})
                    with self.assertRaisesRegex(RuntimeError, "local e relativo"):
                        resolve_background_music(
                            root,
                            BackgroundMusicSpec("unsafe", 0.1),
                            "demo",
                        )


class BackgroundMusicMuxTests(unittest.TestCase):
    def _capture_mux_arguments(
        self,
        root: Path,
        background_music: ResolvedBackgroundMusic | None,
        config=None,
    ) -> list[object]:
        video = root / "video.mp4"
        voice = root / "voice.wav"
        video.write_bytes(b"video")
        voice.write_bytes(b"voice")
        renderer = make_renderer(root, config)

        def fake_run_ffmpeg(arguments, cwd=None):
            Path(arguments[-1]).write_bytes(b"muxed")

        with (
            patch("engine.renderer.run_ffmpeg", side_effect=fake_run_ffmpeg) as mocked,
            patch(
                "engine.renderer.run_ffmpeg_capture",
                return_value=LOUDNORM_ANALYSIS_OUTPUT,
            ),
            patch("engine.renderer.probe_video_frame_count", return_value=60),
            patch("engine.renderer.probe_duration", return_value=2.0),
        ):
            renderer.mux_audio(
                video,
                AudioResult(voice, 2.0, (), "test", True),
                "result.mp4",
                background_music=background_music,
            )

        return mocked.call_args.args[0]

    def test_short_music_is_prepared_for_loop_trim_fades_and_ducking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            track = root / "short.wav"
            track.write_bytes(b"short local music")
            arguments = self._capture_mux_arguments(
                root,
                ResolvedBackgroundMusic("latin_pop_uplifting", track, 0.12),
            )

        self.assertEqual(arguments.count("-i"), 3)
        self.assertLess(
            arguments.index("-stream_loop"),
            arguments.index(track.resolve()),
        )
        filter_graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn("atrim=duration=2.000000", filter_graph)
        self.assertIn("volume=0.120000", filter_graph)
        self.assertIn("afade=t=in:st=0:d=0.750000", filter_graph)
        self.assertIn("afade=t=out:st=1.000000:d=1.000000", filter_graph)
        self.assertIn("[music][duck_control]sidechaincompress=", filter_graph)
        self.assertIn("[voice][ducked_music]amix=", filter_graph)

    def test_mux_without_episode_music_keeps_voice_only_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            arguments = self._capture_mux_arguments(Path(temp_dir), None)

        self.assertEqual(arguments.count("-i"), 2)
        self.assertNotIn("-stream_loop", arguments)
        filter_graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertNotIn("sidechaincompress", filter_graph)
        self.assertNotIn("amix", filter_graph)
        self.assertNotIn("afade", filter_graph)

    def test_global_music_file_keeps_legacy_mix_when_episode_music_is_absent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            track = root / "legacy.wav"
            track.write_bytes(b"legacy")
            config = load_project_config(PROJECT_ROOT / "config" / "config.json")
            config = replace(
                config,
                mix=replace(config.mix, music_file=str(track)),
            )

            arguments = self._capture_mux_arguments(root, None, config)

        filter_graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn(f"volume={config.mix.music_volume:.4f}", filter_graph)
        self.assertIn("amix=inputs=2:duration=first", filter_graph)
        self.assertNotIn("sidechaincompress", filter_graph)

    def test_mux_with_short_synthetic_music_keeps_audio_until_end(self):
        duration = 1.2
        preflight(require_background_music=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            music = root / "music.wav"
            late_audio = root / "late.wav"
            write_wave(voice, duration, frequency=None)
            write_wave(music, 0.25, frequency=220.0)
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
            renderer = make_renderer(root)
            output = renderer.mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "result.mp4",
                background_music=ResolvedBackgroundMusic(
                    "synthetic_music",
                    music,
                    0.30,
                ),
            )
            run_ffmpeg(
                [
                    "-y",
                    "-hide_banner",
                    "-ss",
                    "0.45",
                    "-i",
                    output,
                    "-t",
                    "0.10",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "8000",
                    "-c:a",
                    "pcm_s16le",
                    late_audio,
                ]
            )

            self.assertEqual(
                probe_video_frame_count(output),
                probe_video_frame_count(video),
            )
            self.assertAlmostEqual(probe_duration(output), duration, delta=0.06)
            rms = wave_rms(late_audio)

        self.assertGreater(rms, 10)

    def test_sidechain_ducking_reduces_music_while_voice_is_active(self):
        duration = 4.0
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            music = root / "music.wav"
            write_wave(
                voice,
                duration,
                frequency=1000.0,
                active_start=1.2,
                active_end=2.0,
            )
            write_wave(music, 0.25, frequency=220.0)
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
            output = make_renderer(root).mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "ducked.mp4",
                background_music=ResolvedBackgroundMusic(
                    "synthetic_music",
                    music,
                    0.30,
                ),
            )

            levels: dict[str, float] = {}
            for label, start in (("before", 0.9), ("during", 1.5), ("after", 2.5)):
                sample_path = root / f"{label}.wav"
                run_ffmpeg(
                    [
                        "-y",
                        "-hide_banner",
                        "-i",
                        output,
                        "-ss",
                        start,
                        "-t",
                        "0.15",
                        "-map",
                        "0:a:0",
                        "-af",
                        "lowpass=f=400",
                        "-ac",
                        "1",
                        "-ar",
                        "8000",
                        "-c:a",
                        "pcm_s16le",
                        sample_path,
                    ]
                )
                levels[label] = wave_rms(sample_path)

        self.assertLess(levels["during"], levels["before"] * 0.70)
        self.assertGreater(levels["after"], levels["during"] * 1.25)

    def test_music_longer_than_video_is_trimmed_to_video_duration(self):
        duration = 0.8
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            music = root / "long_music.wav"
            write_wave(voice, duration, frequency=None)
            write_wave(music, 2.0, frequency=220.0)
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
            output = make_renderer(root).mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "trimmed.mp4",
                background_music=ResolvedBackgroundMusic(
                    "long_synthetic_music",
                    music,
                    0.20,
                ),
            )

            self.assertAlmostEqual(probe_duration(output), duration, delta=0.06)
            self.assertEqual(
                probe_video_frame_count(output),
                probe_video_frame_count(video),
            )


if __name__ == "__main__":
    unittest.main()
