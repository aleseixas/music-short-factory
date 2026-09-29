from __future__ import annotations

from contextlib import ExitStack, nullcontext, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine.assets import load_asset_catalog
from engine.models import ScriptSegment, Story
from engine.timeline import load_timeline
from engine.visual_search import TrimAssessment, VisualInspection
from scripts import repair_media_preflight_batch as batch
from scripts import repair_visual_asset as visual


class MediaRepairScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.episode = self.root / "episodes/demo"
        self.episode.mkdir(parents=True)
        self.output = io.StringIO()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(self.output))
        # Exercise real selection, scoring, reference resolution and writes.
        # Only the network/technical inspection boundary is replaced by a
        # deterministic accepted-media fixture; no remote download can occur.
        self.resolver_fixture = self.stack.enter_context(patch.object(visual, "_web_resolver_context", side_effect=lambda slug: nullcontext()))
        self.stack.enter_context(patch.object(visual, "load_visual_history", return_value=((), ())))
        self.stack.enter_context(patch.object(visual.legacy, "inspect_visual_result", side_effect=self.inspection))
        self.stack.enter_context(patch("requests.get", side_effect=AssertionError("unexpected network access")))

    @staticmethod
    def candidate(name, *, kind="video"):
        extension = "mp4" if kind == "video" else "jpg"
        return {"name": name, "kind": kind, "file": f"{name}.{extension}",
                "url": f"https://upload.wikimedia.org/{name}.{extension}"}

    @staticmethod
    def inspection(root, result, **kwargs):
        video = result.kind == "video"
        return VisualInspection(
            result=result, path=Path(result.suggested_file), width=720, height=1280,
            aspect_ratio=0.5625, duration_seconds=20.0 if video else None,
            fps=30.0 if video else None, motion=None,
            trim=TrimAssessment(True, 0.0, 20.0, 20.0, 8.0, 12.0, "safe") if video else None,
            visual_score=80.0, score_breakdown={"fixture": 80.0},
        )

    def write_fixture(self, ids=("a",), *, missing=()):
        assets = [{"id": name, "file": f"old_{name}.mp4", "url": f"https://upload.wikimedia.org/old_{name}.mp4"}
                  for name in ids if name not in missing]
        shots = [{"id": f"shot_{name}", "segment": name, "asset": name,
                  "motion": "hold", "transition_out": "cut"} for name in ids]
        pool = [{"id": f"shot_{name}", "required_seconds": 6.0,
                 "candidates": [self.candidate(f"fresh_{name}")]} for name in ids]
        for filename, payload in (("assets.json", {"assets": assets}), ("timeline.json", {"shots": shots}),
                                  ("visual_candidates.json", {"slots": pool})):
            self.write(filename, {"schema_version": 1, **payload})

    def write(self, filename, payload):
        (self.episode / filename).write_text(json.dumps(payload), encoding="utf-8")

    def read(self, filename):
        return json.loads((self.episode / filename).read_text(encoding="utf-8"))

    def repair(self, code="VISUAL_ASSET_HTTP_404", scope="asset:a", detail="HTTP 404"):
        log = batch._legacy_log({"code": code, "scope": scope, "detail": detail, "recoverable": True}, "demo")
        try:
            return visual.repair(self.root, "demo", log)
        finally:
            log.unlink(missing_ok=True)

    def run_batch(self, errors):
        diagnostic = self.root / "diagnostic.json"
        diagnostic.write_text(json.dumps({"errors": errors}), encoding="utf-8")
        scopes = []
        def execute(command):
            log = Path(command[-1])
            scopes.append(visual._diagnostic_values(log)["MEDIA_PREFLIGHT_SCOPE"])
            return visual.repair(self.root, "demo", log) == 0
        with patch.object(batch, "PROJECT_ROOT", self.root), patch.object(batch, "_run", side_effect=execute), \
                patch("sys.argv", ["repair", "demo", "--diagnostic-log", str(diagnostic)]):
            result = batch.main()
        return result, scopes

    def test_asset_error_resolves_pool_keyed_by_referencing_shot(self):
        self.write_fixture()
        original_timeline = self.read("timeline.json")
        self.assertEqual(self.repair(), 0)
        asset = self.read("assets.json")["assets"][0]
        self.assertEqual(asset["id"], "a")
        self.assertEqual(asset["file"], "fresh_a.mp4")
        self.assertEqual(asset["url"], self.candidate("fresh_a")["url"])
        self.assertEqual(self.read("timeline.json")["shots"][0]["asset"], original_timeline["shots"][0]["asset"])
        self.assertEqual(self.read("visual_candidates.json")["slots"][0]["id"], "shot_a")

    def test_aggregate_missing_reference_is_repaired_once_using_exact_shot_scope(self):
        self.write_fixture(missing=("a",))
        detail = "Asset desconhecido no plano 'shot_a': 'a'"
        errors = [{"code": "SHOT_REFERENCES_MISSING_ASSET", "scope": scope, "detail": detail,
                   "target": "episodes/demo/timeline.json", "recoverable": True}
                  for scope in ("episode-load", "timeline.json", "shot:shot_a", "shot:shot_a")]
        result, scopes = self.run_batch(errors)
        self.assertEqual(result, 0)
        self.assertEqual(scopes, ["shot:shot_a"])
        assets = load_asset_catalog(self.episode / "assets.json")
        timeline = load_timeline(self.episode / "timeline.json", Story("Demo", "demo", (ScriptSegment("a", "Text"),)), assets)
        self.assertEqual(timeline.shots[0].asset_id, "a")
        self.assertEqual(assets["a"].file, "fresh_a.mp4")

    def test_same_http_detail_for_two_assets_repairs_both_in_one_batch(self):
        self.write_fixture(("a", "b"))
        errors = [{"code": "VISUAL_ASSET_HTTP_404", "scope": f"asset:{name}",
                   "detail": "HTTP 404", "target": "episodes/demo/assets.json", "recoverable": True}
                  for name in ("a", "b")]
        result, scopes = self.run_batch(errors)
        self.assertEqual(result, 0)
        self.assertEqual(scopes, ["asset:a", "asset:b"])
        self.assertEqual({asset["file"] for asset in self.read("assets.json")["assets"]}, {"fresh_a.mp4", "fresh_b.mp4"})

    def test_targeted_repair_rejects_source_already_used_by_another_shot(self):
        self.write_fixture(("a", "b"))
        pool = self.read("visual_candidates.json")
        pool["slots"][1]["candidates"].insert(0, self.candidate("old_a"))
        self.write("visual_candidates.json", pool)
        self.assertEqual(self.repair(scope="asset:b"), 0)
        assets = {asset["id"]: asset for asset in self.read("assets.json")["assets"]}
        self.assertEqual(assets["a"]["file"], "old_a.mp4")
        self.assertEqual(assets["b"]["file"], "fresh_b.mp4")
        self.assertIn("eligible=false", self.output.getvalue())

    def test_opening_repair_never_falls_back_to_image(self):
        self.write_fixture()
        pool = self.read("visual_candidates.json")
        pool["slots"][0]["candidates"] = [self.candidate("photo", kind="image")]
        self.write("visual_candidates.json", pool)
        before = (self.episode / "assets.json").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Nenhum candidato visual acessivel"):
            self.repair()
        self.assertEqual((self.episode / "assets.json").read_bytes(), before)

    def test_duplicate_shot_repair_splits_shared_asset_without_changing_first_shot(self):
        self.write_fixture(("a", "b"))
        timeline = self.read("timeline.json")
        timeline["shots"][1]["asset"] = "a"
        self.write("timeline.json", timeline)
        self.assertEqual(self.repair(code="INTRA_EPISODE_VISUAL_REUSE", scope="shot:shot_b"), 0)
        updated = self.read("timeline.json")
        self.assertEqual(updated["shots"][0]["asset"], "a")
        self.assertEqual(updated["shots"][1]["asset"], "shot_b_visual")
        assets = {asset["id"]: asset for asset in self.read("assets.json")["assets"]}
        self.assertEqual(assets["a"]["file"], "old_a.mp4")
        self.assertEqual(assets["shot_b_visual"]["file"], "fresh_b.mp4")

    def test_aggregate_only_error_is_retained_for_legacy_repair(self):
        error = {"code": "VISUAL_ASSET_INVALID", "scope": "episode-load", "detail": "asset broken"}
        self.assertEqual(batch._independent_errors([error, dict(error)]), [error])


