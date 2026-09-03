import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import resolve_visual_candidates as resolver


class VisualCandidateResolutionTests(unittest.TestCase):
    def _write_episode(self, root: Path, slug: str, pool: dict | None) -> Path:
        episode = root / "episodes" / slug
        episode.mkdir(parents=True)
        (episode / "assets.json").write_text(
            json.dumps({"schema_version": 1, "assets": [{"id": "slot_a", "file": "old.jpg", "url": "https://upload.wikimedia.org/old.jpg", "credit": "old", "license": "CC0", "focus": {"x": 0.5, "y": 0.5}}]}),
            encoding="utf-8",
        )
        (episode / "timeline.json").write_text(
            json.dumps({"schema_version": 1, "shots": [{"id": "shot_a", "segment": "a", "asset": "slot_a", "motion": "hold", "transition_out": "cut"}]}),
            encoding="utf-8",
        )
        if pool is not None:
            (episode / "visual_candidates.json").write_text(json.dumps(pool), encoding="utf-8")
        return episode

    def test_missing_pool_is_backward_compatible(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_episode(root, "demo", None)
            self.assertEqual(resolver.resolve_episode(root, "demo"), 0)

    def test_prefilters_pool_and_selects_best_inspected_candidate(self):
        pool = {
            "schema_version": 1,
            "slots": [
                {
                    "id": "slot_a",
                    "required_seconds": 6.0,
                    "inspect_top": 2,
                    "candidates": [
                        {"name": "small", "kind": "image", "url": "https://upload.wikimedia.org/small.jpg", "file": "small.jpg", "license": "CC0", "width": 400, "height": 600, "editorial_rank": 1},
                        {"name": "good", "kind": "video", "url": "https://upload.wikimedia.org/good.webm", "file": "good.webm", "license": "CC0", "width": 1080, "height": 1920, "duration_seconds": 20, "source_start_seconds": 1, "source_end_seconds": 12, "editorial_rank": 2},
                        {"name": "best", "kind": "video", "url": "https://upload.wikimedia.org/best.webm", "file": "best.webm", "license": "CC0", "width": 1440, "height": 2560, "duration_seconds": 30, "source_start_seconds": 2, "source_end_seconds": 14, "editorial_rank": 3},
                    ],
                }
            ],
        }

        def fake_inspect(_root, result, **_kwargs):
            score = 91.0 if result.name == "best" else 78.0
            return SimpleNamespace(
                visual_score=score,
                width=result.width or 720,
                height=result.height or 1280,
                duration_seconds=result.duration_seconds,
                fps=30.0 if result.kind == "video" else None,
                opening_motion_score=80.0 if result.kind == "video" else None,
                motion_score=75.0 if result.kind == "video" else None,
                is_practically_static=False if result.kind == "video" else None,
                trim=SimpleNamespace(safe_for_shot=True) if result.kind == "video" else None,
            )

        with TemporaryDirectory() as tmp, patch.object(resolver, "inspect_visual_result", side_effect=fake_inspect) as inspect:
            root = Path(tmp)
            episode = self._write_episode(root, "demo", pool)
            self.assertEqual(resolver.resolve_episode(root, "demo"), 0)
            self.assertEqual(inspect.call_count, 2)

            assets = json.loads((episode / "assets.json").read_text(encoding="utf-8"))
            chosen = next(asset for asset in assets["assets"] if asset["id"] == "slot_a")
            self.assertEqual(chosen["file"], "best.webm")

            timeline = json.loads((episode / "timeline.json").read_text(encoding="utf-8"))
            self.assertEqual(timeline["shots"][0]["source_start_seconds"], 2.0)
            self.assertEqual(timeline["shots"][0]["source_end_seconds"], 14.0)

            report = json.loads((episode / "visual_resolution_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["selections"]["slot_a"]["visual_score"], 91.0)


if __name__ == "__main__":
    unittest.main()
