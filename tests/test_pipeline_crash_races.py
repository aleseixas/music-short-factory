from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from engine.pipeline_runtime import RunIdentity, prepare_request, verify_request_commit
from engine.pipeline_state import PipelineError, PipelineStore, atomic_json
from engine.process_lock import process_lock
from publishing.attempts import AttemptStore, PublicationLocked

PROJECT = Path(__file__).resolve().parents[1]


class CrashRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"GITHUB_ACTIONS": "false", "GITHUB_REPOSITORY": "", "GH_TOKEN": "", "GITHUB_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)

    def author(self, root=None):
        root = root or self.root
        store = PipelineStore(root)
        store.start("demo", "req")
        store.transition("demo", "UNIQUE")
        store.transition("demo", "AUTHORING")
        directory = root / "episodes/demo"
        directory.mkdir(parents=True)
        atomic_json(directory / "story.json", {"text": "original"})
        workflow = root / ".github/workflows/episode-media-preflight.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_bytes((PROJECT / ".github/workflows/episode-media-preflight.yml").read_bytes())
        return store

    def queued(self, root=None):
        root = root or self.root
        store = self.author(root)
        store.transition("demo", "LOCAL_VALIDATION")
        store.transition("demo", "MEDIA_PREFLIGHT", local_preflight_passed=True, run_id="123", commit_sha="a" * 40)
        store.transition("demo", "READY_TO_QUEUE")
        store.transition("demo", "QUEUED")
        queue = root / ".publish-queue/demo.txt"
        queue.parent.mkdir()
        queue.write_text("demo\n123\nreq\n")
        return store

    @contextmanager
    def child(self, code, *args):
        env = dict(os.environ, PYTHONPATH=str(PROJECT))
        # These tests isolate local kernel locks. Distributed fencing is tested
        # separately against a shared authority using independent processes.
        code = "import engine.coordination_runtime as cr; cr._factory=lambda root: None\n" + code
        process = subprocess.Popen([sys.executable, "-u", "-c", code, *map(str, args)], cwd=PROJECT,
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

    def test_killed_state_writer_releases_lock_without_deleting_lockfile(self):
        code = "from engine.pipeline_state import PipelineStore; from pathlib import Path; import sys,time\ns=PipelineStore(Path(sys.argv[1]))\nwith s.lock():\n print('locked',flush=True)\n time.sleep(60)"
        with self.child(code, self.root) as process:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaises(TimeoutError):
                with process_lock(self.root / ".pipeline/.lock", timeout=0.1):
                    pass
            process.kill()
            process.wait(timeout=5)
            PipelineStore(self.root).start("after_crash", "new")
        self.assertTrue((self.root / ".pipeline/.lock").exists())

    def test_killed_publisher_lock_is_recoverable_without_clearing_attempt(self):
        self.queued()
        AttemptStore(self.root, allow_local=True).claim("demo", "youtube", "first")
        code = "from publishing.attempts import AttemptStore; from pathlib import Path; import sys,time\nwith AttemptStore(Path(sys.argv[1]),allow_local=True)._local_lock('demo'):\n print('locked',flush=True)\n time.sleep(60)"
        with self.child(code, self.root) as process:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            process.kill()
            process.wait(timeout=5)
            with self.assertRaisesRegex(PublicationLocked, "ALREADY_ATTEMPTED"):
                AttemptStore(self.root, allow_local=True).claim("demo", "youtube", "second")
        self.assertFalse(PipelineStore(self.root).status("demo")["mutation_allowed"])

    def test_two_processes_cannot_claim_different_slugs_in_one_channel(self):
        gate = self.root / "go"
        code = "from engine.pipeline_state import PipelineStore,PipelineError; from pathlib import Path; import sys,time\nwhile not Path(sys.argv[2]).exists(): time.sleep(.01)\ntry:\n PipelineStore(Path(sys.argv[1])).start(sys.argv[3],sys.argv[3]); print('owned')\nexcept PipelineError as e: print(e.code)"
        with self.child(code, self.root, gate, "first") as one, self.child(code, self.root, gate, "second") as two:
            gate.touch()
            outputs = [one.communicate(timeout=15)[0].strip(), two.communicate(timeout=15)[0].strip()]
        self.assertCountEqual(outputs, ["owned", "ACTIVE_EPISODE_EXISTS"])

    def test_interrupted_episode_index_transaction_replays_before_new_owner(self):
        store = PipelineStore(self.root)
        atomic_json(store.index_path, store._empty_index())
        def interrupted(path, value):
            if path == store.index_path:
                raise OSError("simulated process interruption")
            atomic_json(path, value)
        with patch("engine.pipeline_state.atomic_json", side_effect=interrupted):
            with self.assertRaises(OSError):
                store.start("first", "req")
        self.assertEqual(PipelineStore(self.root).status()["slug"], "first")
        with self.assertRaisesRegex(PipelineError, "ACTIVE_EPISODE_EXISTS"):
            PipelineStore(self.root).start("second", "other")
        self.assertFalse((self.root / ".pipeline/transaction.json").exists())
        self.assertEqual(PipelineStore(self.root).status()["slug"], "first")

    def test_two_local_preflights_do_not_share_validation_ownership(self):
        self.author()
        barrier = threading.Barrier(2)
        calls = []
        def validator(*args, **kwargs):
            calls.append(True)
            time.sleep(.15)
            return []
        def run():
            barrier.wait()
            try:
                return prepare_request(self.root, "demo", "req", validator=validator)["stage"]
            except PipelineError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run(), range(2)))
        self.assertCountEqual(results, ["MEDIA_PREFLIGHT", "REQUEST_ALREADY_EXISTS"])
        self.assertEqual(len(calls), 1)

    def test_changed_authoring_during_validator_cannot_receive_local_pass(self):
        self.author()
        def validator(*args, **kwargs):
            atomic_json(self.root / "episodes/demo/story.json", {"text": "not validated"})
            return []
        result = prepare_request(self.root, "demo", "req", validator=validator)
        self.assertEqual(result["stage"], "VALIDATION_FAILED")
        self.assertFalse((self.root / ".episode-check/demo--req.json").exists())

    def test_old_request_contained_in_later_commit_cannot_correlate_a_run(self):
        self.author()
        prepared = prepare_request(self.root, "demo", "req", validator=lambda *a, **k: [])
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.root, text=True).strip()
        git("init", "-q")
        git("add", prepared["request_path"])
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "request")
        original = git("rev-parse", "HEAD")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "unrelated")
        later = git("rev-parse", "HEAD")
        verify_request_commit(self.root, RunIdentity("req", "demo", original, "episode-media-preflight.yml"), prepared["request_path"])
        verify_request_commit(self.root, RunIdentity("req", "demo", original, "episode-media-preflight.yml", before_sha="0" * 40), prepared["request_path"])
        with self.assertRaisesRegex(PipelineError, "REQUEST_COMMIT_MISMATCH"):
            verify_request_commit(self.root, RunIdentity("req", "demo", later, "episode-media-preflight.yml"), prepared["request_path"])
        with self.assertRaisesRegex(PipelineError, "REQUEST_COMMIT_MISMATCH"):
            verify_request_commit(self.root, RunIdentity("req", "demo", later, "episode-media-preflight.yml", before_sha="0" * 40), prepared["request_path"])

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=self.root, text=True, stderr=subprocess.PIPE).strip()

    def request_push(self):
        self.author()
        prepared = prepare_request(self.root, "demo", "req", validator=lambda *a, **k: [])
        self.git("init", "-q")
        self.git("commit", "--allow-empty", "-qm", "before push")
        before = self.git("rev-parse", "HEAD")
        self.git("add", prepared["request_path"])
        self.git("commit", "-qm", "request")
        receipt = self.git("rev-parse", "HEAD")
        self.git("commit", "--allow-empty", "-qm", "end of push")
        after = self.git("rev-parse", "HEAD")
        return before, receipt, after, prepared["request_path"]

    def test_explicit_push_range_accepts_receipt_before_final_commit(self):
        before, _, after, path = self.request_push()
        identity = RunIdentity("req", "demo", after, "episode-media-preflight.yml", before_sha=before)
        self.assertEqual(verify_request_commit(self.root, identity, path), path)

    def test_explicit_push_range_excludes_request_from_previous_push(self):
        _, receipt, after, path = self.request_push()
        identity = RunIdentity("req", "demo", after, "episode-media-preflight.yml", before_sha=receipt)
        with self.assertRaisesRegex(PipelineError, "REQUEST_COMMIT_MISMATCH"):
            verify_request_commit(self.root, identity, path)

    def test_explicit_push_range_rejects_multiple_new_requests(self):
        before, _, _, path = self.request_push()
        atomic_json(self.root / ".episode-check/other--req.json", {"slug": "other", "request_id": "req"})
        self.git("add", ".episode-check")
        self.git("commit", "-qm", "another request")
        after = self.git("rev-parse", "HEAD")
        identity = RunIdentity("req", "demo", after, "episode-media-preflight.yml", before_sha=before)
        with self.assertRaisesRegex(PipelineError, "REQUEST_COMMIT_MISMATCH"):
            verify_request_commit(self.root, identity, path)

    def test_explicit_push_range_accepts_receipt_introduced_by_merge(self):
        before, _, after, path = self.request_push()
        self.git("checkout", "-qb", "merge-target", before)
        self.git("merge", "--no-ff", "-qm", "merge request", after)
        merged = self.git("rev-parse", "HEAD")
        identity = RunIdentity("req", "demo", merged, "episode-media-preflight.yml", before_sha=before)
        self.assertEqual(verify_request_commit(self.root, identity, path), path)

    def test_push_range_requires_full_before_sha_before_running_git(self):
        for before in ("HEAD", "a" * 41, "--help"):
            with self.subTest(before=before), patch("engine.pipeline_runtime.subprocess.run") as run:
                with self.assertRaisesRegex(PipelineError, "BEFORE_SHA_INVALID"):
                    verify_request_commit(self.root, RunIdentity("req", "demo", "a" * 40, "episode-media-preflight.yml", before_sha=before))
                run.assert_not_called()

    def test_wait_cli_uses_explicit_push_boundary_and_final_sha(self):
        import io
        from contextlib import redirect_stdout
        from scripts.pipeline_control import main
        before, _, after, _ = self.request_push()
        output = io.StringIO()
        with patch("scripts.pipeline_control.github_fetcher"), patch("scripts.pipeline_control.wait_for_run", return_value={"id": 7, "conclusion": "success"}) as wait, redirect_stdout(output):
            status = main(["--root", str(self.root), "wait", "demo", "--request-id", "req", "--commit-sha", after,
                           "--before-sha", before, "--repository", "owner/repo"])
        self.assertEqual(status, 0, output.getvalue())
        identity = wait.call_args.args[0]
        self.assertEqual((identity.before_sha, identity.commit_sha), (before, after))

    def test_two_stale_clones_cannot_both_claim_remote_publication(self):
        roots = [self.root / "one", self.root / "two"]
        for root in roots:
            self.queued(root)
        barrier = threading.Barrier(2)
        mutex = threading.Lock()
        stored = []
        def request(method, url, **kwargs):
            if method == "GET":
                barrier.wait(timeout=5)
                return Mock(status_code=404)
            with mutex:
                if stored:
                    return Mock(status_code=422)
                stored.append(kwargs["json"])
                return Mock(status_code=201)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "test"}):
            stores = [AttemptStore(root, Mock(request=request)) for root in roots]
            for store in stores:
                store._verify_live_preflight = Mock()
            def claim(index):
                try:
                    stores[index].claim("demo", "youtube", f"session-{index}")
                    return "owned"
                except PublicationLocked:
                    return "rejected"
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(claim, range(2)))
        self.assertCountEqual(results, ["owned", "rejected"])
        self.assertEqual(len(stored), 1)

    def test_cli_and_actions_require_successful_external_preflight(self):
        store = self.queued()
        for actions in ("false", "true"):
            with self.subTest(actions=actions), patch.dict(os.environ, {"GITHUB_ACTIONS": actions, "GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "test"}):
                http = Mock()
                http.request.return_value = Mock(status_code=200, json=lambda: {"id": 123, "status": "in_progress"})
                attempts = AttemptStore(self.root, http)
                with patch("publishing.snapshot.approved_manifest", return_value=({}, "")), patch("publishing.attempts.time.monotonic", side_effect=[0, 301]):
                    with self.assertRaisesRegex(PublicationLocked, "PREFLIGHT_UNVERIFIED"):
                        attempts._verify_live_preflight("demo", store.status("demo"))
                self.assertTrue(all(call.args[0] == "GET" for call in http.request.call_args_list))

    def test_successful_run_with_wrong_request_does_not_authorize_local_cli(self):
        store = self.queued()
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "test"}):
            http = Mock()
            run = {"id": 123, "status": "completed", "conclusion": "success", "event": "push", "head_branch": "main",
                   "path": ".github/workflows/episode-media-preflight.yml", "head_sha": "a" * 40}
            receipt = {"slug": "other", "request_id": "req", "local_preflight_passed": True}
            http.request.side_effect = [Mock(status_code=200, json=lambda: run),
                                        Mock(status_code=200, json=lambda: {"content": base64.b64encode(json.dumps(receipt).encode()).decode()})]
            with patch("publishing.snapshot.approved_manifest", return_value=({}, "")):
                with self.assertRaisesRegex(PublicationLocked, "PREFLIGHT_UNVERIFIED"):
                    AttemptStore(self.root, http)._verify_live_preflight("demo", store.status("demo"))

    def test_successful_external_run_and_matching_request_pass_read_only_gate(self):
        store = self.queued()
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "test"}):
            http = Mock()
            run = {"id": 123, "status": "completed", "conclusion": "success", "event": "push", "head_branch": "main",
                   "path": ".github/workflows/episode-media-preflight.yml", "head_sha": "a" * 40}
            receipt = {"slug": "demo", "request_id": "req", "local_preflight_passed": True}
            http.request.side_effect = [Mock(status_code=200, json=lambda: run),
                                        Mock(status_code=200, json=lambda: {"content": base64.b64encode(json.dumps(receipt).encode()).decode()})]
            with patch("publishing.snapshot.approved_manifest", return_value=({}, "")) as verify:
                AttemptStore(self.root, http)._verify_live_preflight("demo", store.status("demo"))
            verify.assert_called_once_with(self.root, "demo", "123", "req")
            self.assertEqual(http.request.call_args.kwargs["params"], {"ref": "a" * 40})
            self.assertTrue(all(call.args[0] == "GET" for call in http.request.call_args_list))

    def test_reconcile_fetches_exact_remote_marker_before_using_stale_local_state(self):
        import io
        from contextlib import redirect_stdout
        from scripts.pipeline_control import main
        self.queued()
        def synchronize(attempts, slug):
            marker = {"slug": slug, "stage": "PUBLISHED", "publisher_started": True,
                      "run_id": "456", "session_id": "github:456:1"}
            atomic_json(self.root / ".publication-attempts" / f"{slug}.json", marker)
            return marker, "remote-sha"
        output = io.StringIO()
        with patch.object(AttemptStore, "_read", autospec=True, side_effect=synchronize) as read, redirect_stdout(output):
            status = main(["--root", str(self.root), "reconcile", "--slug", "demo", "--repository", "owner/repo"])
        self.assertEqual(status, 0)
        self.assertEqual(read.call_count, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["stage"], "PUBLISHED")
        self.assertFalse(result["mutation_allowed"])
        self.assertTrue(result["can_create_new_episode"])


if __name__ == "__main__":
    unittest.main()
