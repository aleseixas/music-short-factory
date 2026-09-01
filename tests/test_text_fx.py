import json
import tempfile
from types import SimpleNamespace
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from engine.config import load_project_config, load_style_config
from engine.captions import write_ass_captions
from engine.editorial import EditorialCatalogs
from engine.ffmpeg import probe_duration, probe_video_frame_count, run_ffmpeg
from engine.models import (
    AssetSpec,
    AudioResult,
    RelativeTextFxCue,
    ResolvedTextFxCue,
    ScriptSegment,
    ShotSpec,
    Story,
    TextFxCue,
    TimelinePlan,
    TimelineScene,
    WordTiming,
)
from engine.pipeline import build_video
from engine.renderer import Renderer
from engine.text_fx import write_text_fx_ass
from engine.timeline import build_timeline, load_timeline, resolve_text_fx_cues


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TextFxValidationTests(unittest.TestCase):
    def setUp(self):
        self.story = Story("Test", "test", (ScriptSegment("hook", "Texto."),))
        self.assets = {"photo": AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)}
        self.shots = [{"id": "shot", "segment": "hook", "asset": "photo", "motion": "hold", "transition_out": "cut"}]

    def _load(self, cues):
        data = {"schema_version": 1, "shots": self.shots, "text_fx_cues": cues}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timeline.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return load_timeline(path, self.story, self.assets)

    def test_all_animations_and_defaults_are_loaded(self):
        cues = [
            {"start_seconds": index, "end_seconds": index + 0.8, "text": "MUITO BOM", "animation": animation}
            for index, animation in enumerate(("pop_in", "scale_bounce", "slide_up", "fade_pop"))
        ]
        timeline = self._load(cues)
        self.assertEqual([cue.animation for cue in timeline.text_fx_cues], [cue["animation"] for cue in cues])
        self.assertTrue(all(cue.position == "center" and cue.intensity == 0.5 for cue in timeline.text_fx_cues))

    def test_legacy_absolute_and_relative_timing_are_loaded_without_ambiguity(self):
        absolute = self._load([
            {
                "start_seconds": 0.25,
                "end_seconds": 1.25,
                "text": "LEGADO",
                "animation": "pop_in",
            }
        ]).text_fx_cues[0]
        relative = self._load([
            {
                "segment": "hook",
                "offset_seconds": 0.4,
                "duration_seconds": 1.8,
                "text": "RELATIVO",
                "animation": "scale_bounce",
            }
        ]).text_fx_cues[0]

        self.assertIsInstance(absolute, TextFxCue)
        self.assertEqual((absolute.start_seconds, absolute.end_seconds), (0.25, 1.25))
        self.assertIsInstance(relative, RelativeTextFxCue)
        self.assertEqual(relative.segment_id, "hook")
        self.assertEqual(relative.offset_seconds, 0.4)
        self.assertEqual(relative.duration_seconds, 1.8)

        shot = ShotSpec("shot", "hook", "photo", "hold", "cut")
        plan = build_timeline(
            self.story,
            (shot,),
            self.assets,
            (WordTiming("Texto.", 0.0, 2.0),),
            2.0,
            10,
            0,
        )
        resolved_absolute = resolve_text_fx_cues((absolute,), plan)[0]
        self.assertEqual(
            (resolved_absolute.start_seconds, resolved_absolute.end_seconds),
            (0.25, 1.25),
        )

    def test_intensity_endpoints_and_invalid_values(self):
        timeline = self._load([
            {"start_seconds": 0, "end_seconds": 1, "text": "ZERO", "animation": "pop_in", "intensity": 0},
            {"start_seconds": 1, "end_seconds": 2, "text": "UM", "animation": "fade_pop", "intensity": 1},
        ])
        self.assertEqual([cue.intensity for cue in timeline.text_fx_cues], [0, 1])
        with self.assertRaisesRegex(RuntimeError, "entre 0 e 1"):
            self._load([{"start_seconds": 0, "end_seconds": 1, "text": "X", "animation": "pop_in", "intensity": 2}])

    def test_rejects_invalid_animation_overlap_and_bad_accent(self):
        with self.assertRaisesRegex(RuntimeError, "animation desconhecida"):
            self._load([{"start_seconds": 0, "end_seconds": 1, "text": "X", "animation": "shake"}])
        with self.assertRaisesRegex(RuntimeError, "sobrepostas"):
            self._load([
                {"start_seconds": 0, "end_seconds": 2, "text": "X", "animation": "pop_in"},
                {"start_seconds": 1, "end_seconds": 3, "text": "Y", "animation": "fade_pop"},
            ])
        with self.assertRaisesRegex(RuntimeError, "precisa aparecer"):
            self._load([{"start_seconds": 0, "end_seconds": 1, "text": "X", "accent_text": "Y", "animation": "pop_in"}])

    def test_relative_timing_rejects_mixed_partial_and_invalid_numbers(self):
        base = {
            "segment": "hook",
            "offset_seconds": 0.1,
            "duration_seconds": 0.8,
            "text": "X",
            "animation": "pop_in",
        }
        cases = (
            ({**base, "start_seconds": 0, "end_seconds": 1}, "mistura timing"),
            ({key: value for key, value in base.items() if key != "offset_seconds"}, "modo relativo"),
            ({**base, "offset_seconds": -0.1}, "maior ou igual a zero"),
            ({**base, "offset_seconds": True}, "numero finito"),
            ({**base, "offset_seconds": float("nan")}, "numero finito"),
            ({**base, "duration_seconds": 0}, "maior que zero"),
            ({**base, "duration_seconds": True}, "numero finito"),
            ({**base, "duration_seconds": float("inf")}, "numero finito"),
        )
        for cue, message in cases:
            with self.subTest(cue=cue), self.assertRaisesRegex(RuntimeError, message):
                self._load([cue])


