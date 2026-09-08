from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from engine.models import (
    AssetSpec,
    Episode,
    ResolvedFreezeFrame,
    ShotSpec,
    Story,
    TimelinePlan,
    TimelineScene,
)
from engine import visual_usage
from engine.visual_repetition import VisualFingerprint, VisualHistoryEntry
from scripts import persist_visual_usage as persistence


NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
RECORDED_AT = "2026-09-08T12:00:00Z"
SECRET_URL = "https://example.invalid/private.jpg?token=fixture-secret"


def _payload(recorded_at: str = RECORDED_AT) -> dict:
    entry = VisualHistoryEntry(
        episode="current",
        shot_id="shot_main",
        asset_id="main",
        recorded_at=recorded_at,
        fingerprint=VisualFingerprint(
            kind="image",
            sha256="a" * 64,
            url_hashes=("b" * 64,),
            perceptual_hashes=("c" * 16,),
        ),
    )
    return {
        "schema_version": 1,
        "episode": "current",
        "recorded_at": recorded_at,
        "visual_usage": [entry.as_dict()],
    }


def _write_usage(root: Path, payload: dict) -> Path:
    target = root / "episodes" / "current" / "visual_usage.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _episode_and_plan(
    root: Path, *, video: bool = False, url: str | None = SECRET_URL
) -> tuple[Episode, TimelinePlan, Path]:
    episode_dir = root / "episodes" / "current"
    episode_dir.mkdir(parents=True)
    asset = AssetSpec("main", "main.mp4" if video else "main.png", url, "", "", 0.5, 0.5)
    shot = ShotSpec(
        "shot_main", "segment", asset.id, "hold", "crossfade" if video else "cut",
        source_start_seconds=10.0 if video else 0.0,
        source_end_seconds=90.0 if video else None,
        speed=1.5 if video else 1.0,
    )
    scene = TimelineScene(
        0, shot, asset, 0, 120, 135 if video else 120, 15 if video else 0,
        freeze_frame=ResolvedFreezeFrame(30, 16) if video else None,
    )
    plan = TimelinePlan(30, 120, 4.0, (scene,))
    episode = Episode("current", episode_dir, Story("Fixture", "current", ()), {asset.id: asset}, (shot,))
    source = root / asset.file
    if video:
        source.write_bytes(b"local video fixture")
    else:
        image = Image.new("RGB", (64, 96), (90, 140, 190))
        image.paste((230, 210, 170), (8, 12, 42, 56))
        image.paste((20, 35, 70), (22, 50, 60, 91))
        image.save(source)
    return episode, plan, source


