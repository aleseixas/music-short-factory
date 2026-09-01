from contextlib import redirect_stdout
import io
import json
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from engine.audio import validate_audio_duration
from engine.config import load_project_config
from engine.models import AssetSpec, AudioResult, ScriptSegment, Story, WordTiming
from engine.pipeline import build_video
from engine.timeline import load_shots, load_timeline


class TimelineValidationTests(unittest.TestCase):
    def setUp(self):
        self.story = Story(
            title="Test",
            slug="test",
            target_duration_seconds=75,
            segments=(ScriptSegment("hook", "Texto do hook."),),
        )
        asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        self.assets = {asset.id: asset}

    def load(self, **changes: str):
        shot = {
            "id": "shot_hook",
            "segment": "hook",
            "asset": "photo",
            "motion": "hold",
            "transition_out": "cut",
        }
        shot.update(changes)
        data = {"schema_version": 1, "shots": [shot]}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "timeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_shots(path, self.story, self.assets)

    def load_with_options(self, **options: object):
        shot = {
            "id": "shot_hook",
            "segment": "hook",
            "asset": "photo",
            "motion": "hold",
            "transition_out": "cut",
        }
        data = {"schema_version": 1, "shots": [shot], **options}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "timeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_timeline(path, self.story, self.assets)

    def test_unknown_segment_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "exatamente um plano por segmento"):
            self.load(segment="missing")

    def test_unknown_asset_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Asset desconhecido.*missing"):
            self.load(asset="missing")

    def test_unknown_motion_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Movimento desconhecido.*shake"):
            self.load(motion="shake")

    def test_unknown_transition_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Transicao desconhecida.*dissolve"):
            self.load(transition_out="dissolve")

    def test_optional_audio_and_visual_configuration_is_loaded(self):
        timeline = self.load_with_options(
            background_music={"profile": "latin_pop_uplifting", "volume": 0.12},
            sfx_cues=[{"time_seconds": 0.3, "type": "impact", "volume": 0.5}],
            visual_fx_cues=[
                {"start_seconds": 0, "end_seconds": 3, "type": "slow_zoom_in"}
            ],
            text_fx_cues=[
                {"start_seconds": 0, "end_seconds": 1, "text": "MUITO BOM", "animation": "pop_in"}
            ],
        )

        self.assertEqual(timeline.background_music.profile, "latin_pop_uplifting")
        self.assertEqual(timeline.background_music.volume, 0.12)
        self.assertEqual(timeline.sfx_cues[0].time_seconds, 0.3)
        self.assertEqual(timeline.sfx_cues[0].type, "impact")
        self.assertEqual(timeline.sfx_cues[0].volume, 0.5)
        self.assertEqual(timeline.sfx_cues[0].source_start_seconds, 0.0)
        self.assertIsNone(timeline.sfx_cues[0].duration_seconds)
        self.assertEqual(timeline.visual_fx_cues[0].start_seconds, 0)
        self.assertEqual(timeline.visual_fx_cues[0].end_seconds, 3)
        self.assertEqual(timeline.visual_fx_cues[0].type, "slow_zoom_in")
        self.assertEqual(timeline.text_fx_cues[0].animation, "pop_in")
        self.assertEqual(timeline.text_fx_cues[0].intensity, 0.5)

    def test_sfx_source_trim_configuration_is_loaded(self):
        timeline = self.load_with_options(
            sfx_cues=[
                {
                    "time_seconds": 12.4,
                    "type": "cinematic_piano",
                    "volume": 0.10,
                    "source_start_seconds": 1.5,
                    "duration_seconds": 3.0,
                }
            ]
        )

        cue = timeline.sfx_cues[0]
        self.assertEqual(cue.time_seconds, 12.4)
        self.assertEqual(cue.source_start_seconds, 1.5)
        self.assertEqual(cue.duration_seconds, 3.0)

    def test_sfx_source_trim_rejects_invalid_values(self):
        cases = (
            ({"source_start_seconds": -0.01}, "maior ou igual a zero"),
            ({"source_start_seconds": float("nan")}, "numero finito"),
            ({"source_start_seconds": True}, "numero finito"),
            ({"duration_seconds": 0}, "maior que zero"),
            ({"duration_seconds": -0.1}, "maior que zero"),
            ({"duration_seconds": float("inf")}, "numero finito"),
            ({"duration_seconds": True}, "numero finito"),
        )
        for extra, message in cases:
            with self.subTest(extra=extra), self.assertRaisesRegex(RuntimeError, message):
                self.load_with_options(
                    sfx_cues=[
                        {
                            "time_seconds": 0.3,
                            "type": "impact",
                            "volume": 0.5,
                            **extra,
                        }
                    ]
                )

    def test_optional_configuration_defaults_are_empty(self):
        timeline = self.load_with_options()

        self.assertIsNone(timeline.background_music)
        self.assertEqual(timeline.sfx_cues, ())
        self.assertEqual(timeline.visual_fx_cues, ())
        self.assertEqual(timeline.text_fx_cues, ())

    def test_optional_configuration_rejects_invalid_volumes(self):
        cases = (
            {"background_music": {"profile": "ambient", "volume": -0.1}},
            {"sfx_cues": [{"time_seconds": 0, "type": "impact", "volume": 1.1}]},
        )
        for options in cases:
            with self.subTest(options=options):
                with self.assertRaisesRegex(RuntimeError, "precisa ficar entre 0 e 1"):
                    self.load_with_options(**options)

    def test_optional_configuration_rejects_invalid_times_and_names(self):
        cases = (
            (
                {
                    "sfx_cues": [
                        {"time_seconds": -0.1, "type": "impact", "volume": 0.5}
                    ]
                },
                "maior ou igual a zero",
            ),
            (
                {
                    "visual_fx_cues": [
                        {"start_seconds": 2, "end_seconds": 2, "type": "zoom"}
                    ]
                },
                "maior que start_seconds",
            ),
            (
                {"background_music": {"profile": "Latin Pop", "volume": 0.1}},
                "invalido",
            ),
            (
                {
                    "sfx_cues": [
                        {"time_seconds": 0.1, "type": "", "volume": 0.5}
                    ]
                },
                "invalido",
            ),
        )
        for options, message in cases:
            with self.subTest(options=options):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.load_with_options(**options)