class ResolverIsolationTests(unittest.TestCase):
    def test_strict_web_hooks_are_restored_after_failed_and_repeated_repairs(self):
        import resolve_visual_candidates_web as web

        original_inspection = web._inspect_candidate
        original_score = web._score_record
        original_legacy_inspection = visual.legacy._inspect_candidate
        original_episode = web.CURRENT_EPISODE
        original_cache = dict(web.SELECTED_VIDEO_SEGMENTS)

        # Bypass only downloader/provider setup. The authenticated semantic
        # wrapper remains the real implementation used during the repair.
        def install_hooks():
            visual.legacy._inspect_candidate = web._inspect_candidate

        for _attempt in range(2):
            with patch.object(web, "_install_patches", side_effect=install_hooks), redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "semantic_video"):
                    with visual._web_resolver_context("demo"):
                        import resolve_visual_candidates_web_auth as auth
                        self.assertIs(web._inspect_candidate, auth._inspect_candidate_with_safe_video_fallback)
                        self.assertEqual(web.CURRENT_EPISODE, "demo")
                        web.SELECTED_VIDEO_SEGMENTS["fixture"] = [(0.0, 2.0)]
                        with patch.object(auth, "_original_resolver_inspect_candidate", return_value=(
                            SimpleNamespace(kind="video"), SimpleNamespace(is_practically_static=False), [],
                        )):
                            web._inspect_candidate(Path("."), {"id": "shot", "candidates": [{"semantic_fit": "direct"}]},
                                                   {"semantic_fit": "generic"}, 1)
            self.assertIs(web._inspect_candidate, original_inspection)
            self.assertIs(web._score_record, original_score)
            self.assertIs(visual.legacy._inspect_candidate, original_legacy_inspection)
            self.assertEqual(web.CURRENT_EPISODE, original_episode)
            self.assertEqual(web.SELECTED_VIDEO_SEGMENTS, original_cache)


if __name__ == "__main__":
    unittest.main()
