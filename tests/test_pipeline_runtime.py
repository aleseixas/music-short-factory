from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from engine.pipeline_runtime import RunIdentity, prepare_request, trigger_plan, verify_request_commit, wait_for_run
from engine.pipeline_state import PipelineError, PipelineStore, atomic_json

ROOT = Path(__file__).resolve().parents[1]


class PipelineRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        shutil.copyfile(ROOT / "config/pipeline-contract.json", self.root / "config/pipeline-contract.json")
        (self.root / ".github/workflows").mkdir(parents=True)
        shutil.copyfile(ROOT / ".github/workflows/episode-media-preflight.yml", self.root / ".github/workflows/episode-media-preflight.yml")
        self.store = PipelineStore(self.root)

    def author(self, slug="episode", request="req_1"):
        self.store.start(slug, request)
        self.store.transition(slug, "UNIQUE")
        self.store.transition(slug, "AUTHORING")
        directory = self.root / "episodes" / slug
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / "assets.json", {"broken": True})

    def test_media_pass_cannot_rotate_duplicate_request_without_admission(self):
        from scripts.pipeline_workflow import media_pass
        self.store.start("episode", "duplicate_1")
        self.store.transition("episode", "UNIQUE")
        with self.assertRaisesRegex(PipelineError, "PREFLIGHT_ADMISSION_REQUIRED"):
            media_pass(self.root, "episode", "media_2")
        self.assertEqual(self.store.status("episode")["request_id"], "duplicate_1")

    def test_external_failure_updates_own_state_without_rolling_back_queue(self):
        from scripts.pipeline_workflow import media_failed
        self.author()
        self.store.transition("episode", "LOCAL_VALIDATION")
        self.store.transition("episode", "MEDIA_PREFLIGHT", local_preflight_passed=True, run_id="123")
        media_failed(self.root, "episode", "another_request", "123")
        self.assertEqual(self.store.status("episode")["stage"], "MEDIA_PREFLIGHT")
        media_failed(self.root, "episode", "req_1", "456")
        self.assertEqual(self.store.status("episode")["stage"], "MEDIA_PREFLIGHT")
        media_failed(self.root, "episode", "req_1", "123")
        self.assertEqual(self.store.status("episode")["stage"], "VALIDATION_FAILED")
        self.store.transition("episode", "LOCAL_VALIDATION")
        self.store.transition("episode", "MEDIA_PREFLIGHT", local_preflight_passed=True)
        self.store.transition("episode", "READY_TO_QUEUE")
        self.store.transition("episode", "QUEUED")
        media_failed(self.root, "episode", "req_1", "123")
        self.assertEqual(self.store.status("episode")["stage"], "QUEUED")

    def test_push_without_dispatch_is_ready(self):
        plan = trigger_plan(self.root, "media")
        self.assertEqual(plan["action"], "commit_and_push_request")
        self.assertFalse(plan["dispatch_required"])
        self.assertIn("workflow_dispatch", plan["events"])
        self.assertTrue(plan["actions_dispatch_required"])
        self.assertEqual(plan["actions_handoff"], "workflow_dispatch")

    def test_contract_drift_stops_before_trigger(self):
        (self.root / ".github/workflows/episode-media-preflight.yml").write_text("on:\n  workflow_dispatch:\n", encoding="utf-8")
        with self.assertRaisesRegex(PipelineError, "WORKFLOW_CONTRACT_DRIFT"):
            trigger_plan(self.root, "media")

    def test_repair_multiple_errors_before_request_exists(self):
        self.author()
        checks = []
        errors = [{"error_code": "VISUAL_ASSET_HTTP_404", "recoverable": True},
                  {"error_code": "BACKGROUND_MUSIC_INVALID", "recoverable": True}]
        def validate(root, slug, **kwargs):
            self.assertFalse((root / ".episode-check/episode--req_1.json").exists())
            checks.append(True)
            return errors if len(checks) == 1 else []
        batches = []
        def repair(root, slug, all_errors):
            batches.append(all_errors)
            atomic_json(root / "episodes/episode/assets.json", {"repaired": True})
        result = prepare_request(self.root, "episode", "req_1", validator=validate, repairer=repair)
        self.assertEqual(len(checks), 2)
        self.assertEqual(batches, [errors])
        self.assertTrue((self.root / result["request_path"]).is_file())
        self.assertEqual(result["stage"], "MEDIA_PREFLIGHT")

    def test_prepare_can_pass_after_five_material_repairs(self):
        self.author()
        checks, repairs = [], []

        def validate(root, slug, **kwargs):
            self.assertFalse((root / ".episode-check/episode--req_1.json").exists())
            checks.append(True)
            return [{"error_code": "BAD_ASSET", "recoverable": True}] if len(repairs) < 5 else []

        def repair(root, slug, errors):
            repairs.append(errors)
            atomic_json(root / "episodes/episode/assets.json", {"repair": len(repairs)})

        result = prepare_request(self.root, "episode", "req_1", validator=validate, repairer=repair)
        self.assertEqual(len(repairs), 5)
        self.assertEqual(len(checks), 6)
        self.assertEqual(result["stage"], "MEDIA_PREFLIGHT")
        self.assertTrue((self.root / result["request_path"]).is_file())

    def test_prepare_stops_after_five_repairs_when_validation_still_fails(self):
        self.author()
        checks, repairs = [], []

        def validate(root, slug, **kwargs):
            checks.append(True)
            return [{"error_code": "BAD_ASSET", "recoverable": True}]

        def repair(root, slug, errors):
            repairs.append(errors)
            atomic_json(root / "episodes/episode/assets.json", {"repair": len(repairs)})

        result = prepare_request(self.root, "episode", "req_1", validator=validate, repairer=repair)
        self.assertEqual(len(repairs), 5)
        self.assertEqual(len(checks), 6)
        self.assertEqual(result["stage"], "VALIDATION_FAILED")
        self.assertFalse((self.root / ".episode-check").exists())

    def test_deterministic_failure_no_retry_without_change(self):
        self.author()
        calls = []
        def validate(*args, **kwargs):
            calls.append(True)
            return [{"error_code": "BAD_ASSET", "recoverable": True}]
        result = prepare_request(self.root, "episode", "req_1", validator=validate, repairer=lambda *args: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["stage"], "VALIDATION_FAILED")
        self.assertFalse((self.root / ".episode-check").exists())

    def test_normal_status_never_lists_thousands_of_old_episodes(self):
        self.author()
        with patch.object(self.store, "_indexed_paths", side_effect=AssertionError("no scan")), patch.object(Path, "iterdir", side_effect=AssertionError("no listing")):
            self.assertEqual(self.store.status()["slug"], "episode")
            self.assertFalse(self.store.status()["can_create_new_episode"])

    def test_reconciliation_with_5000_archived_episodes_resumes_real_active(self):
        self.author()
        self.store.index_path.unlink()
        paths = [f"episodes/old_{number}/story.json" for number in range(5000)] + ["episodes/episode/story.json"]
        load = self.store._load_episode
        with patch.object(self.store, "_indexed_paths", return_value=iter(paths)), patch.object(self.store, "_load_episode", side_effect=lambda slug: {"active": False} if slug.startswith("old_") else load(slug)):
            result = self.store.status()
        self.assertEqual(result["slug"], "episode")
        self.assertEqual(result["stage"], "AUTHORING")

    def test_missing_index_reconciles_complete_index_ignoring_directory_listing(self):
        self.author()
        self.store.index_path.write_text("truncated", encoding="utf-8")
        with patch.object(self.store, "_indexed_paths", return_value=iter(["episodes/episode/story.json"])), patch.object(Path, "iterdir", side_effect=AssertionError("truncated Contents listing must never matter")):
            result = self.store.reconcile()
        self.assertEqual(result["slug"], "episode")
        self.assertEqual(result["stage"], "AUTHORING")

    def test_legacy_queue_boundary_ignores_archived_samples_and_keeps_untracked_active(self):
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout
        git("init", "-q")
        atomic_json(self.root / "episodes/archived/story.json", {"slug": "archived"})
        git("add", "episodes")
        git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "old sample")
        queue = self.root / ".publish-queue/previous.txt"
        queue.parent.mkdir()
        queue.write_text("previous\n17\n", encoding="utf-8")
        git("add", ".publish-queue")
        git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "queue boundary")
        atomic_json(self.root / "episodes/current/story.json", {"slug": "current"})
        result = self.store.status()
        self.assertEqual(result["slug"], "current")
        self.assertNotIn("conflicts", result)

    def test_true_active_is_resumed_and_new_slug_rejected(self):
        self.author()
        self.assertEqual(self.store.start("episode", "req_1")["stage"], "AUTHORING")
        with self.assertRaisesRegex(PipelineError, "ACTIVE_EPISODE_EXISTS"):
            self.store.start("another", "req_2")

    def test_independent_nostalgia_channel(self):
        self.author()
        self.author("nostalgia_other", "req_2")
        self.assertEqual(self.store.status()["slug"], "episode")
        self.assertEqual(self.store.status(channel="nostalgia")["slug"], "nostalgia_other")

    def test_attempt_marker_always_freezes_corrupt_state(self):
        self.author()
        marker = self.root / ".publication-attempts/episode.json"
        marker.parent.mkdir()
        marker.write_text("interrupted-json", encoding="utf-8")
        self.store.episode_path("episode").write_text("invalid-json", encoding="utf-8")
        state = self.store.status("episode")
        self.assertFalse(state["republication_allowed"])
        self.assertFalse(state["recovery_mutation_allowed"])
        self.assertEqual(state["EVER_PUBLISHED_OR_ATTEMPTED"], "YES")
        with self.assertRaisesRegex(PipelineError, "PUBLICATION_ALREADY_ATTEMPTED"):
            self.store.assert_mutation_allowed("episode")

    def test_legacy_queue_is_not_proof_of_safe_publication(self):
        path = self.root / ".publish-queue/legacy.txt"
        path.parent.mkdir()
        path.write_text("legacy\n123\n", encoding="utf-8")
        self.assertFalse(self.store.status("legacy")["mutation_allowed"])

    def test_no_transition_can_clear_attempt(self):
        self.author()
        self.store.mark_publisher_started("episode")
        with self.assertRaisesRegex(PipelineError, "PUBLICATION_ALREADY_ATTEMPTED"):
            self.store.transition("episode", "AUTHORING")
        self.store.mark_published("episode")
        self.assertFalse(self.store.status("episode")["republication_allowed"])

    def test_media_stage_requires_local_pass(self):
        self.author()
        self.store.transition("episode", "LOCAL_VALIDATION")
        with self.assertRaisesRegex(PipelineError, "LOCAL_PASS_REQUIRED"):
            self.store.transition("episode", "MEDIA_PREFLIGHT")

    def test_missing_request_after_interrupted_write_is_recoverable(self):
        self.author()
        prepared = prepare_request(self.root, "episode", "req_1", validator=lambda *args, **kwargs: [])
        (self.root / prepared["request_path"]).unlink()
        self.assertEqual(self.store.status("episode")["stage"], "VALIDATION_FAILED")
        restored = prepare_request(self.root, "episode", "req_1", validator=lambda *args, **kwargs: [])
        self.assertTrue((self.root / restored["request_path"]).is_file())

    def test_report_only_change_never_triggers_another_validation(self):
        self.author()
        calls = []
        def validate(*args, **kwargs):
            calls.append(True)
            return [{"code": "VISUAL_ASSET_INVALID", "recoverable": True}]
        def repair(root, slug, errors):
            atomic_json(root / "episodes/episode/visual_auto_repair_report.json", {"changed": True})
        prepare_request(self.root, "episode", "req_1", validator=validate, repairer=repair)
        self.assertEqual(len(calls), 1)

    def test_finished_publisher_run_reconciles_without_unlocking(self):
        self.author()
        atomic_json(self.root / ".publication-attempts/episode.json", {"slug": "episode", "run_id": "42", "run_attempt": "1", "stage": "PUBLISHING"})
        result = self.store.reconcile("episode", observe_run=lambda run_id: {"id": 42, "run_attempt": 1, "status": "completed", "conclusion": "failure"})
        self.assertEqual(result["stage"], "PUBLISH_UNCERTAIN")
        self.assertFalse(self.store.status("episode")["republication_allowed"])
        self.assertEqual(self.store.status("episode")["stage"], "PUBLISH_UNCERTAIN")

    def test_local_default_calls_the_same_external_validator(self):
        self.author()
        with patch("check_episode_media_batch.validate_episode_media", return_value=[]) as validate:
            result = prepare_request(self.root, "episode", "req_1")
        validate.assert_called_once()
        self.assertEqual(result["stage"], "MEDIA_PREFLIGHT")

    def test_corrupt_episode_state_rebuilt_from_exact_local_pass_receipt(self):
        self.author()
        result = prepare_request(self.root, "episode", "req_1", validator=lambda *args, **kwargs: [])
        self.store.episode_path("episode").write_text("corrupt", encoding="utf-8")
        with patch.object(self.store, "_indexed_paths", side_effect=lambda: iter([result["request_path"], "episodes/episode/story.json"])):
            rebuilt = self.store.reconcile("episode")
        self.assertEqual(rebuilt["stage"], "MEDIA_PREFLIGHT")
        self.assertEqual(rebuilt["request_id"], "req_1")
        self.assertFalse(rebuilt["reconciliation_required"])

    def test_delayed_run_and_all_pending_statuses_are_polled(self):
        identity = RunIdentity("req", "episode", "a" * 40, "episode-media-preflight.yml")
        statuses = iter([None, "queued", "pending", "waiting", "requested", "in_progress", "completed"])
        now = [0.0]
        def fetch(identity, page):
            status = next(statuses)
            return {"workflow_runs": [] if status is None else [dict(id=7, head_sha=identity.commit_sha, head_branch="main", path=".github/workflows/" + identity.workflow, event="push", status=status, conclusion="success")]}
        def sleep(seconds):
            now[0] += seconds
        result = wait_for_run(identity, fetch, monotonic=lambda: now[0], sleep=sleep)
        self.assertEqual(result["id"], 7)
        self.assertGreater(now[0], 0)

    def test_two_requests_track_own_runs_even_when_latest_differs(self):
        first = RunIdentity("one", "first", "a" * 40, "episode-media-preflight.yml")
        second = RunIdentity("two", "second", "b" * 40, first.workflow)
        runs = [dict(id=n, head_sha=identity.commit_sha, head_branch="main", path=".github/workflows/" + identity.workflow,
                     event="push", status="completed", conclusion="success", request_id=identity.request_id, slug=identity.slug)
                for n, identity in [(11, first), (22, second)]]
        fetch = lambda identity, page: {"workflow_runs": list(reversed(runs))}
        self.assertEqual(wait_for_run(first, fetch)["id"], 11)
        self.assertEqual(wait_for_run(second, fetch)["id"], 22)

    def test_dispatch_wait_uses_prepared_sha_in_title_not_later_head_sha(self):
        identity = RunIdentity("req", "episode", "a" * 40, "episode-media-preflight.yml",
                               "workflow_dispatch", run_id="7")
        run = dict(id=7, head_sha="b" * 40, head_branch="main",
                   display_title="media/episode/req/" + "a" * 40,
                   path=".github/workflows/episode-media-preflight.yml",
                   event="workflow_dispatch", status="completed", conclusion="success")
        assert wait_for_run(identity, lambda *_: {"workflow_runs": [run]})["id"] == 7

    def test_paginated_runs_not_truncated(self):
        identity = RunIdentity("req", "episode", "a" * 40, "episode-media-preflight.yml")
        run = dict(id=9, head_sha=identity.commit_sha, head_branch="main", path=".github/workflows/" + identity.workflow, event="push", status="completed", conclusion="success")
        result = wait_for_run(identity, lambda identity, page: {"workflow_runs": [] if page == 1 else [run], "has_next": page == 1})
        self.assertEqual(result["id"], 9)

    def test_timeout_reports_pending_without_redispatch(self):
        identity = RunIdentity("req", "episode", "a" * 40, "episode-media-preflight.yml")
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        with self.assertRaisesRegex(PipelineError, "RUN_PENDING") as caught:
            wait_for_run(identity, lambda *args: {"workflow_runs": []}, timeout=3, sleep=sleep, monotonic=lambda: now[0])
        self.assertTrue(caught.exception.recoverable)

    def test_request_must_match_exact_commit_not_latest(self):
        self.author()
        prepared = prepare_request(self.root, "episode", "req_1", validator=lambda *args, **kwargs: [])
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        git("add", prepared["request_path"])
        git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "request")
        sha = git("rev-parse", "HEAD")
        identity = RunIdentity("req_1", "episode", sha, "episode-media-preflight.yml")
        verify_request_commit(self.root, identity, prepared["request_path"])
        with self.assertRaisesRegex(PipelineError, "REQUEST_COMMIT_MISMATCH"):
            verify_request_commit(self.root, RunIdentity("other", "episode", sha, identity.workflow), prepared["request_path"])


if __name__ == "__main__":
    unittest.main()