class TextFxResolutionTests(unittest.TestCase):
    def setUp(self):
        self.story = Story(
            "Test",
            "test",
            (
                ScriptSegment("intro", "one"),
                ScriptSegment("chart", "two"),
            ),
        )
        self.asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        self.assets = {self.asset.id: self.asset}
        self.shots = (
            ShotSpec("shot_intro", "intro", "photo", "hold", "cut"),
            ShotSpec("shot_chart", "chart", "photo", "hold", "cut"),
        )
        self.cue = RelativeTextFxCue(
            "chart",
            0.4,
            1.8,
            "No. 1\nNO BRASIL",
            "scale_bounce",
            accent_text="No. 1",
        )

    def _plan(self, words, duration):
        return build_timeline(
            self.story,
            self.shots,
            self.assets,
            words,
            duration,
            10,
            0,
        )

    def test_relative_cue_follows_real_segment_start_when_tts_moves(self):
        first_plan = self._plan(
            (
                WordTiming("one", 0.0, 0.9),
                WordTiming("two", 1.3, 4.0),
            ),
            4.0,
        )
        second_plan = self._plan(
            (
                WordTiming("one", 0.0, 1.8),
                WordTiming("two", 2.6, 5.0),
            ),
            5.0,
        )

        first = resolve_text_fx_cues((self.cue,), first_plan)[0]
        second = resolve_text_fx_cues((self.cue,), second_plan)[0]

        self.assertIsInstance(first, ResolvedTextFxCue)
        self.assertAlmostEqual(
            first.start_seconds,
            first_plan.scenes[1].start_frame / first_plan.fps + 0.4,
        )
        self.assertAlmostEqual(first.end_seconds, first.start_seconds + 1.8)
        self.assertAlmostEqual(
            second.start_seconds - first.start_seconds,
            second_plan.scenes[1].start_frame / second_plan.fps
            - first_plan.scenes[1].start_frame / first_plan.fps,
        )

    def test_relative_anchor_and_segment_boundaries_fail_clearly(self):
        plan = self._plan(
            (
                WordTiming("one", 0.0, 0.9),
                WordTiming("two", 1.3, 4.0),
            ),
            4.0,
        )
        cases = (
            (
                RelativeTextFxCue("missing", 0.1, 0.5, "X", "pop_in"),
                plan,
                "segmento inexistente",
            ),
            (
                RelativeTextFxCue("chart", 3.0, 0.1, "X", "pop_in"),
                plan,
                "comeca fora",
            ),
            (
                RelativeTextFxCue("chart", 2.0, 1.0, "X", "pop_in"),
                plan,
                "ultrapassa o segmento",
            ),
        )
        for cue, target_plan, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                resolve_text_fx_cues((cue,), target_plan)

        chart_scene = plan.scenes[1]
        ambiguous_plan = replace(
            plan,
            scenes=(*plan.scenes, replace(chart_scene, index=3)),
        )
        with self.assertRaisesRegex(RuntimeError, "ancora ambigua"):
            resolve_text_fx_cues((self.cue,), ambiguous_plan)

    def test_overlap_is_checked_after_relative_timing_is_resolved(self):
        plan = self._plan(
            (
                WordTiming("one", 0.0, 0.9),
                WordTiming("two", 1.3, 4.0),
            ),
            4.0,
        )
        absolute = TextFxCue(1.2, 1.7, "ABSOLUTO", "pop_in")

        with self.assertRaisesRegex(RuntimeError, "sobrepostas"):
            resolve_text_fx_cues((absolute, self.cue), plan)


class TextFxAssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        cls.captions, cls.highlights = style.captions, style.highlights

    def test_multiline_accent_and_all_animations_write_safe_centered_ass(self):
        cues = tuple(
            ResolvedTextFxCue(index, index + 0.9, "NUNCA\nESQUEÃ‡A ISSO", animation, intensity=0.5, accent_text="ISSO")
            for index, animation in enumerate(("pop_in", "scale_bounce", "slide_up", "fade_pop"))
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text_fx.ass"
            write_text_fx_ass(cues, path, 720, 1280, self.captions, self.highlights, 4.0)
            content = path.read_text(encoding="utf-8-sig")
        self.assertEqual(content.count("Dialogue:"), 4)
        self.assertIn(r"\N", content)
        self.assertIn(r"\c&H3BD4FF&", content)
        self.assertIn(r"\pos(360,589)", content)
        self.assertIn(r"\move(360,", content)
        self.assertIn(r"\fad(", content)

    def test_cue_outside_video_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text_fx.ass"
            write_text_fx_ass((ResolvedTextFxCue(3, 4, "FORA", "pop_in"),), path, 720, 1280, self.captions, self.highlights, 2)
            content = path.read_text(encoding="utf-8-sig")
        self.assertNotIn("FORA", content)

    def test_resolved_relative_timing_is_written_as_absolute_ass_time(self):
        story = Story("Test", "test", (ScriptSegment("hook", "one"),))
        asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        shot = ShotSpec("shot", "hook", "photo", "hold", "cut")
        plan = build_timeline(
            story,
            (shot,),
            {asset.id: asset},
            (WordTiming("one", 0.0, 2.0),),
            2.0,
            10,
            0,
        )
        resolved = resolve_text_fx_cues(
            (RelativeTextFxCue("hook", 0.35, 1.2, "RESOLVIDO", "pop_in"),),
            plan,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text_fx.ass"
            write_text_fx_ass(
                resolved,
                path,
                720,
                1280,
                self.captions,
                self.highlights,
                plan.duration,
            )
            content = path.read_text(encoding="utf-8-sig")

        self.assertIn("Dialogue: 1,0:00:00.35,0:00:01.55", content)

    def test_relative_cue_boundaries_never_round_into_adjacent_segments(self):
        asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        first_shot = ShotSpec("shot_one", "one", "photo", "hold", "cut")
        second_shot = ShotSpec("shot_two", "two", "photo", "hold", "cut")
        plan = TimelinePlan(
            fps=30,
            total_frames=60,
            audio_duration=2.0,
            scenes=(
                TimelineScene(1, first_shot, asset, 0, 31, 31, 0),
                TimelineScene(2, second_shot, asset, 31, 60, 29, 0),
            ),
        )
        resolved = resolve_text_fx_cues(
            (
                RelativeTextFxCue(
                    "one",
                    0.5,
                    31 / 30 - 0.5,
                    "ATE O LIMITE",
                    "fade_pop",
                ),
                RelativeTextFxCue(
                    "two",
                    0,
                    0.2,
                    "DEPOIS DO LIMITE",
                    "pop_in",
                ),
            ),
            plan,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text_fx.ass"
            write_text_fx_ass(
                resolved,
                path,
                720,
                1280,
                self.captions,
                self.highlights,
                plan.duration,
            )
            content = path.read_text(encoding="utf-8-sig")

        self.assertIn("Dialogue: 1,0:00:00.50,0:00:01.03", content)
        self.assertIn("Dialogue: 1,0:00:01.04,0:00:01.23", content)
        self.assertNotIn("Dialogue: 1,0:00:01.03,0:00:01.23", content)

    def test_legacy_absolute_cue_is_still_accepted_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text_fx.ass"
            write_text_fx_ass(
                (TextFxCue(0.123, 0.876, "LEGADO", "pop_in"),),
                path,
                720,
                1280,
                self.captions,
                self.highlights,
                1.0,
            )
            content = path.read_text(encoding="utf-8-sig")

        self.assertIn("Dialogue: 1,0:00:00.12,0:00:00.88", content)


class TextFxPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_passes_resolved_cues_to_the_ass_writer(self):
        class StopAfterTextFx(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            episode_dir = root / "episodes" / "demo"
            episode_dir.mkdir(parents=True)
            asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
            story = Story(
                "Demo",
                "demo",
                (
                    ScriptSegment("intro", "one"),
                    ScriptSegment("chart", "two"),
                ),
                target_duration_seconds=4.0,
            )
            episode = SimpleNamespace(
                name="demo",
                directory=episode_dir,
                assets_dir=episode_dir / "assets",
                story=story,
                assets={asset.id: asset},
                shots=(
                    ShotSpec("shot_intro", "intro", "photo", "hold", "cut"),
                    ShotSpec("shot_chart", "chart", "photo", "hold", "cut"),
                ),
                background_music=None,
                sfx_cues=(),
                visual_fx_cues=(),
                text_fx_cues=(
                    RelativeTextFxCue(
                        "chart",
                        0.4,
                        1.8,
                        "No. 1\nNO BRASIL",
                        "scale_bounce",
                        accent_text="No. 1",
                    ),
                ),
                overlay_cues=(),
            )
            config = SimpleNamespace(
                paths=SimpleNamespace(
                    episodes_dir="episodes",
                    work_dir="work",
                    output_dir="output",
                    cache_dir="cache",
                ),
                duration=SimpleNamespace(target_tolerance_seconds=1.0),
                render=SimpleNamespace(
                    width=720,
                    height=1280,
                    working_scale=1,
                    fps=10,
                ),
                tts=object(),
            )
            style = SimpleNamespace(
                transitions=SimpleNamespace(crossfade_seconds=0.0),
                captions=object(),
                highlights=SimpleNamespace(default_duration=1.5),
            )
            audio = AudioResult(
                path=root / "voice.wav",
                duration=4.0,
                words=(
                    WordTiming("one", 0.0, 0.9),
                    WordTiming("two", 1.3, 4.0),
                ),
                source="test",
                exact_timings=True,
            )
            captured: list[ResolvedTextFxCue] = []

            def capture_resolved_cues(**kwargs):
                captured.extend(kwargs["cues"])
                raise StopAfterTextFx("resolved cues reached ASS writer")

            with (
                patch("engine.pipeline.load_project_config", return_value=config),
                patch("engine.pipeline.load_style_config", return_value=style),
                patch("engine.pipeline.load_episode", return_value=episode),
                patch("engine.pipeline.resolve_background_music", return_value=None),
                patch("engine.pipeline.resolve_sfx_cues", return_value=()),
                patch("engine.pipeline.preflight"),
                patch("engine.pipeline.AssetManager") as manager_class,
                patch(
                    "engine.pipeline.resolve_audio",
                    new=AsyncMock(return_value=audio),
                ),
                patch(
                    "engine.pipeline.load_editorial_catalogs",
                    return_value=EditorialCatalogs(),
                ),
                patch("engine.pipeline.write_timeline_plan"),
                patch("engine.pipeline.write_ass_captions"),
                patch(
                    "engine.pipeline.write_text_fx_ass",
                    side_effect=capture_resolved_cues,
                ),
            ):
                manager_class.return_value.preflight_video_scene.return_value = None
                with self.assertRaises(StopAfterTextFx):
                    await build_video(root, "demo")

        self.assertEqual(len(captured), 1)
        self.assertIsInstance(captured[0], ResolvedTextFxCue)
        self.assertAlmostEqual(captured[0].start_seconds, 1.5)
        self.assertAlmostEqual(captured[0].end_seconds, 3.3)


class TextFxCompositionTests(unittest.TestCase):
    def test_empty_text_fx_keeps_legacy_filter_and_text_fx_precedes_captions(self):
        config = load_project_config(PROJECT_ROOT / "config" / "config.json")
        config = replace(config, render=replace(config.render, width=90, height=160, fps=12))
        style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        shot = ShotSpec("shot", "hook", "photo", "hold", "cut")
        plan = build_timeline(Story("T", "t", (ScriptSegment("hook", "one"),)), (shot,), {"photo": asset}, (WordTiming("one", 0, 1),), 1, 12, 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"; work.mkdir()
            captions = work / "captions.ass"; captions.write_text("", encoding="utf-8")
            text_fx = work / "text_fx.ass"; text_fx.write_text("", encoding="utf-8")
            clip = root / "clip.mp4"; clip.write_bytes(b"x")
            renderer = Renderer(root, work, root / "output", config, style)
            with patch("engine.renderer.run_ffmpeg") as ffmpeg, patch("engine.renderer.probe_video_frame_count", return_value=12):
                renderer.compose_timeline([clip], plan, captions)
                legacy_args = ffmpeg.call_args.args[0]
                legacy_graph = legacy_args[legacy_args.index("-filter_complex") + 1]
                renderer.compose_timeline([clip], plan, captions, text_fx)
                text_args = ffmpeg.call_args.args[0]
                text_graph = text_args[text_args.index("-filter_complex") + 1]
        self.assertNotIn("text_fx.ass", legacy_graph)
        self.assertIn("text_fx.ass", text_graph)
        self.assertLess(text_graph.index("text_fx.ass"), text_graph.index("captions.ass"))

    def test_text_fx_render_keeps_final_resolution_frames_and_duration(self):
        config = load_project_config(PROJECT_ROOT / "config" / "config.json")
        config = replace(config, render=replace(config.render, width=90, height=160, fps=12, preset="ultrafast"))
        style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        asset = AssetSpec("photo", "photo.jpg", None, "", "", 0.5, 0.5)
        shot = ShotSpec("shot", "hook", "photo", "hold", "cut")
        plan = build_timeline(Story("T", "t", (ScriptSegment("hook", "one"),)), (shot,), {"photo": asset}, (WordTiming("one", 0, 1),), 1, 12, 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"; work.mkdir()
            clip = root / "clip.mp4"
            run_ffmpeg(["-y", "-hide_banner", "-f", "lavfi", "-i", "color=c=#202020:s=90x160:r=12", "-frames:v", 12, "-pix_fmt", "yuv420p", clip])
            captions = work / "captions.ass"
            write_ass_captions((WordTiming("one", 0, 1),), captions, 90, 160, style.captions)
            text_fx = work / "text_fx.ass"
            write_text_fx_ass((ResolvedTextFxCue(0.1, 0.9, "TEXTO", "scale_bounce"),), text_fx, 90, 160, style.captions, style.highlights, plan.duration)
            output = Renderer(root, work, root / "output", config, style).compose_timeline([clip], plan, captions, text_fx)
            self.assertEqual(probe_video_frame_count(output), 12)
            self.assertLess(abs(probe_duration(output) - 1), 0.05)
