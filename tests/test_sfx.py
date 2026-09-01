from dataclasses import replace
import hashlib
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import wave

from engine.config import load_project_config, load_style_config
from engine.ffmpeg import preflight, probe_duration, probe_video_frame_count, run_ffmpeg
from engine.models import (
    AudioResult,
    ResolvedBackgroundMusic,
    ResolvedSfxCue,
    SfxCue,
)
from engine.pipeline import build_video
from engine.renderer import Renderer
from engine.sfx import resolve_sfx_cues
from tests.loudnorm_fixture import LOUDNORM_ANALYSIS_OUTPUT


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 48_000


def write_sfx_catalog(root: Path, types: dict[str, list[str]]) -> Path:
    sfx_root = root / "assets" / "audio" / "sfx"
    sfx_root.mkdir(parents=True, exist_ok=True)
    (sfx_root / "catalog.json").write_text(
        json.dumps({"schema_version": 1, "types": types}),
        encoding="utf-8",
    )
    return sfx_root


def write_wave(
    path: Path,
    duration: float,
    frequency: float | None,
    amplitude: int = 10_000,
    sample_rate: int = SAMPLE_RATE,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for index in range(round(duration * sample_rate)):
        sample = 0
        if frequency is not None:
            sample = round(
                amplitude * math.sin(2 * math.pi * frequency * index / sample_rate)
            )
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(bytes(frames))


def write_segmented_wave(
    path: Path,
    segments: tuple[tuple[float, float], ...],
    amplitude: int = 10_000,
    sample_rate: int = SAMPLE_RATE,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for duration, frequency in segments:
        for index in range(round(duration * sample_rate)):
            sample = round(
                amplitude * math.sin(2 * math.pi * frequency * index / sample_rate)
            )
            frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(bytes(frames))


def make_video(path: Path, duration: float) -> None:
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
            path,
        ]
    )


def decode_audio(input_path: Path, output_path: Path) -> tuple[int, tuple[int, ...]]:
    run_ffmpeg(
        [
            "-y",
            "-hide_banner",
            "-i",
            input_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            SAMPLE_RATE,
            "-c:a",
            "pcm_s16le",
            output_path,
        ]
    )
    with wave.open(str(output_path), "rb") as source:
        sample_rate = source.getframerate()
        raw_samples = source.readframes(source.getnframes())
    samples = struct.unpack(f"<{len(raw_samples) // 2}h", raw_samples)
    return sample_rate, samples


def window_samples(
    samples: tuple[int, ...],
    sample_rate: int,
    start: float,
    end: float,
) -> tuple[int, ...]:
    return samples[round(start * sample_rate) : round(end * sample_rate)]


