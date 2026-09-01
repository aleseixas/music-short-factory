import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from engine.config import load_project_config, load_style_config
from engine.captions import write_ass_captions
from engine.ffmpeg import probe_duration, probe_video_frame_count, run_ffmpeg
from engine.models import AssetSpec, ScriptSegment, ShotSpec, Story, TextFxCue, TimelineScene, WordTiming
from engine.renderer import Renderer
from engine.text_fx import write_text_fx_ass
from engine.timeline import build_timeline, load_timeline


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


class TextFxAssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        style = load_style_config(PROJECT_ROOT / "config" / "style.json")
        cls.captions, cls.highlights = style.captions, style.highlights

    def test_multiline_accent_and_all_animations_write_safe_centered_ass(self):
        cues = tuple(
            TextFxCue(index, index + 0.9, "NUNCA\nESQUEÃ‡A ISSO", animation, intensity=0.5, accent_text="ISSO")
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
            write_text_fx_ass((TextFxCue(3, 4, "FORA", "pop_in"),), path, 720, 1280, self.captions, self.highlights, 2)
            content = path.read_text(encoding="utf-8-sig")
        self.assertNotIn("FORA", content)


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
            write_text_fx_ass((TextFxCue(0.1, 0.9, "TEXTO", "scale_bounce"),), text_fx, 90, 160, style.captions, style.highlights, plan.duration)
            output = Renderer(root, work, root / "output", config, style).compose_timeline([clip], plan, captions, text_fx)
            self.assertEqual(probe_video_frame_count(output), 12)
            self.assertLess(abs(probe_duration(output) - 1), 0.05)