class VisualUsageTests(unittest.TestCase):
    def test_real_local_image_records_hashes_without_urls_or_network(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode, plan, source = _episode_and_plan(root)
            with (
                patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")) as connect,
                patch("engine.assets.AssetManager.ensure", side_effect=AssertionError("Resolution forbidden")) as ensure,
            ):
                destination = visual_usage.record_visual_usage(
                    root, episode, plan, {"main": source}, now=lambda: NOW
                )
            connect.assert_not_called()
            ensure.assert_not_called()
            serialized = destination.read_text(encoding="utf-8")
            payload = json.loads(serialized)
            entries = visual_usage.validate_visual_usage_payload(payload, "current")
            self.assertEqual(destination, episode.directory / "visual_usage.json")
            self.assertEqual(payload["recorded_at"], RECORDED_AT)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].fingerprint.sha256, hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertTrue(entries[0].fingerprint.perceptual_hashes)
            expected_url_hashes = VisualFingerprint.from_dict({"kind": "image", "urls": [SECRET_URL]}).url_hashes
            self.assertEqual(entries[0].fingerprint.url_hashes, expected_url_hashes)
            self.assertNotIn("https://", serialized)
            self.assertNotIn("fixture-secret", serialized)
            self.assertNotIn(str(source), serialized)
            self.assertFalse(destination.with_name(".visual_usage.json.tmp").exists())

    def test_video_uses_real_render_window_with_speed_freeze_and_crossfade(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode, plan, source = _episode_and_plan(root, video=True)

            def build(path, kind, **kwargs):
                return VisualFingerprint.from_dict({
                    "kind": kind,
                    "sha256": "a" * 64,
                    "video_frame_hashes": ["c" * 16],
                    **kwargs,
                })

            with patch.object(visual_usage, "build_visual_fingerprint", side_effect=build) as fingerprint:
                destination = visual_usage.record_visual_usage(root, episode, plan, {"main": source}, now=lambda: NOW)
            call = fingerprint.call_args
            self.assertEqual(call.args, (source.resolve(), "video"))
            self.assertEqual(call.kwargs["source_start_seconds"], 10.0)
            # 120 logical frames + 15 crossfade - 15 added freeze frames,
            # divided by 30 fps and played at 1.5x consumes six source seconds.
            self.assertEqual(plan.scenes[0].required_source_duration(plan.fps), 6.0)
            self.assertEqual(call.kwargs["source_duration_seconds"], 6.0)
            entry = json.loads(destination.read_text(encoding="utf-8"))["visual_usage"][0]
            self.assertEqual(entry["fingerprint"]["source_duration_seconds"], 6.0)
            self.assertNotEqual(entry["fingerprint"]["source_duration_seconds"], 80.0)

    def test_repeated_source_ranges_share_fingerprint_but_keep_each_shot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode, plan, source = _episode_and_plan(root, video=True)
            first = plan.scenes[0]
            second = replace(first, index=1, shot=replace(first.shot, id="shot_second"))
            third = replace(first, index=2, shot=replace(first.shot, id="shot_third", source_start_seconds=20.0))
            plan = replace(plan, scenes=(first, second, third))
            with patch.object(visual_usage, "build_visual_fingerprint", return_value=VisualFingerprint(kind="video", sha256="a" * 64)) as build:
                target = visual_usage.record_visual_usage(root, episode, plan, {"main": source})
            entries = json.loads(target.read_text(encoding="utf-8"))["visual_usage"]
            self.assertEqual(build.call_count, 2)
            self.assertEqual([entry["shot_id"] for entry in entries], ["shot_main", "shot_second", "shot_third"])

    def test_fingerprint_failure_keeps_url_only_usage_and_hides_exception_details(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode, plan, source = _episode_and_plan(root)
            with patch.object(visual_usage, "build_visual_fingerprint", side_effect=RuntimeError(SECRET_URL)):
                target = visual_usage.record_visual_usage(root, episode, plan, {"main": source})
            serialized = target.read_text(encoding="utf-8")
            payload = json.loads(serialized)
            self.assertEqual(len(payload["visual_usage"]), 1)
            self.assertIsNone(payload["visual_usage"][0]["fingerprint"]["sha256"])
            self.assertTrue(payload["visual_usage"][0]["fingerprint"]["url_hashes"])
            self.assertTrue(payload["warnings"])
            self.assertNotIn("fixture-secret", serialized)
            self.assertNotIn("https://", serialized)

    def test_missing_local_source_does_not_download_and_does_not_block_other_shots(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            episode, plan, source = _episode_and_plan(root, url=None)
            missing_asset = replace(plan.scenes[0].asset, id="missing", file="missing.png")
            missing_scene = replace(plan.scenes[0], index=1, asset=missing_asset, shot=replace(plan.scenes[0].shot, id="shot_missing", asset_id="missing"))
            plan = replace(plan, scenes=(*plan.scenes, missing_scene))
            with patch("engine.assets.AssetManager.ensure", side_effect=AssertionError("Resolution forbidden")) as ensure:
                target = visual_usage.record_visual_usage(root, episode, plan, {"main": source})
            payload = json.loads(target.read_text(encoding="utf-8"))
            ensure.assert_not_called()
            self.assertEqual([entry["shot_id"] for entry in payload["visual_usage"]], ["shot_main"])
            self.assertTrue(payload["warnings"])

    def test_report_url_provenance_requires_same_source_shot_asset_and_kind(self):
        for mismatch in (None, "sha256", "missing_sha256", "shot_id", "asset_id", "kind", "episode"):
            with self.subTest(mismatch=mismatch), TemporaryDirectory() as directory:
                root = Path(directory)
                episode, plan, source = _episode_and_plan(root, url=None)
                sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
                report = _payload()
                entry = report["visual_usage"][0]
                entry["fingerprint"]["sha256"] = sha256
                entry["fingerprint"]["source_duration_seconds"] = 99.0
                if mismatch in ("shot_id", "asset_id", "episode"):
                    entry[mismatch] = "unrelated"
                elif mismatch == "sha256":
                    entry["fingerprint"]["sha256"] = "f" * 64
                elif mismatch == "missing_sha256":
                    entry["fingerprint"]["sha256"] = None
                elif mismatch == "kind":
                    entry["fingerprint"]["kind"] = "video"
                (episode.directory / "visual_resolution_report.json").write_text(json.dumps(report), encoding="utf-8")
                target = visual_usage.record_visual_usage(root, episode, plan, {"main": source})
                fingerprint = json.loads(target.read_text(encoding="utf-8"))["visual_usage"][0]["fingerprint"]
                self.assertEqual(fingerprint["url_hashes"], ["b" * 64] if mismatch is None else [])
                self.assertEqual(fingerprint["sha256"], sha256)
                self.assertIsNone(fingerprint["source_duration_seconds"])

    def test_corrupt_resolution_report_does_not_block_post_render_record(self):
        for report in ("{broken", "[]", '{"visual_usage": [null]}'):
            with self.subTest(report=report), TemporaryDirectory() as directory:
                root = Path(directory)
                episode, plan, source = _episode_and_plan(root)
                (episode.directory / "visual_resolution_report.json").write_text(report, encoding="utf-8")
                target = visual_usage.record_visual_usage(root, episode, plan, {"main": source})
                payload = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(len(payload["visual_usage"]), 1)
                self.assertEqual(payload["visual_usage"][0]["fingerprint"]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_wrapper_failure_is_advisory_and_does_not_print_secrets(self):
        output = io.StringIO()
        with patch.object(visual_usage, "record_visual_usage", side_effect=OSError(SECRET_URL)), redirect_stdout(output):
            result = visual_usage.record_visual_usage_safely(Path("unused"), None, None, {})
        self.assertIsNone(result)
        self.assertIn("OSError", output.getvalue())
        self.assertIn("continua valido", output.getvalue())
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertNotIn("https://", output.getvalue())

    def test_loader_canonicalizes_timezone_and_discards_unknown_fields(self):
        raw = _payload("2026-09-08T09:00:00-03:00")
        raw["remote_url"] = SECRET_URL
        raw["warnings"] = [SECRET_URL]
        raw["visual_usage"][0]["path"] = "C:/private/credentials.json"
        raw["visual_usage"][0]["fingerprint"]["download_url"] = SECRET_URL
        with TemporaryDirectory() as directory:
            path = _write_usage(Path(directory), raw)
            result = visual_usage.load_and_validate_visual_usage(path, "current")
        self.assertEqual(set(result), {"schema_version", "episode", "recorded_at", "visual_usage"})
        self.assertEqual(result["recorded_at"], RECORDED_AT)
        self.assertEqual(set(result["visual_usage"][0]), set(_payload()["visual_usage"][0]))
        self.assertEqual(set(result["visual_usage"][0]["fingerprint"]), set(_payload()["visual_usage"][0]["fingerprint"]))
        self.assertNotIn("fixture-secret", json.dumps(result))
        self.assertNotIn("credentials.json", json.dumps(result))

    def test_validator_rejects_wrong_episode_invalid_hash_and_naive_timestamp(self):
        invalid_payloads = []
        for key, value in (("episode", "../escape"), ("recorded_at", "2026-09-08T12:00:00"), ("schema_version", 99), ("visual_usage", {})):
            payload = _payload()
            payload[key] = value
            invalid_payloads.append(payload)
        payload = _payload()
        payload["visual_usage"][0]["episode"] = "another"
        invalid_payloads.append(payload)
        payload = _payload()
        payload["visual_usage"][0]["fingerprint"]["sha256"] = SECRET_URL
        invalid_payloads.append(payload)
        for key in ("shot_id", "asset_id"):
            payload = _payload()
            payload["visual_usage"][0][key] = SECRET_URL
            invalid_payloads.append(payload)
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                visual_usage.validate_visual_usage_payload(payload, "current")


@unittest.skipUnless(shutil.which("git"), "Git is required for local persistence tests")
class VisualUsagePersistenceTests(unittest.TestCase):
    """All fetches and pushes target a fresh local bare repository, never a service."""

    def setUp(self):
        temporary = TemporaryDirectory(prefix="msf-usage-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.remote = self.root / "origin.git"
        self.seed = self.root / "seed"
        self.caller = self.root / "caller"
        self._git(self.root, "init", "--bare", "--initial-branch=main", self.remote)
        self._git(self.root, "init", "--initial-branch=main", self.seed)
        self._configure(self.seed)
        (self.seed / "episodes" / "current").mkdir(parents=True)
        (self.seed / "episodes" / "current" / "story.md").write_text("Episode fixture\n", encoding="utf-8")
        (self.seed / "README.md").write_text("Initial fixture\n", encoding="utf-8")
        (self.seed / ".gitignore").write_text("*.mp4\n", encoding="utf-8")
        self._git(self.seed, "add", ".")
        self._git(self.seed, "commit", "-m", "Initial fixture")
        self._git(self.seed, "remote", "add", "origin", self.remote)
        self._git(self.seed, "push", "-u", "origin", "main")
        self._git(self.root, "clone", "--branch", "main", self.remote, self.caller)
        self._configure(self.caller)
        self._git(self.caller, "checkout", "-b", "render-run")

    def _git(self, root: Path, *arguments: object) -> str:
        root.resolve().relative_to(self.root)
        result = subprocess.run(
            ["git", "-C", str(root), *map(str, arguments)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        return result.stdout.strip()

    def _configure(self, root: Path):
        self._git(root, "config", "user.name", "Local Test")
        self._git(root, "config", "user.email", "test@example.invalid")
        self._git(root, "config", "core.autocrlf", "false")

    def _remote_head(self) -> str:
        return self._git(self.remote, "rev-parse", "refs/heads/main")

    def _advance_remote(self, filename="concurrent.txt", content="Concurrent update\n") -> str:
        (self.seed / filename).write_text(content, encoding="utf-8")
        self._git(self.seed, "add", "--", filename)
        self._git(self.seed, "commit", "-m", "Advance remote")
        self._git(self.seed, "push", "origin", "main")
        return self._remote_head()

    def test_persists_only_canonical_sidecar_on_latest_main_and_preserves_dirty_caller(self):
        (self.caller / "private-local.txt").write_text("Only on render branch\n", encoding="utf-8")
        self._git(self.caller, "add", "private-local.txt")
        self._git(self.caller, "commit", "-m", "Caller-only work")
        original_head = self._git(self.caller, "rev-parse", "HEAD")
        (self.caller / "README.md").write_text("Unstaged caller edit\n", encoding="utf-8")
        (self.caller / "staged.txt").write_text("Staged caller edit\n", encoding="utf-8")
        self._git(self.caller, "add", "staged.txt")
        (self.caller / "local-token.txt").write_text("fixture-secret", encoding="utf-8")
        (self.caller / "render.mp4").write_bytes(b"render must survive")
        payload = _payload()
        payload["remote_url"] = SECRET_URL
        payload["visual_usage"][0]["download_url"] = SECRET_URL
        payload["visual_usage"][0]["fingerprint"]["path"] = "C:/private/cache"
        source = _write_usage(self.caller, payload)
        original_source = source.read_bytes()
        original_status = self._git(self.caller, "status", "--porcelain", "--untracked-files=all")
        original_index = self._git(self.caller, "diff", "--cached")
        original_worktrees = self._git(self.caller, "worktree", "list", "--porcelain")
        advanced_head = self._advance_remote()

        with patch.object(persistence, "_git_result", wraps=persistence._git_result) as commands:
            self.assertTrue(persistence.persist_visual_usage(self.caller, "current"))

        committed = self._git(self.remote, "show", "main:episodes/current/visual_usage.json")
        self.assertEqual(json.loads(committed), _payload())
        self.assertNotIn("fixture-secret", committed)
        self.assertNotIn("C:/private/cache", committed)
        self.assertEqual(self._git(self.remote, "rev-parse", "main^"), advanced_head)
        self.assertEqual(self._git(self.remote, "diff-tree", "--no-commit-id", "--name-only", "-r", "main"), "episodes/current/visual_usage.json")
        self.assertNotIn("private-local.txt", self._git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines())
        self.assertEqual(self._git(self.caller, "rev-parse", "HEAD"), original_head)
        self.assertEqual(self._git(self.caller, "branch", "--show-current"), "render-run")
        self.assertEqual(self._git(self.caller, "status", "--porcelain", "--untracked-files=all"), original_status)
        self.assertEqual(self._git(self.caller, "diff", "--cached"), original_index)
        self.assertEqual(self._git(self.caller, "worktree", "list", "--porcelain"), original_worktrees)
        self.assertEqual(source.read_bytes(), original_source)
        self.assertEqual((self.caller / "README.md").read_text(encoding="utf-8"), "Unstaged caller edit\n")
        self.assertEqual((self.caller / "render.mp4").read_bytes(), b"render must survive")
        push_calls = [call.args[1:] for call in commands.call_args_list if call.args[1] == "push"]
        self.assertEqual(push_calls, [("push", "--quiet", "origin", "HEAD:refs/heads/main")])

    def test_equal_or_newer_remote_history_is_not_overwritten(self):
        for timestamp in (RECORDED_AT, "2026-09-09T12:00:00Z"):
            with self.subTest(recorded_at=timestamp):
                remote_payload = _payload(timestamp)
                remote_payload["visual_usage"][0]["fingerprint"]["sha256"] = "d" * 64
                _write_usage(self.seed, remote_payload)
                self._git(self.seed, "add", "episodes/current/visual_usage.json")
                self._git(self.seed, "commit", "-m", "Remote visual usage")
                self._git(self.seed, "push", "origin", "main")
                before = self._remote_head()
                _write_usage(self.caller, _payload())
                self.assertTrue(persistence.persist_visual_usage(self.caller, "current"))
                self.assertEqual(self._remote_head(), before)
                self.assertEqual(json.loads(self._git(self.remote, "show", "main:episodes/current/visual_usage.json")), remote_payload)

    def test_non_fast_forward_retries_from_new_remote_head_with_normal_push(self):
        _write_usage(self.caller, _payload())
        original = persistence._git_result
        pushes = []
        concurrent_head = []

        def race(root, *arguments):
            if arguments and arguments[0] == "push":
                pushes.append(arguments)
                if len(pushes) == 1:
                    concurrent_head.append(self._advance_remote("raced.txt", "Preserve concurrent commit\n"))
            return original(root, *arguments)

        with patch.object(persistence, "_git_result", side_effect=race), patch.object(persistence.time, "sleep"):
            self.assertTrue(persistence.persist_visual_usage(self.caller, "current", attempts=2))
        self.assertEqual(pushes, [("push", "--quiet", "origin", "HEAD:refs/heads/main")] * 2)
        self.assertEqual(self._git(self.remote, "rev-parse", "main^"), concurrent_head[0])
        self.assertEqual(self._git(self.remote, "show", "main:raced.txt"), "Preserve concurrent commit")
        self.assertEqual(self._git(self.remote, "diff-tree", "--no-commit-id", "--name-only", "-r", "main"), "episodes/current/visual_usage.json")

    def test_main_returns_zero_for_invalid_payload_without_changing_remote(self):
        payload = deepcopy(_payload())
        payload["visual_usage"][0]["fingerprint"]["sha256"] = SECRET_URL
        _write_usage(self.caller, payload)
        before = self._remote_head()
        output = io.StringIO()
        with redirect_stdout(output):
            code = persistence.main(["current"], project_root=self.caller)
        self.assertEqual(code, 0)
        self.assertEqual(self._remote_head(), before)
        self.assertIn("::warning::", output.getvalue())
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertNotIn("https://", output.getvalue())

    def test_main_suppresses_git_stderr_and_remote_credentials_on_fetch_error(self):
        _write_usage(self.caller, _payload())
        original = persistence._git_result

        def fail_fetch(root, *arguments):
            if arguments and arguments[0] == "fetch":
                return subprocess.CompletedProcess(["git", "fetch"], 128, "", "fatal: " + SECRET_URL)
            return original(root, *arguments)

        output = io.StringIO()
        before = self._remote_head()
        with patch.object(persistence, "_git_result", side_effect=fail_fetch), redirect_stdout(output):
            code = persistence.main(["current"], project_root=self.caller)
        self.assertEqual(code, 0)
        self.assertEqual(self._remote_head(), before)
        self.assertIn("VisualUsagePersistenceError", output.getvalue())
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertNotIn("https://", output.getvalue())


if __name__ == "__main__":
    unittest.main()