def rms(samples: tuple[int, ...]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def tone_magnitude(samples: tuple[int, ...], sample_rate: int, frequency: float) -> float:
    if not samples:
        return 0.0
    mean = sum(samples) / len(samples)
    cosine = 0.0
    sine = 0.0
    for index, sample in enumerate(samples):
        centered = sample - mean
        angle = 2 * math.pi * frequency * index / sample_rate
        cosine += centered * math.cos(angle)
        sine += centered * math.sin(angle)
    return 2 * math.hypot(cosine, sine) / len(samples)


def make_renderer(root: Path, config=None) -> Renderer:
    config = config or load_project_config(PROJECT_ROOT / "config" / "config.json")
    style = load_style_config(PROJECT_ROOT / "config" / "style.json")
    return Renderer(root, root / "work", root / "output", config, style)


class SfxResolverTests(unittest.TestCase):
    def test_absent_cues_do_not_require_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result = resolve_sfx_cues(Path(temp_dir), (), "old_episode")

        self.assertEqual(result, ())

    def test_type_resolves_deterministically_and_varies_by_cue_position(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sfx_root = write_sfx_catalog(
                root,
                {"whoosh": ["whoosh/b.wav", "whoosh/a.wav"]},
            )
            first_file = sfx_root / "whoosh" / "a.wav"
            second_file = sfx_root / "whoosh" / "b.wav"
            first_file.parent.mkdir()
            first_file.write_bytes(b"a")
            second_file.write_bytes(b"b")
            cues = tuple(
                SfxCue(time_seconds=index / 10, type="whoosh", volume=0.35)
                for index in range(4)
            )

            first = resolve_sfx_cues(root, cues, "duckworth")
            write_sfx_catalog(
                root,
                {"whoosh": ["whoosh/a.wav", "whoosh/b.wav"]},
            )
            second = resolve_sfx_cues(root, cues, "duckworth")

        self.assertEqual([cue.path for cue in first], [cue.path for cue in second])
        self.assertEqual({cue.path for cue in first}, {first_file.resolve(), second_file.resolve()})
        self.assertEqual([cue.index for cue in first], [1, 2, 3, 4])
        self.assertEqual(first[2].time_seconds, 0.2)
        self.assertEqual(first[2].type, "whoosh")
        self.assertEqual(first[2].volume, 0.35)
        self.assertEqual(first[2].source_start_seconds, 0.0)
        self.assertIsNone(first[2].duration_seconds)

    def test_source_trim_is_validated_propagated_and_probed_once_per_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sfx_root = write_sfx_catalog(root, {"piano": ["piano/clip.wav"]})
            effect = sfx_root / "piano" / "clip.wav"
            effect.parent.mkdir()
            effect.write_bytes(b"effect")
            cues = (
                SfxCue(0.2, "piano", 0.1, 1.5, 3.5),
                SfxCue(3.0, "piano", 0.2, 0.25, None),
            )

            with patch("engine.sfx.probe_duration", return_value=5.0) as probe:
                resolved = resolve_sfx_cues(root, cues, "demo")

        probe.assert_called_once_with(effect.resolve())
        self.assertEqual(resolved[0].source_start_seconds, 1.5)
        self.assertEqual(resolved[0].duration_seconds, 3.5)
        self.assertEqual(resolved[1].source_start_seconds, 0.25)
        self.assertIsNone(resolved[1].duration_seconds)

    def test_legacy_cue_does_not_probe_source_duration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sfx_root = write_sfx_catalog(root, {"impact": ["impact/clip.wav"]})
            effect = sfx_root / "impact" / "clip.wav"
            effect.parent.mkdir()
            effect.write_bytes(b"legacy")

            with patch("engine.sfx.probe_duration") as probe:
                resolved = resolve_sfx_cues(
                    root, (SfxCue(0.2, "impact", 0.5),), "legacy"
                )

        probe.assert_not_called()
        self.assertEqual(resolved[0].path, effect.resolve())

    def test_source_trim_cannot_exceed_real_file_duration(self):
        cases = (
            (SfxCue(0.2, "impact", 0.5, 2.0, None), "no ou apos o fim"),
            (SfxCue(0.2, "impact", 0.5, 1.5, 0.6), "ultrapassa a duracao real"),
        )
        for cue, message in cases:
            with self.subTest(cue=cue), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                sfx_root = write_sfx_catalog(
                    root, {"impact": ["impact/clip.wav"]}
                )
                effect = sfx_root / "impact" / "clip.wav"
                effect.parent.mkdir()
                effect.write_bytes(b"effect")
                with patch("engine.sfx.probe_duration", return_value=2.0):
                    with self.assertRaisesRegex(RuntimeError, message):
                        resolve_sfx_cues(root, (cue,), "demo")

    def test_unknown_type_names_type_and_catalog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_sfx_catalog(root, {})

            with self.assertRaisesRegex(
                RuntimeError,
                r"Type de SFX inexistente: 'impact'.*assets/audio/sfx/catalog.json",
            ):
                resolve_sfx_cues(root, (SfxCue(0.2, "impact", 0.5),), "demo")

    def test_missing_file_has_clear_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_sfx_catalog(root, {"impact": ["impact/missing.wav"]})

            with self.assertRaisesRegex(
                RuntimeError,
                r"Arquivo local do type de SFX 'impact' nao encontrado: "
                r"impact/missing.wav",
            ):
                resolve_sfx_cues(root, (SfxCue(0.2, "impact", 0.5),), "demo")

    def test_unsafe_and_unsupported_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = (root / "outside.wav").resolve()
            outside.write_bytes(b"outside")
            cases = (
                ("../../../outside.wav", "local e relativo"),
                (str(outside), "local e relativo"),
                ("https://example.com/impact.wav", "local e relativo"),
                ("impact/impact.txt", "nao suportado"),
            )
            for value, message in cases:
                with self.subTest(value=value):
                    write_sfx_catalog(root, {"impact": [value]})
                    with self.assertRaisesRegex(RuntimeError, message):
                        resolve_sfx_cues(
                            root,
                            (SfxCue(0.2, "impact", 0.5),),
                            "demo",
                        )

    def test_symlink_cannot_escape_sfx_library(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root / "outside.wav"
            outside.write_bytes(b"outside")
            sfx_root = write_sfx_catalog(root, {"impact": ["impact/link.wav"]})
            link = sfx_root / "impact" / "link.wav"
            link.parent.mkdir()
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"Symlink indisponivel neste ambiente: {exc}")

            with self.assertRaisesRegex(RuntimeError, "fora da biblioteca"):
                resolve_sfx_cues(root, (SfxCue(0.2, "impact", 0.5),), "demo")


class SfxPipelineOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_error_happens_before_heavy_render_work(self):
        config = SimpleNamespace(
            paths=SimpleNamespace(
                episodes_dir="episodes",
                work_dir="work",
                output_dir="output",
                cache_dir="cache",
            )
        )
        episode = SimpleNamespace(
            background_music=None,
            sfx_cues=(SfxCue(0.2, "missing", 0.5),),
            name="demo",
            directory=PROJECT_ROOT / "episodes" / "demo",
        )
        with (
            patch("engine.pipeline.load_project_config", return_value=config),
            patch("engine.pipeline.load_style_config", return_value=object()),
            patch("engine.pipeline.load_episode", return_value=episode),
            patch("engine.pipeline.resolve_background_music", return_value=None),
            patch(
                "engine.pipeline.resolve_sfx_cues",
                side_effect=RuntimeError("Type de SFX inexistente: 'missing'"),
            ) as resolve_sfx,
            patch("engine.pipeline._reset_episode_work_dir") as reset_work,
            patch("engine.pipeline.resolve_audio", new_callable=AsyncMock) as resolve_audio,
        ):
            with self.assertRaisesRegex(RuntimeError, "Type de SFX inexistente"):
                await build_video(PROJECT_ROOT, "demo")

        reset_work.assert_not_called()
        resolve_audio.assert_not_awaited()
        resolve_sfx.assert_called_once_with(
            PROJECT_ROOT.resolve(),
            episode.sfx_cues,
            "demo",
            PROJECT_ROOT.resolve() / "cache",
        )


class SfxMuxGraphTests(unittest.TestCase):
    def _capture_mux_arguments(
        self,
        root: Path,
        sfx_cues: tuple[ResolvedSfxCue, ...] | None,
        background_music: ResolvedBackgroundMusic | None = None,
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
            keywords = {"background_music": background_music}
            if sfx_cues is not None:
                keywords["sfx_cues"] = sfx_cues
            renderer.mux_audio(
                video,
                AudioResult(voice, 2.0, (), "test", True),
                "result.mp4",
                **keywords,
            )

        return mocked.call_args.args[0]

    def test_empty_cues_keep_existing_commands_exactly(self):
        for with_music in (False, True):
            with self.subTest(with_music=with_music):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    background_music = None
                    if with_music:
                        track = root / "music.wav"
                        track.write_bytes(b"music")
                        background_music = ResolvedBackgroundMusic(
                            "documentary_warm", track, 0.12
                        )
                    omitted = self._capture_mux_arguments(
                        root,
                        None,
                        background_music,
                    )
                    explicit_empty = self._capture_mux_arguments(
                        root,
                        (),
                        background_music,
                    )

                self.assertEqual(omitted, explicit_empty)

    def test_sfx_uses_sample_accurate_position_volume_and_final_limiter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            effect = root / "impact.wav"
            effect.write_bytes(b"effect")
            arguments = self._capture_mux_arguments(
                root,
                (ResolvedSfxCue(1, 0.35, "impact", effect, 0.4),),
            )

        self.assertEqual(arguments.count("-i"), 3)
        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn("atrim=duration=1.650000", graph)
        self.assertNotIn("atrim=start=", graph)
        self.assertIn("volume=0.400000", graph)
        self.assertIn("anullsrc=r=48000:cl=stereo", graph)
        self.assertIn("atrim=end_sample=16800", graph)
        self.assertIn("[sfx_silence0][sfx_effect0]concat=n=2:v=0:a=1", graph)
        self.assertIn("[voice][sfx0]amix=inputs=2", graph)
        self.assertLess(graph.index("amix=inputs=2"), graph.index("alimiter="))

    def test_source_trim_keeps_time_position_volume_and_existing_mix_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            effect = root / "cinematic-piano.wav"
            effect.write_bytes(b"effect")
            arguments = self._capture_mux_arguments(
                root,
                (
                    ResolvedSfxCue(
                        1,
                        0.35,
                        "cinematic_piano",
                        effect,
                        0.10,
                        1.5,
                        0.3,
                    ),
                ),
            )

        graph = arguments[arguments.index("-filter_complex") + 1]
        trim = "atrim=start=1.500000:duration=0.300000"
        delay = "atrim=end_sample=16800"
        self.assertIn(trim, graph)
        self.assertIn("volume=0.100000", graph)
        self.assertIn(delay, graph)
        self.assertLess(graph.index(trim), graph.index("volume=0.100000"))
        self.assertLess(graph.index("volume=0.100000"), graph.index(delay))

    def test_requested_sfx_duration_is_clipped_at_video_end(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            effect = root / "impact.wav"
            effect.write_bytes(b"effect")
            arguments = self._capture_mux_arguments(
                root,
                (ResolvedSfxCue(1, 1.8, "impact", effect, 0.5, 0.25, 0.5),),
            )

        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn("atrim=start=0.250000:duration=0.200000", graph)

    def test_overlapping_cues_have_independent_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "impact.wav"
            second = root / "whoosh.wav"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            arguments = self._capture_mux_arguments(
                root,
                (
                    ResolvedSfxCue(1, 0.20, "impact", first, 0.5),
                    ResolvedSfxCue(2, 0.35, "whoosh", second, 0.3),
                ),
            )

        self.assertEqual(arguments.count("-i"), 4)
        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn("[2:a]", graph)
        self.assertIn("[3:a]", graph)
        self.assertIn("atrim=end_sample=9600", graph)
        self.assertIn("atrim=end_sample=16800", graph)
        self.assertIn("[voice][sfx0][sfx1]amix=inputs=3", graph)

    def test_background_music_is_ducked_only_by_voice_before_sfx_mix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music = root / "music.wav"
            effect = root / "whoosh.wav"
            music.write_bytes(b"music")
            effect.write_bytes(b"effect")
            arguments = self._capture_mux_arguments(
                root,
                (ResolvedSfxCue(1, 0.5, "whoosh", effect, 0.35),),
                ResolvedBackgroundMusic("ambient", music, 0.12),
            )

        graph = arguments[arguments.index("-filter_complex") + 1]
        sidechain = "[music][duck_control]sidechaincompress="
        final_mix = "[voice][ducked_music][sfx0]amix=inputs=3"
        self.assertIn(sidechain, graph)
        self.assertIn(final_mix, graph)
        self.assertLess(graph.index(sidechain), graph.index(final_mix))
        self.assertNotIn("[sfx0][duck_control]", graph)
        self.assertLess(graph.index(final_mix), graph.index("alimiter="))

    def test_legacy_music_file_still_mixes_with_sfx_without_sidechain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            music = root / "legacy.wav"
            effect = root / "impact.wav"
            music.write_bytes(b"music")
            effect.write_bytes(b"effect")
            config = load_project_config(PROJECT_ROOT / "config" / "config.json")
            config = replace(
                config,
                mix=replace(config.mix, music_file=str(music)),
            )
            arguments = self._capture_mux_arguments(
                root,
                (ResolvedSfxCue(1, 0.4, "impact", effect, 0.3),),
                config=config,
            )

        graph = arguments[arguments.index("-filter_complex") + 1]
        self.assertIn("[voice][music]amix=inputs=2:duration=first", graph)
        self.assertIn("[voice_music][sfx0]amix=inputs=2", graph)
        self.assertNotIn("sidechaincompress", graph)
        self.assertLess(
            graph.index("[voice_music][sfx0]amix=inputs=2"),
            graph.index("alimiter="),
        )

    def test_cue_at_or_after_end_is_warned_and_does_not_change_mix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            effect = root / "late.wav"
            effect.write_bytes(b"effect")
            baseline = self._capture_mux_arguments(root, ())
            with patch("builtins.print") as logged:
                after_end = self._capture_mux_arguments(
                    root,
                    (ResolvedSfxCue(1, 2.0, "impact", effect, 0.5),),
                )

        self.assertEqual(after_end, baseline)
        warning = " ".join(str(call) for call in logged.call_args_list)
        self.assertIn("ignorada", warning)
        self.assertIn("impact", warning)
        self.assertIn("time_seconds=2.000s", warning)
        self.assertIn("video (2.000s)", warning)


class SfxMuxIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight(require_background_music=True, require_sfx=True)

    def test_voice_and_sfx_start_at_configured_timestamp(self):
        duration = 1.0
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            effect = root / "impact.wav"
            decoded = root / "decoded.wav"
            make_video(video, duration)
            write_wave(voice, duration, None)
            write_wave(effect, 0.16, 2_000.0)

            output = make_renderer(root).mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "result.mp4",
                sfx_cues=(ResolvedSfxCue(1, 0.35, "impact", effect, 0.6),),
            )
            sample_rate, samples = decode_audio(output, decoded)
            before = rms(window_samples(samples, sample_rate, 0.20, 0.28))
            during = rms(window_samples(samples, sample_rate, 0.38, 0.44))

            self.assertEqual(
                probe_video_frame_count(output),
                probe_video_frame_count(video),
            )
            self.assertAlmostEqual(probe_duration(output), duration, delta=0.06)

        self.assertLess(before, 30)
        self.assertGreater(during, 500)

    def test_source_trim_selects_requested_excerpt_without_mutating_original(self):
        duration = 1.0
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            effect = root / "segmented.wav"
            decoded = root / "decoded.wav"
            make_video(video, duration)
            write_wave(voice, duration, None)
            write_segmented_wave(
                effect,
                ((0.30, 500.0), (0.25, 2_000.0), (0.25, 900.0)),
            )
            original_hash = hashlib.sha256(effect.read_bytes()).hexdigest()

            output = make_renderer(root).mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "trimmed.mp4",
                sfx_cues=(
                    ResolvedSfxCue(
                        1,
                        0.20,
                        "cinematic_piano",
                        effect,
                        0.60,
                        0.30,
                        0.20,
                    ),
                ),
            )
            sample_rate, samples = decode_audio(output, decoded)
            before = rms(window_samples(samples, sample_rate, 0.05, 0.15))
            excerpt = window_samples(samples, sample_rate, 0.24, 0.36)
            after = rms(window_samples(samples, sample_rate, 0.55, 0.65))

            self.assertEqual(
                hashlib.sha256(effect.read_bytes()).hexdigest(), original_hash
            )
            self.assertEqual(
                probe_video_frame_count(output),
                probe_video_frame_count(video),
            )
            self.assertAlmostEqual(probe_duration(output), duration, delta=0.06)

        self.assertLess(before, 30)
        self.assertGreater(tone_magnitude(excerpt, sample_rate, 2_000.0), 300)
        self.assertLess(tone_magnitude(excerpt, sample_rate, 500.0), 120)
        self.assertLess(after, 80)

    def test_sfx_near_end_is_clipped_without_extending_output(self):
        duration = 0.8
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            effect = root / "riser.wav"
            decoded = root / "decoded.wav"
            make_video(video, duration)
            write_wave(voice, duration, None)
            write_wave(effect, 0.30, 1_500.0)

            output = make_renderer(root).mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "clipped.mp4",
                sfx_cues=(ResolvedSfxCue(1, 0.68, "riser", effect, 0.5),),
            )
            sample_rate, samples = decode_audio(output, decoded)
            late = rms(window_samples(samples, sample_rate, 0.71, 0.77))

            self.assertEqual(
                probe_video_frame_count(output),
                probe_video_frame_count(video),
            )
            self.assertAlmostEqual(probe_duration(output), duration, delta=0.06)

        self.assertGreater(late, 300)

    def test_two_sfx_can_overlap(self):
        duration = 0.9
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            impact = root / "impact.wav"
            whoosh = root / "whoosh.wav"
            decoded = root / "decoded.wav"
            make_video(video, duration)
            write_wave(voice, duration, None)
            write_wave(impact, 0.35, 500.0)
            write_wave(whoosh, 0.30, 2_000.0)

            output = make_renderer(root).mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "overlap.mp4",
                sfx_cues=(
                    ResolvedSfxCue(1, 0.20, "impact", impact, 0.35),
                    ResolvedSfxCue(2, 0.28, "whoosh", whoosh, 0.35),
                ),
            )
            sample_rate, samples = decode_audio(output, decoded)
            overlap = window_samples(samples, sample_rate, 0.33, 0.43)
            impact_level = tone_magnitude(overlap, sample_rate, 500.0)
            whoosh_level = tone_magnitude(overlap, sample_rate, 2_000.0)

            self.assertEqual(
                probe_video_frame_count(output),
                probe_video_frame_count(video),
            )
            self.assertAlmostEqual(probe_duration(output), duration, delta=0.06)

        self.assertGreater(impact_level, 300)
        self.assertGreater(whoosh_level, 300)

    def test_voice_background_music_and_sfx_mix_without_sfx_ducking_music(self):
        duration = 2.0
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "video.mp4"
            voice = root / "voice.wav"
            music = root / "music.wav"
            effect = root / "whoosh.wav"
            decoded = root / "decoded.wav"
            make_video(video, duration)
            write_wave(voice, duration, 1_000.0, amplitude=3_000)
            write_wave(music, 0.40, 220.0, amplitude=10_000)
            write_wave(effect, 0.25, 4_000.0, amplitude=8_000)
            config = load_project_config(PROJECT_ROOT / "config" / "config.json")
            config = replace(
                config,
                mix=replace(
                    config.mix,
                    background_music_fade_in_seconds=0.05,
                    background_music_fade_out_seconds=0.05,
                ),
            )

            output = make_renderer(root, config).mux_audio(
                video,
                AudioResult(voice, duration, (), "synthetic", True),
                "complete.mp4",
                background_music=ResolvedBackgroundMusic(
                    "synthetic_music", music, 0.25
                ),
                sfx_cues=(ResolvedSfxCue(1, 1.0, "whoosh", effect, 0.40),),
            )
            sample_rate, samples = decode_audio(output, decoded)
            before = window_samples(samples, sample_rate, 0.65, 0.75)
            during = window_samples(samples, sample_rate, 1.07, 1.17)
            music_before = tone_magnitude(before, sample_rate, 220.0)
            music_during = tone_magnitude(during, sample_rate, 220.0)
            sfx_during = tone_magnitude(during, sample_rate, 4_000.0)

            self.assertEqual(
                probe_video_frame_count(output),
                probe_video_frame_count(video),
            )
            self.assertAlmostEqual(probe_duration(output), duration, delta=0.06)

        self.assertGreater(music_before, 50)
        self.assertGreater(music_during, music_before * 0.75)
        self.assertLess(music_during, music_before * 1.25)
        self.assertGreater(sfx_during, 300)


if __name__ == "__main__":
    unittest.main()
