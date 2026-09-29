from __future__ import annotations

import base64
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from engine.pipeline_state import PipelineError, PipelineStore
from scripts.dispatch_publication import dispatch


class DispatchAuthority:
    def __init__(self):
        self.closed = False
        self.abandoned = []
        self.generation = 1
        self.slug = "demo"
        self.request_id = "request"

    def status(self, slug):
        return {"slug": self.slug, "request_id": self.request_id, "generation": self.generation,
                "requested_slug_closed": {"request_id": "request", "reason": "DISPATCH_UNCERTAIN"} if self.closed else None}

    def validate_dispatch(self, slug, request_id, state):
        if self.closed:
            raise PipelineError("RESERVATION_CLOSED", slug)
        return {"generation": 1}

    def abandon_expired(self, slug, request_id, outcome, expected_generation=None):
        if expected_generation != self.generation:
            raise PipelineError("SHARED_STALE_FENCE", "authoritative generation changed")
        if not self.closed:
            self.abandoned.append((slug, request_id, outcome))
        self.closed = True


def dispatch_child(root, endpoint, crash_at, result_queue):
    def api(method, path, body=None):
        request = Request(endpoint, data=json.dumps([method, path, body]).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=10) as response:
            code, value = json.load(response)
        return code, value

    def checkpoint(stage):
        if stage == crash_at:
            os._exit(73)

    class RemoteAuthority:
        def status(self, slug):
            _, value = api("STATUS", "authority", [slug])
            return value

        def validate_dispatch(self, slug, request_id, state):
            _, value = api("VALIDATE", "authority", [slug, request_id, state])
            if value.get("error"):
                raise PipelineError(value["error"], "server authority rejected dispatch")
            return value

        def abandon_expired(self, slug, request_id, outcome, expected_generation=None):
            _, value = api("ABANDON", "authority", [slug, request_id, outcome, expected_generation])
            if value.get("error"):
                raise PipelineError(value["error"], "server authority rejected closure")
            return value

    try:
        result = dispatch(Path(root), "owner/repo", "demo", "request", "123", api=api,
                          coordinator=RemoteAuthority(), checkpoint=checkpoint)
        result_queue.put(result)
    except PipelineError as exc:
        result_queue.put({"error": exc.code})


class PublicationDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = PipelineStore(self.root)
        self.store.start("demo", "request")
        for stage in ("UNIQUE", "AUTHORING", "LOCAL_VALIDATION"):
            self.store.transition("demo", stage)
        self.store.transition("demo", "MEDIA_PREFLIGHT", local_preflight_passed=True, run_id="123", commit_sha="b" * 40)
        self.store.transition("demo", "READY_TO_QUEUE", media_preflight_passed=True)
        self.store.transition("demo", "QUEUED")
        queue = self.root / ".publish-queue/demo.txt"
        queue.parent.mkdir()
        queue.write_text("demo\n123\nrequest\n")
        self.record = None
        self.version = 0
        self.posts = []
        self.post_status = 204
        self.runs = []
        self.marker = False
        self.run_queries = []
        self.exact_queries = []
        self.now = 1000
        self.authority = DispatchAuthority()
        self.source_run = {"id": 123, "event": "push", "head_branch": "main", "head_sha": "b" * 40,
                           "path": ".github/workflows/episode-media-preflight.yml", "status": "in_progress"}
        self.source_receipt = {"slug": "demo", "request_id": "request", "local_preflight_passed": True}

    def api(self, method, path, body=None):
        if path == "authority":
            try:
                if method == "VALIDATE":
                    return 200, self.authority.validate_dispatch(*body)
                if method == "STATUS":
                    return 200, self.authority.status(*body)
                self.authority.abandon_expired(*body)
                return 200, {}
            except PipelineError as exc:
                return 409, {"error": exc.code}
        if "/contents/.episode-check/" in path:
            self.assertEqual(path, "repos/owner/repo/contents/.episode-check/demo--request.json?ref=" + "b" * 40)
            return 200, {"content": base64.b64encode(json.dumps(self.source_receipt).encode()).decode()}
        if path.endswith("/actions/runs/123"):
            return 200, self.source_run
        if ".publication-attempts/" in path:
            return (200 if self.marker else 404), {}
        if "/contents/.pipeline/dispatch/" in path:
            if method == "GET":
                if self.record is None:
                    return 404, {}
                return 200, {"sha": str(self.version), "content": base64.b64encode(json.dumps(self.record).encode()).decode()}
            if body.get("sha") != (str(self.version) if self.version else None):
                return 409, {}
            self.record = json.loads(base64.b64decode(body["content"]))
            self.version += 1
            return 201, {"content": {"sha": str(self.version)}}
        if method == "POST":
            self.assertEqual(self.record["status"], "SENDING")
            self.posts.append(body)
            if isinstance(self.post_status, Exception):
                raise self.post_status
            return self.post_status, {}
        if "/actions/runs/" in path:
            self.exact_queries.append(path)
            run_id = path.rsplit("/", 1)[1]
            run = next((run for run in self.runs if str(run["id"]) == run_id), None)
            return (200, run) if run else (404, {})
        self.assertIn("/runs?event=workflow_dispatch&per_page=100&page=", path)
        self.run_queries.append(path)
        page = int(path.rsplit("=", 1)[1])
        return 200, {"workflow_runs": self.runs[(page - 1) * 100:page * 100], "total_count": len(self.runs)}

    def matching_run(self, run_id=111):
        return {"id": run_id, "display_title": "publish/demo/request/123", "event": "workflow_dispatch",
                "head_sha": "a" * 40, "status": "in_progress", "conclusion": None,
                "path": ".github/workflows/publish-episode.yml", "head_branch": "main"}

    def dispatch(self, **options):
        return dispatch(self.root, "owner/repo", "demo", "request", "123", api=options.pop("api", self.api),
                        clock=lambda: self.now, coordinator=self.authority, **options)

    def test_existing_queue_without_dispatch_intent_can_resume_before_publisher(self):
        self.assertEqual(self.dispatch()["result"], "DISPATCHED")
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.dispatch()["result"], "PENDING")
        self.assertEqual(len(self.posts), 1)

    def test_ambiguous_transport_is_persisted_and_never_resent(self):
        self.post_status = PipelineError("DISPATCH_TRANSPORT_UNCERTAIN", "timeout", recoverable=True)
        self.assertEqual(self.dispatch()["result"], "PENDING")
        self.assertEqual(self.record["status"], "SENDING")
        self.dispatch()
        self.assertEqual(len(self.posts), 1)

    def test_definite_rejection_can_resume_after_cause_is_corrected(self):
        self.post_status = 403
        with self.assertRaisesRegex(PipelineError, "DISPATCH_REJECTED"):
            self.dispatch()
        self.assertEqual(self.record["status"], "REJECTED")
        self.post_status = 204
        self.assertEqual(self.dispatch()["result"], "DISPATCHED")
        self.assertEqual(len(self.posts), 2)

    def test_server_error_does_not_authorize_second_dispatch(self):
        self.post_status = 502
        self.assertEqual(self.dispatch()["result"], "UNCERTAIN")
        self.dispatch()
        self.assertEqual(len(self.posts), 1)

    def test_observes_exact_request_on_later_page_without_choosing_latest(self):
        self.dispatch()
        self.runs = [{"id": index, "display_title": "publish/unrelated/other/456", "event": "workflow_dispatch"}
                     for index in range(100)]
        self.runs.append(self.matching_run())
        result = self.dispatch()
        self.assertEqual(result["run_id"], 111)
        self.assertEqual(result["commit_sha"], "a" * 40)
        self.assertEqual(len(self.posts), 1)

    def test_discovery_beyond_one_hundred_pages_saves_exact_run_for_restart(self):
        self.dispatch()
        self.runs = [{"id": index, "display_title": "unrelated", "event": "workflow_dispatch"}
                     for index in range(1, 10001)]
        self.runs.append(self.matching_run(10001))
        self.assertEqual(self.dispatch()["run_id"], 10001)
        self.assertEqual(len(self.run_queries), 101)
        self.assertEqual(self.record["run_id"], 10001)
        self.assertEqual(self.record["head_sha"], "a" * 40)
        self.runs[-1].update(status="completed", conclusion="success")
        result = self.dispatch()
        self.assertEqual(result["conclusion"], "success")
        self.assertEqual(len(self.run_queries), 101)
        self.assertEqual(self.exact_queries, ["repos/owner/repo/actions/runs/10001"])
        self.assertEqual(len(self.posts), 1)

    def test_cached_run_must_preserve_every_identity_field(self):
        self.dispatch()
        self.runs = [self.matching_run()]
        self.dispatch()
        for field, wrong in (("display_title", "publish/demo/request/456"),
                             ("event", "push"), ("path", ".github/workflows/other.yml"),
                             ("head_branch", "other"), ("head_sha", "b" * 40)):
            with self.subTest(field=field):
                previous = self.runs[0][field]
                self.runs[0][field] = wrong
                with self.assertRaisesRegex(PipelineError, "DISPATCH_RUN_IDENTITY_INVALID"):
                    self.dispatch()
                self.runs[0][field] = previous
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.run_queries), 1)

    def test_missing_cached_run_is_not_rediscovered_or_redispatched(self):
        self.dispatch()
        self.runs = [self.matching_run()]
        self.dispatch()
        self.runs.clear()
        with self.assertRaisesRegex(PipelineError, "DISPATCH_RUN_QUERY_FAILED"):
            self.dispatch()
        self.assertEqual(len(self.run_queries), 1)
        self.assertEqual(len(self.posts), 1)

    def test_incomplete_server_listing_does_not_infer_absence(self):
        self.dispatch()
        def api(method, path, body=None):
            if "/runs?" in path:
                return 200, {"workflow_runs": [], "total_count": 101}
            return self.api(method, path, body)
        with self.assertRaisesRegex(PipelineError, "DISPATCH_RUN_DISCOVERY_PENDING") as caught:
            self.dispatch(api=api)
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(len(self.posts), 1)

    def test_discovery_timeout_preserves_intent_without_resending(self):
        self.dispatch()
        self.runs = [{"id": index, "display_title": "unrelated"} for index in range(100)]
        clock = iter((0, 0, 61))
        with self.assertRaisesRegex(PipelineError, "DISPATCH_RUN_DISCOVERY_PENDING") as caught:
            self.dispatch(monotonic=lambda: next(clock))
        self.assertTrue(caught.exception.recoverable)
        self.assertEqual(len(self.run_queries), 1)
        self.assertEqual(len(self.posts), 1)

    def test_observed_run_receipt_still_uses_compare_and_set(self):
        self.dispatch()
        self.runs = [self.matching_run()]
        def api(method, path, body=None):
            if method == "PUT":
                self.assertEqual(body["sha"], str(self.version))
                return 409, {}
            return self.api(method, path, body)
        with self.assertRaisesRegex(PipelineError, "DISPATCH_INTENT_CONFLICT"):
            self.dispatch(api=api)
        self.assertNotIn("run_id", self.record)
        self.assertEqual(len(self.posts), 1)

    def test_ambiguous_runs_cannot_be_cached(self):
        self.dispatch()
        self.runs = [self.matching_run(111), self.matching_run(112)]
        with self.assertRaisesRegex(PipelineError, "DISPATCH_RUN_AMBIGUOUS"):
            self.dispatch()
        self.assertNotIn("run_id", self.record)
        self.assertEqual(len(self.posts), 1)

    def test_any_remote_attempt_prevents_new_dispatch(self):
        self.marker = True
        self.assertEqual(self.dispatch()["result"], "PUBLISHER_ALREADY_STARTED")
        self.assertEqual(self.posts, [])

    def test_queue_identity_cannot_be_replaced_by_new_source_run(self):
        (self.root / ".publish-queue/demo.txt").write_text("demo\n999\nrequest\n")
        with self.assertRaisesRegex(PipelineError, "DISPATCH_QUEUE_MISMATCH"):
            self.dispatch()
        self.assertEqual(self.posts, [])

    def test_receipt_contains_server_lease_owner_generation_and_source_identity(self):
        self.dispatch(owner_id="worker-a")
        self.assertEqual(self.record["owner_id"], "worker-a")
        self.assertEqual(self.record["request_id"], "request")
        self.assertEqual(self.record["expected_workflow"], ".github/workflows/publish-episode.yml")
        self.assertEqual(self.record["created_at"], 1000)
        self.assertEqual(self.record["lease_expires_at"], 1120)
        self.assertEqual(self.record["reconcile_after"], 1900)
        self.assertEqual(self.record["run_reconcile_after"], 87400)
        self.assertEqual(self.record["reservation_generation"], 1)
        self.assertEqual(self.record["expected_source_sha"], "b" * 40)

    def test_source_workflow_commit_and_receipt_must_be_verified_before_intent(self):
        for field, wrong in (("head_sha", "c" * 40), ("event", "workflow_dispatch"),
                             ("path", ".github/workflows/publish-episode.yml"),
                             ("head_branch", "other"), ("id", 999)):
            with self.subTest(field=field):
                original = self.source_run[field]
                self.source_run[field] = wrong
                with self.assertRaisesRegex(PipelineError, "DISPATCH_SOURCE_UNVERIFIED"):
                    self.dispatch()
                self.source_run[field] = original
                self.assertIsNone(self.record)
                self.assertEqual(self.posts, [])
        self.source_receipt["request_id"] = "unrelated"
        with self.assertRaisesRegex(PipelineError, "DISPATCH_SOURCE_UNVERIFIED"):
            self.dispatch()
        self.assertEqual(self.posts, [])

    def test_failed_source_run_cannot_authorize_a_new_dispatch(self):
        self.source_run.update(status="completed", conclusion="failure")
        with self.assertRaisesRegex(PipelineError, "DISPATCH_SOURCE_UNVERIFIED"):
            self.dispatch()
        self.assertIsNone(self.record)
        self.assertEqual(self.posts, [])

    def test_stale_shared_generation_prevents_intent_and_post(self):
        def fail(*args, **kwargs):
            raise PipelineError("SHARED_STALE_FENCE", "another clone owns this generation")
        self.authority.validate_dispatch = fail
        with self.assertRaisesRegex(PipelineError, "SHARED_STALE_FENCE"):
            self.dispatch()
        self.assertIsNone(self.record)
        self.assertEqual(self.posts, [])

    def test_ambiguous_send_expires_to_fenced_terminal_outcome_without_retry(self):
        self.post_status = PipelineError("DISPATCH_TRANSPORT_UNCERTAIN", "timeout")
        self.dispatch()
        self.now = 1901
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(self.authority.abandoned, [("demo", "request", "DISPATCH_UNCERTAIN")])
        self.assertFalse(self.dispatch()["redispatch_allowed"])
        self.assertEqual(len(self.posts), 1)

    def test_incomplete_listing_can_close_uncertain_but_cannot_prove_not_sent(self):
        self.dispatch()
        self.now = 1901
        def api(method, path, body=None):
            if "/runs?" in path:
                return 200, {"workflow_runs": [], "total_count": 101}
            return self.api(method, path, body)
        result = self.dispatch(api=api)
        self.assertEqual(result["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(self.authority.closed)

    def test_closure_failure_keeps_intent_for_safe_reconciliation(self):
        self.dispatch()
        self.now = 1901
        def fail(*args, **kwargs):
            raise PipelineError("RESERVATION_ACTIVE", "lease is active")
        self.authority.abandon_expired = fail
        with self.assertRaisesRegex(PipelineError, "RESERVATION_ACTIVE"):
            self.dispatch()
        self.assertEqual(self.record["status"], "DISPATCHED")
        self.assertEqual(len(self.posts), 1)

    def test_completed_run_without_publisher_eventually_releases_slot_conservatively(self):
        self.dispatch()
        self.runs = [self.matching_run()]
        self.runs[0].update(status="completed", conclusion="failure")
        self.assertEqual(self.dispatch()["result"], "OBSERVED")
        self.now = 1901
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(self.record["reconciliation_reason"], "completed_run_without_publisher_after_deadline")
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(self.authority.closed)

    def test_expired_discovery_of_completed_success_without_claim_also_closes(self):
        self.dispatch()
        self.runs = [self.matching_run()]
        self.runs[0].update(status="completed", conclusion="success")
        self.now = 1901
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(self.record["run_id"], 111)
        self.assertEqual(len(self.posts), 1)

    def test_publisher_winning_race_prevents_dispatch_terminal_closure(self):
        self.dispatch()
        self.now = 1901
        def already_publishing(*args, **kwargs):
            raise PipelineError("SHARED_PUBLICATION_ALREADY_STARTED", "publisher won atomic closure")
        self.authority.abandon_expired = already_publishing
        with self.assertRaisesRegex(PipelineError, "SHARED_PUBLICATION_ALREADY_STARTED"):
            self.dispatch()
        self.assertEqual(self.record["status"], "DISPATCHED")
        self.assertEqual(len(self.posts), 1)

    def test_queued_or_running_workflow_cannot_pin_the_channel_forever(self):
        self.dispatch()
        self.runs = [self.matching_run()]
        self.runs[0]["status"] = "queued"
        self.now = 1901
        self.assertEqual(self.dispatch()["result"], "OBSERVED")
        self.runs[0]["status"] = "in_progress"
        self.now = 87399
        self.assertEqual(self.dispatch()["result"], "OBSERVED")
        self.now = 87400
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(self.record["reconciliation_reason"], "run_without_publisher_exceeded_maximum_lifetime")
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(self.authority.closed)

    def test_valid_handoff_generation_does_not_permanently_block_reconciliation(self):
        self.dispatch()
        self.authority.generation = 5
        self.now = 1901
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(self.record["reservation_generation"], 1)
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(self.authority.closed)

    def test_old_dispatch_cannot_close_a_different_authoritative_request(self):
        self.dispatch()
        self.authority.request_id = "replacement"
        self.now = 1901
        with self.assertRaisesRegex(PipelineError, "SHARED_STALE_FENCE"):
            self.dispatch()
        self.assertFalse(self.authority.closed)
        self.assertEqual(len(self.posts), 1)

    def test_generation_change_between_reconcile_read_and_close_is_rejected(self):
        self.dispatch()
        self.now = 1901
        status = self.authority.status
        def moved_generation(slug):
            value = status(slug)
            self.authority.generation += 1
            return value
        self.authority.status = moved_generation
        with self.assertRaisesRegex(PipelineError, "SHARED_STALE_FENCE"):
            self.dispatch()
        self.assertFalse(self.authority.closed)
        self.assertEqual(len(self.posts), 1)

    def test_terminal_receipt_survives_stale_local_stage(self):
        self.dispatch()
        self.now = 1901
        self.dispatch()
        from engine.pipeline_state import atomic_json
        state_path = self.store.episode_path("demo")
        state = json.loads(state_path.read_text())
        state["stage"] = "REJECTED"
        atomic_json(state_path, state)
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(len(self.posts), 1)

    def test_legacy_intent_gets_one_bounded_reconciliation_window_without_post(self):
        self.record = {"slug": "demo", "request_id": "request", "source_run_id": "123", "status": "INTENT"}
        self.version = 1
        self.assertEqual(self.dispatch()["result"], "PENDING")
        self.assertEqual(self.record["reconcile_after"], 1900)
        self.now = 1800
        self.assertEqual(self.dispatch()["result"], "PENDING")
        self.assertEqual(self.record["reconcile_after"], 1900)
        self.now = 1901
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(self.posts, [])

    def test_new_api_run_id_is_persisted_then_observed_exactly(self):
        self.runs = [self.matching_run()]
        def api(method, path, body=None):
            if method == "POST":
                self.posts.append(body)
                return 200, {"workflow_run_id": 111}
            return self.api(method, path, body)
        self.dispatch(api=api)
        self.assertEqual(self.record["run_id"], 111)
        self.assertEqual(self.dispatch()["run_id"], 111)
        self.assertEqual(self.run_queries, [])
        self.assertEqual(len(self.posts), 1)

    def test_real_shared_queue_handoff_allows_different_publisher_owner_immediately(self):
        from engine.coordination_runtime import save_token, sync_authority
        from engine.shared_coordination import SharedCoordinator, using_coordinator
        from tests.test_shared_coordination import ServerBackend, authority

        with authority() as (url, data, _):
            coordinator = SharedCoordinator(self.root, ServerBackend(url), owner_id="media-run")
            publisher = SharedCoordinator(self.root / "publisher-clone", ServerBackend(url), owner_id="publisher-run")
            token = coordinator.acquire("demo", "request")
            token = coordinator.set_phase(token, "QUEUED", files={
                ".pipeline/episodes/demo.json": self.store.episode_path("demo").read_bytes(),
                ".publish-queue/demo.txt": (self.root / ".publish-queue/demo.txt").read_bytes(),
            })
            token = coordinator.release(token)
            with using_coordinator(self.root, coordinator):
                save_token(self.root, token)
                sync_authority(self.root, "demo", download=True)
                def api(method, path, body=None):
                    if method == "POST":
                        self.assertEqual(self.record["status"], "SENDING")
                        publisher_token = publisher.acquire("demo", "request")
                        publisher.close_for_publication(publisher_token, "a" * 64)
                    return self.api(method, path, body)
                result = dispatch(self.root, "owner/repo", "demo", "request", "123", api=api,
                                  clock=lambda: self.now)
            self.assertEqual(result["result"], "DISPATCHED")
            self.assertEqual(len(self.posts), 1)
            self.assertTrue(data["states"]["default"]["publisher_started"])
            self.assertEqual(data["states"]["default"]["owner_id"], "publisher-run")

    def run_children(self, crash_at, count=1):
        guard = threading.Lock()
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                method, path, body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with guard:
                    code, value = parent.api(method, path, body)
                    value["_server_epoch"] = parent.now
                encoded = json.dumps([code, value]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        clone_directories = [tempfile.TemporaryDirectory(prefix="independent-dispatch-clone-") for _ in range(count)]
        clone_roots = [Path(directory.name) / "repository" for directory in clone_directories]
        for clone_root in clone_roots:
            shutil.copytree(self.root, clone_root)
        children = [context.Process(target=dispatch_child,
                    args=(str(clone_root), f"http://127.0.0.1:{server.server_port}", crash_at, results))
                    for clone_root in clone_roots]
        try:
            for child in children:
                child.start()
            for child in children:
                child.join(20)
                self.assertFalse(child.is_alive(), "independent dispatcher must finish")
            return [child.exitcode for child in children], results
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                    child.join(5)
            server.shutdown()
            server.server_close()
            thread.join(5)
            for directory in clone_directories:
                directory.cleanup()

    def test_process_crash_after_prepared_allows_only_expired_safe_takeover(self):
        codes, _ = self.run_children("PREPARED")
        self.assertEqual(codes, [73])
        self.assertEqual(self.record["status"], "PREPARED")
        self.assertEqual(self.posts, [])
        self.assertEqual(self.dispatch()["next_action"], "wait_for_active_dispatch_owner")
        self.assertEqual(self.posts, [])
        self.now = 1121
        self.assertEqual(self.dispatch()["result"], "DISPATCHED")
        self.assertEqual(self.record["generation"], 2)
        self.assertEqual(len(self.posts), 1)

    def test_process_crash_after_send_before_run_id_adopts_exact_run(self):
        codes, _ = self.run_children("POST_RETURNED")
        self.assertEqual(codes, [73])
        self.assertEqual(self.record["status"], "SENDING")
        self.assertEqual(len(self.posts), 1)
        self.runs = [self.matching_run()]
        self.assertEqual(self.dispatch()["result"], "OBSERVED")
        self.assertEqual(self.dispatch()["run_id"], 111)
        self.assertEqual(len(self.posts), 1)

    def test_process_crash_at_send_boundary_never_guesses_that_post_was_absent(self):
        codes, _ = self.run_children("SENDING")
        self.assertEqual(codes, [73])
        self.assertEqual(self.posts, [])
        self.now = 1901
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(self.posts, [])
        self.assertTrue(self.authority.closed)

    def test_process_crash_after_shared_closure_recovers_receipt_without_closing_again(self):
        self.dispatch()
        self.now = 1901
        codes, _ = self.run_children("SHARED_CLOSED")
        self.assertEqual(codes, [73])
        self.assertTrue(self.authority.closed)
        self.assertEqual(self.record["status"], "DISPATCHED")
        self.assertEqual(self.dispatch()["result"], "DISPATCH_UNCERTAIN")
        self.assertEqual(len(self.authority.abandoned), 1)
        self.assertEqual(len(self.posts), 1)

    def test_independent_dispatchers_cannot_both_authorize_post(self):
        codes, results = self.run_children("", count=2)
        self.assertEqual(codes, [0, 0])
        outcomes = [results.get(timeout=3) for _ in range(2)]
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(any(value.get("result") == "DISPATCHED" for value in outcomes))


if __name__ == "__main__":
    unittest.main()