class DurationValidationTests(unittest.TestCase):
    def test_duration_at_target_and_tolerance_boundaries_is_valid(self):
        for duration in (60.0, 75.0, 90.0):
            with self.subTest(duration=duration):
                self.assertIsNone(validate_audio_duration(duration, 75.0, 15.0))

    def test_narration_outside_target_returns_warning_and_continues(self):
        for duration in (55.0, 92.0):
            with self.subTest(duration=duration):
                warning = validate_audio_duration(duration, 80.0, 5.0)

                self.assertIsNotNone(warning)
                self.assertIn("fora do alvo", warning)
                self.assertIn(f"{duration:.2f}s", warning)
                self.assertIn("duracao real sera usada", warning)

    def test_non_positive_or_non_finite_duration_is_rejected(self):
        for duration in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(RuntimeError, "Duracao de audio invalida"):
                    validate_audio_duration(duration, 75.0, 15.0)

    def test_invalid_target_or_tolerance_remains_a_technical_error(self):
        cases = (
            ((75.0, 0.0, 15.0), "target_duration_seconds invalido"),
            ((75.0, 75.0, -1.0), "Tolerancia de duracao invalida"),
            ((75.0, 75.0, float("nan")), "Tolerancia de duracao invalida"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(RuntimeError, message):
                    validate_audio_duration(*arguments)


class PipelineDurationPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_warns_and_passes_real_duration_to_timeline(self):
        class StopAfterDuration(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            episode_dir = root / "episodes" / "demo"
            episode_dir.mkdir(parents=True)
            config = SimpleNamespace(
                paths=SimpleNamespace(
                    episodes_dir="episodes",
                    work_dir="work",
                    output_dir="output",
                    cache_dir="cache",
                ),
                duration=SimpleNamespace(target_tolerance_seconds=5.0),
                render=SimpleNamespace(width=720, height=1280, working_scale=1, fps=30),
                tts=object(),
            )
            style = SimpleNamespace(
                transitions=SimpleNamespace(crossfade_seconds=0.0),
            )
            episode = SimpleNamespace(
                name="demo",
                directory=episode_dir,
                assets_dir=episode_dir / "assets",
                story=Story(
                    "Demo",
                    "demo",
                    (ScriptSegment("hook", "one"),),
                    target_duration_seconds=80.0,
                ),
                assets={},
                shots=(),
                background_music=None,
                sfx_cues=(),
                overlay_cues=(),
                visual_fx_cues=(),
            )
            audio = AudioResult(
                root / "voice.wav",
                92.0,
                (WordTiming("one", 0.0, 92.0),),
                "test",
                True,
            )
            received_duration: list[float] = []

            def stop_after_duration(**kwargs):
                received_duration.append(kwargs["audio_duration"])
                raise StopAfterDuration("timeline recebeu a duracao real")

            stdout = io.StringIO()
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
                patch("engine.pipeline.build_timeline", side_effect=stop_after_duration),
                redirect_stdout(stdout),
            ):
                with self.assertRaises(StopAfterDuration):
                    await build_video(root, "demo")

        self.assertEqual(received_duration, [92.0])
        self.assertIn("[voz] aviso: Duracao da narracao fora do alvo", stdout.getvalue())
        self.assertIn("duracao=92.00s", stdout.getvalue())


class ProjectPathValidationTests(unittest.TestCase):
    def test_runtime_directories_cannot_overlap(self):
        data = {
            "paths": {
                "episodes_dir": "episodes",
                "work_dir": "episodes",
                "output_dir": "output",
                "cache_dir": "cache",
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "nao podem se sobrepor"):
                load_project_config(path)

    def test_non_finite_duration_tolerance_is_rejected(self):
        data = {"duration": {"target_tolerance_seconds": "nan"}}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "finito e nao negativo"):
                load_project_config(path)

if __name__ == "__main__":
    unittest.main()
