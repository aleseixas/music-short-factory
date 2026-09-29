from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import check_episode_media
import check_episode_media_batch as batch
from engine.ffmpeg import VideoStreamInfo, run_ffmpeg
from engine.assets import AssetManager
from engine.models import AssetSpec, Episode, ShotSpec, Story
from engine.visual_candidates import validate_visual_candidate_pool, validate_visual_candidate_coverage
from scripts import repair_media_preflight_batch as repairs

ROOT = Path(__file__).resolve().parents[1]


class LocalMediaGateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "config").mkdir()
        shutil.copyfile(ROOT / "config/config.json", self.root / "config/config.json")
        self.directory = self.root / "episodes/demo"
        self.directory.mkdir(parents=True)
        assets = {name: AssetSpec(name, f"{name}.mp4", f"https://example.test/{name}.mp4", "", "", 0.5, 0.5)
                  for name in ("a", "b")}
        shots = tuple(ShotSpec(name, "segment", name, "hold", "cut") for name in assets)
        self.episode = Episode("demo", self.directory, Story("Demo", "demo", ()), assets, shots)
        pool = {"schema_version": 1, "slots": [{"id": name, "candidates": [{"kind": "video", "url": asset.url}]} for name, asset in assets.items()]}
        (self.directory / "visual_candidates.json").write_text(json.dumps(pool), encoding="utf-8")

    def test_single_entry_point_calls_shared_batch_validator(self):
        with patch.object(batch, "main", return_value=17) as shared:
            self.assertEqual(check_episode_media.main(), 17)
        shared.assert_called_once_with()

    def test_collects_independent_asset_and_background_errors(self):
        manager = Mock()
        manager.preflight_video_scene.return_value = VideoStreamInfo(20.0, 720, 1280, 30.0)
        manager.ensure.side_effect = [RuntimeError("HTTP 403 a.mp4"), RuntimeError("HTTP 404 b.mp4")]
        with patch.object(batch, "load_episode", return_value=self.episode), patch.object(batch, "AssetManager", return_value=manager), patch.object(batch, "resolve_background_music", side_effect=RuntimeError("background music invalid")):
            errors = batch.validate_episode_media(self.root, "demo", request_id="req", commit_sha="abc")
        self.assertEqual(manager.ensure.call_count, 2)
        self.assertGreaterEqual(len(errors), 3)
        self.assertEqual({item["scope"] for item in errors}, {"asset:a", "asset:b", "background-music-resolve"})
        for item in errors:
            self.assertEqual(item["request_id"], "req")
            self.assertEqual(item["commit_sha"], "abc")
            self.assertTrue({"error_code", "error_class", "recoverable", "target", "detail", "stage", "slug"} <= item.keys())

    def test_renderer_source_bounds_checked_locally(self):
        manager = Mock()
        manager.preflight_video_scene.return_value = VideoStreamInfo(20.0, 720, 1280, 30.0)
        manager.preflight_video_scene.side_effect = [RuntimeError("source_end_seconds ultrapassa a duracao do video"), VideoStreamInfo(20.0, 720, 1280, 30.0)]
        with patch.object(batch, "load_episode", return_value=self.episode), patch.object(batch, "AssetManager", return_value=manager), patch.object(batch, "resolve_background_music", return_value=None):
            errors = batch.validate_episode_media(self.root, "demo")
        self.assertEqual(manager.preflight_video_scene.call_count, 2)
        self.assertIn("VIDEO_SOURCE_WINDOW_INVALID", {item["error_code"] for item in errors})

    def test_authored_duration_rejects_short_source_before_external_request(self):
        manager = Mock()
        manager.preflight_video_scene.return_value = VideoStreamInfo(0.5, 720, 1280, 30.0)
        with patch.object(batch, "load_episode", return_value=self.episode), patch.object(batch, "AssetManager", return_value=manager), patch.object(batch, "resolve_background_music", return_value=None):
            errors = batch.validate_episode_media(self.root, "demo")
        self.assertEqual([error["error_code"] for error in errors], ["VIDEO_SOURCE_WINDOW_INVALID"] * 2)
        manager.preflight_video_decode.assert_not_called()

    def test_real_decode_accepts_video_and_rejects_corrupt_content(self):
        source = self.directory / "clip.mp4"
        run_ffmpeg(["-y", "-f", "lavfi", "-i", "color=c=blue:s=32x32:d=0.5", "-c:v", "libx264", source])
        manager = AssetManager(self.directory, self.root / "work", 720, 1280, 1)
        asset = self.episode.assets["a"]
        with patch.object(manager, "ensure", return_value=source):
            manager.preflight_video_decode(asset, 0, 0.4)
            source.write_bytes(b"corrupt-video")
            with self.assertRaisesRegex(RuntimeError, "decodificacao falhou"):
                manager.preflight_video_decode(asset, 0, 0.4)

    def test_invalid_opening_does_not_make_second_shot_the_opening(self):
        assets = {**self.episode.assets, "b": replace(self.episode.assets["b"], file="b.jpg")}
        partial = replace(self.episode, assets=assets, shots=self.episode.shots[1:])
        (self.directory / "timeline.json").write_text(json.dumps({"shots": [{"id": "a", "asset": "a"}, {"id": "b", "asset": "b"}]}))
        errors = batch._collect_visual_authoring_errors(partial, "demo")
        self.assertNotIn("FIRST_EDITORIAL_VISUAL_NOT_VIDEO", {error["code"] for error in errors})

    def test_decoder_success_without_frames_is_not_accepted(self):
        manager = AssetManager(self.directory, self.root / "work", 720, 1280, 1)
        with patch.object(manager, "ensure", return_value=self.directory / "clip.mp4"), patch("engine.assets.run_ffmpeg_capture", return_value="frame=0\nprogress=end\n"):
            with self.assertRaisesRegex(RuntimeError, "nenhum frame"):
                manager.preflight_video_decode(self.episode.assets["a"], 0, 1)

    def test_partial_diagnostics_can_never_turn_original_failure_into_pass(self):
        manager = Mock()
        manager.preflight_video_scene.return_value = VideoStreamInfo(20.0, 720, 1280, 30.0)
        with patch.object(batch, "load_episode", side_effect=RuntimeError("bad timeline")), patch.object(batch, "collect_partial_episode", return_value=(self.episode, [])), patch.object(batch, "AssetManager", return_value=manager), patch.object(batch, "resolve_background_music", return_value=None):
            errors = batch.validate_episode_media(self.root, "demo")
        self.assertTrue(any(item["scope"] == "episode-load" for item in errors))
        self.assertEqual(manager.ensure.call_count, 2)

    def test_pool_requires_distinct_sources_and_video_opening(self):
        pool = {"schema_version": 1, "slots": [{"id": name, "candidates": [{"kind": "video", "url": "https://example.test/same.mp4"}]} for name in ("a", "b")]}
        pool = validate_visual_candidate_pool(pool)
        with self.assertRaisesRegex(ValueError, "insuficiente"):
            validate_visual_candidate_coverage(pool, [("a", "a"), ("b", "b")])

    def test_nonrecoverable_error_does_not_suppress_independent_batch_repairs(self):
        errors = [{"code": "VISUAL_ASSET_HTTP_404", "recoverable": True, "scope": "asset:a", "detail": "a.mp4"},
                  {"code": "BACKGROUND_MUSIC_INVALID", "recoverable": True},
                  {"code": "UNCLASSIFIED_ENGINE_ERROR", "recoverable": False}]
        log = self.root / "diagnostic.log"
        log.write_text("MEDIA_PREFLIGHT_ERRORS_JSON=" + json.dumps(errors), encoding="utf-8")
        output = io.StringIO()
        with patch.object(repairs, "PROJECT_ROOT", self.root), patch.object(repairs, "_run", return_value=True) as run, patch("sys.argv", ["repair", "demo", "--diagnostic-log", str(log)]), redirect_stdout(output):
            result = repairs.main()
        self.assertEqual(result, 2)
        self.assertEqual(run.call_count, 2)
        self.assertIn("BATCH_AUTO_REPAIR_HANDLED=2", output.getvalue())


if __name__ == "__main__":
    unittest.main()
