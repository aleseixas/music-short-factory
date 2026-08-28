import json
import tempfile
import unittest
from pathlib import Path

from engine.audio import validate_audio_duration
from engine.config import load_project_config
from engine.models import AssetSpec, ScriptSegment, Story
from engine.timeline import load_shots


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


class DurationValidationTests(unittest.TestCase):
    def test_duration_at_target_and_tolerance_boundaries_is_valid(self):
        for duration in (60.0, 75.0, 90.0):
            with self.subTest(duration=duration):
                validate_audio_duration(duration, 75.0, 15.0)

    def test_duration_outside_tolerance_is_rejected(self):
        for duration in (59.99, 90.01):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(RuntimeError, "fora do alvo"):
                    validate_audio_duration(duration, 75.0, 15.0)

    def test_non_positive_or_non_finite_duration_is_rejected(self):
        for duration in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(RuntimeError, "Duracao de audio invalida"):
                    validate_audio_duration(duration, 75.0, 15.0)


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

if __name__ == "__main__":
    unittest.main()
