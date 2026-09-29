from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import Mock, patch

import requests
from engine.pipeline_state import PipelineError, PipelineStore
from publishing.attempts import AttemptStore, PublicationLocked
from scripts.pipeline_workflow import duplicate_guard


class PublicationAttemptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "GITHUB_REPOSITORY": "", "GH_TOKEN": "", "GITHUB_TOKEN": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        store = PipelineStore(self.root)
        store.start("demo", "request")
        for stage in ("UNIQUE", "AUTHORING", "LOCAL_VALIDATION"):
            store.transition("demo", stage)
        store.transition("demo", "MEDIA_PREFLIGHT", local_preflight_passed=True)
        store.transition("demo", "READY_TO_QUEUE")
        store.transition("demo", "QUEUED")

    def test_unvalidated_episode_cannot_start_any_live_publisher(self):
        with self.assertRaisesRegex(PublicationLocked, "PROVENANCE_REQUIRED"):
            AttemptStore(self.root, allow_local=True).claim("unvalidated", "youtube", "one")

    def test_each_platform_once_same_session_and_other_sessions_forbidden(self):
        store = AttemptStore(self.root, allow_local=True)
        store.claim("demo", "youtube", "one")
        store.claim("demo", "instagram", "one")
        with self.assertRaises(PublicationLocked):
            store.claim("demo", "youtube", "one")
        with self.assertRaises(PublicationLocked):
            store.claim("demo", "tiktok", "another_run")
        state = PipelineStore(self.root).status("demo")
        self.assertEqual(state["stage"], "PUBLISHING")
        self.assertFalse(state["mutation_allowed"])
        self.assertTrue(state["active"])

    def test_success_is_terminal_and_still_forbids_republication(self):
        store = AttemptStore(self.root, allow_local=True)
        store.claim("demo", "youtube", "one")
        from publishing.base import PublishResult
        store.mark_sending("demo", "youtube", "one")
        store.record_receipt("demo", "youtube", "one", PublishResult("youtube", "uploaded", "confirmed-video"))
        store.complete("demo", "one", success=True)
        state = PipelineStore(self.root).status("demo")
        self.assertEqual(state["stage"], "PUBLISHED")
        self.assertTrue(state["can_create_new_episode"])
        self.assertFalse(state["republication_allowed"])
        with self.assertRaises(PublicationLocked):
            store.assert_unstarted("demo")

    def test_unknown_failure_is_terminal_but_irreversible(self):
        store = AttemptStore(self.root, allow_local=True)
        store.claim("demo", "youtube", "one")
        store.complete("demo", "one", success=False)
        self.assertEqual(PipelineStore(self.root).status("demo")["stage"], "PUBLISH_UNCERTAIN")
        with self.assertRaises(PublicationLocked):
            store.claim("demo", "youtube", "two")

    def test_invalid_marker_is_never_overwritten(self):
        marker = self.root / ".publication-attempts/demo.json"
        marker.parent.mkdir()
        marker.write_text("broken", encoding="utf-8")
        with self.assertRaises(PublicationLocked):
            AttemptStore(self.root, allow_local=True).claim("demo", "youtube", "one")
        self.assertEqual(marker.read_text(), "broken")

    def github_origin(self, origin="git@github.com:owner/repo.git"):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=self.root, check=True)

    def test_production_claim_without_canonical_repository_fails_closed(self):
        with self.assertRaisesRegex(PublicationLocked, "DURABLE_STORE_UNAVAILABLE"):
            AttemptStore(self.root).claim("demo", "youtube", "copied_checkout")
        self.assertFalse((self.root / ".publication-attempts/demo.json").exists())

    def test_local_github_origin_uses_existing_remote_marker_without_actions(self):
        self.github_origin()
        http = Mock()
        existing = {"slug": "demo", "session_id": "github:123:1", "stage": "PUBLISHED",
                    "platforms": {"youtube": {"status": "attempted"}}}
        response = Mock(status_code=200)
        response.json.return_value = {"sha": "remote-sha", "content": base64.b64encode(json.dumps(existing).encode()).decode()}
        http.request.return_value = response
        with patch.dict(os.environ, {"GH_TOKEN": "test-shared-token"}):
            store = AttemptStore(self.root, http)
            self.assertTrue(store.remote)
            with self.assertRaisesRegex(PublicationLocked, "ALREADY_ATTEMPTED"):
                store.claim("demo", "youtube", "local-fresh-process")
        http.request.assert_called_once()
        self.assertEqual(http.request.call_args.args[:2], (
            "GET", "https://api.github.com/repos/owner/repo/contents/.publication-attempts/demo.json"))
        self.assertTrue((self.root / ".publication-attempts/demo.json").is_file())
        self.assertFalse(PipelineStore(self.root).status("demo")["mutation_allowed"])

    def test_local_shared_claim_can_use_gh_auth_token(self):
        self.github_origin("https://github.com/owner/repo.git")
        http = Mock()
        http.request.side_effect = [Mock(status_code=404), Mock(status_code=201)]
        store = AttemptStore(self.root, http)
        store._verify_live_preflight = Mock()
        with patch("publishing.attempts.subprocess.run", return_value=Mock(returncode=0, stdout="test-auth-token\n")) as run:
            store.claim("demo", "youtube", "local-once")
        run.assert_called_once_with(["gh", "auth", "token", "--hostname", "github.com"],
                                    cwd=self.root, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(http.request.call_count, 2)
        self.assertEqual(http.request.call_args.kwargs["headers"]["Authorization"], "Bearer test-auth-token")
        self.assertTrue((self.root / ".publication-attempts/demo.json").is_file())

    def test_missing_shared_credentials_never_falls_back_to_local_claim(self):
        self.github_origin()
        http = Mock()
        store = AttemptStore(self.root, http)
        with patch("publishing.attempts.subprocess.run", side_effect=FileNotFoundError("gh")):
            with self.assertRaisesRegex(PublicationLocked, "DURABLE_STORE_UNAVAILABLE"):
                store.claim("demo", "youtube", "one")
        http.request.assert_not_called()
        self.assertFalse((self.root / ".publication-attempts/demo.json").exists())

    def test_connected_mutation_checks_central_claim_even_outside_actions(self):
        self.github_origin()
        with patch("publishing.attempts.AttemptStore.assert_remote_unstarted", side_effect=PublicationLocked("PUBLICATION_ALREADY_ATTEMPTED")) as check:
            with self.assertRaisesRegex(PipelineError, "PUBLICATION_SAFETY_UNVERIFIED"):
                PipelineStore(self.root).assert_mutation_allowed("new_demo")
        check.assert_called_once_with("new_demo")

    def test_read_only_status_stays_offline(self):
        self.github_origin()
        with patch("publishing.attempts.AttemptStore", side_effect=AssertionError("status must stay offline")):
            self.assertEqual(PipelineStore(self.root).status("demo")["stage"], "QUEUED")

    def test_configured_repository_must_match_local_origin(self):
        self.github_origin()
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/repo"}):
            with self.assertRaisesRegex(PublicationLocked, "REPOSITORY_MISMATCH"):
                AttemptStore(self.root).claim("demo", "youtube", "one")

    def test_mutation_cannot_bypass_remote_lock_with_mismatched_repository(self):
        self.github_origin()
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/repo"}), patch("requests.Session.request") as request:
            with self.assertRaisesRegex(PipelineError, "PUBLICATION_SAFETY_UNVERIFIED.*REPOSITORY_MISMATCH"):
                PipelineStore(self.root).assert_mutation_allowed("new_demo")
        request.assert_not_called()

    def test_mutation_cannot_treat_invalid_origin_as_offline_permission(self):
        self.github_origin("https://example.invalid/owner/repo.git")
        with patch("requests.Session.request") as request:
            with self.assertRaisesRegex(PipelineError, "PUBLICATION_SAFETY_UNVERIFIED.*GITHUB_ORIGIN_REQUIRED"):
                PipelineStore(self.root).assert_mutation_allowed("new_demo")
        request.assert_not_called()

    def test_legacy_continuity_resume_persists_index_before_workflow_git_add(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            story = root / "episodes/legacy_active/story.json"
            story.parent.mkdir(parents=True)
            story.write_text("{}", encoding="utf-8")
            subprocess.run(["git", "add", "episodes/legacy_active/story.json"], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "-qm", "legacy episode"], cwd=root, check=True)
            self.assertEqual(duplicate_guard(root, "new_candidate", "request-new", "default"),
                             "RESUME_EXISTING_EPISODE")
            self.assertTrue((root / ".pipeline/state.json").is_file())
            self.assertEqual(PipelineStore(root).status()["slug"], "legacy_active")
            persisted = subprocess.run(["git", "add", "--", ".pipeline"], cwd=root, capture_output=True)
            self.assertEqual(persisted.returncode, 0)

    def remote(self):
        patcher = patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "test-only"})
        patcher.start()
        self.addCleanup(patcher.stop)
        http = Mock()
        store = AttemptStore(self.root, http)
        store._verify_live_preflight = Mock()
        return store, http

    def test_remote_compare_and_set_conflict_prevents_claim(self):
        store, http = self.remote()
        http.request.side_effect = [Mock(status_code=404), Mock(status_code=422)]
        with self.assertRaisesRegex(PublicationLocked, "CLAIM_REJECTED"):
            store.claim("demo", "youtube", "one")
        self.assertEqual(http.request.call_count, 2)
        self.assertFalse((self.root / ".publication-attempts/demo.json").exists())
        payload = http.request.call_args.kwargs["json"]
        self.assertNotIn("sha", payload)
        self.assertTrue(json.loads(base64.b64decode(payload["content"]))["publisher_started"])

    def test_remote_timeout_has_no_blind_retry(self):
        store, http = self.remote()
        http.request.side_effect = [Mock(status_code=404), requests.Timeout("uncertain")]
        with self.assertRaisesRegex(PublicationLocked, "CLAIM_UNCERTAIN"):
            store.claim("demo", "youtube", "one")
        self.assertEqual(http.request.call_count, 2)

    def test_remote_marker_checked_even_with_stale_checkout(self):
        store, http = self.remote()
        existing = {"slug": "demo", "session_id": "previous", "platforms": {"youtube": {}}}
        response = Mock(status_code=200)
        response.json.return_value = {"sha": "oldsha", "content": base64.b64encode(json.dumps(existing).encode()).decode()}
        http.request.return_value = response
        with self.assertRaises(PublicationLocked):
            store.claim("demo", "instagram", "new_run")
        self.assertEqual(http.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
