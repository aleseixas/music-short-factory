"""Independent clients against one atomic server; no client-side lock authority."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest
import requests

from engine.shared_coordination import CASConflict, CoordinationError, GitHubRefBackend, SharedCoordinator

pytestmark = pytest.mark.distributed_coordination


class ServerBackend:
    def __init__(self, url):
        self.url = url

    def read(self, channel):
        response = requests.get(self.url + "/read", params={"channel": channel}, timeout=10)
        data = response.json()
        return data["state"], str(data["revision"]), data["now"]

    def compare_and_swap(self, channel, expected, state, files):
        response = requests.post(self.url + "/cas", json={"channel": channel, "expected": expected,
            "state": state, "files": {path: None if value is None else base64.b64encode(value).decode()
                                      for path, value in files.items()}}, timeout=10)
        if response.status_code == 409:
            raise CASConflict()
        response.raise_for_status()
        result = response.json()
        return str(result["revision"]), result["now"]

    def read_files(self, paths, revision):
        response = requests.get(self.url + "/files", params={"paths": json.dumps(paths), "revision": revision}, timeout=10)
        response.raise_for_status()
        return {path: None if raw is None else base64.b64decode(raw) for path, raw in response.json().items()}

    def read_closed(self, slug, revision):
        path = f".pipeline/closed/{slug}.json"
        raw = self.read_files([path], revision)[path]
        return json.loads(raw) if raw is not None else None


@contextmanager
def authority():
    data = {"states": {}, "files": {}, "history": {0: {}}, "revision": 0, "now": 1_800_000_000.0}
    lock = threading.Lock()  # This is the remote SERVER transaction, not a client lock.

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, code, body):
            value = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)

        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit
            query = parse_qs(urlsplit(self.path).query)
            if urlsplit(self.path).path == "/files":
                with lock:
                    snapshot = data["history"][int(query["revision"][0])]
                    result = {path: base64.b64encode(snapshot[path]).decode() if path in snapshot else None
                              for path in json.loads(query["paths"][0])}
                self.respond(200, result)
                return
            channel = query["channel"][0]
            with lock:
                result = {"state": deepcopy(data["states"].get(channel)), "revision": data["revision"], "now": data["now"]}
            self.respond(200, result)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with lock:
                if payload["expected"] != str(data["revision"]):
                    code, result = 409, {"error": "CAS"}
                else:
                    data["states"][payload["channel"]] = payload["state"]
                    for path, value in payload["files"].items():
                        if value is None:
                            data["files"].pop(path, None)
                        else:
                            data["files"][path] = base64.b64decode(value)
                    data["revision"] += 1
                    data["history"][data["revision"]] = deepcopy(data["files"])
                    code, result = 200, {"revision": data["revision"], "now": data["now"]}
            self.respond(code, result)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", data, lock
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def _claim_worker(root, url, owner, slug, start, results):
    coordinator = SharedCoordinator(Path(root), ServerBackend(url), owner_id=owner, lease_seconds=10)
    start.wait(10)
    try:
        token = coordinator.acquire(slug, "request_1")
        results.put(("won", token))
    except CoordinationError as exc:
        results.put(("rejected", exc.code))


def _old_mutation_worker(root, url, ready, resume, results):
    coordinator = SharedCoordinator(Path(root), ServerBackend(url), owner_id="old", lease_seconds=10)
    token = coordinator.acquire("episode_a", "request_1")
    results.put(token)
    ready.set()
    resume.wait(15)
    try:
        coordinator.commit_mutation(token, {"episodes/episode_a/episode.json": b"stale bytes"})
        results.put("COMMITTED")
    except CoordinationError as exc:
        results.put(exc.code)


def _validated_mutation_worker(root, url, validated, resume, results):
    class PausingBackend(ServerBackend):
        def compare_and_swap(self, channel, expected, state, files):
            if "episodes/episode_a/episode.json" in files:
                validated.set()
                resume.wait(20)
            return super().compare_and_swap(channel, expected, state, files)

    coordinator = SharedCoordinator(Path(root), PausingBackend(url), owner_id="old", lease_seconds=10)
    token = coordinator.acquire("episode_a", "request_1")
    results.put(token)
    try:
        coordinator.commit_mutation(token, {"episodes/episode_a/episode.json": b"validated before close"})
        results.put("COMMITTED")
    except CoordinationError as exc:
        results.put(exc.code)


def _coordinator(root, url, owner="first", **kwargs):
    return SharedCoordinator(root, ServerBackend(url), owner_id=owner, lease_seconds=10, **kwargs)


def test_two_independent_clones_cannot_claim_one_channel(tmp_path):
    context = multiprocessing.get_context("spawn")
    with authority() as (url, data, _):
        start, results = context.Event(), context.Queue()
        processes = [context.Process(target=_claim_worker,
            args=(str(tmp_path / owner), url, owner, slug, start, results))
            for owner, slug in (("one", "episode_a"), ("two", "episode_b"))]
        for process in processes:
            process.start()
        start.set()
        outputs = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert [output[0] for output in outputs].count("won") == 1
        assert data["revision"] == 1
        assert data["states"]["default"]["owner_id"] in {"one", "two"}


def test_live_lease_cannot_be_stolen_and_expiry_uses_server_clock(tmp_path, monkeypatch):
    with authority() as (url, data, lock):
        first = _coordinator(tmp_path / "one", url)
        token = first.acquire("episode_a", "request_1")
        second = _coordinator(tmp_path / "two", url, "second")
        monkeypatch.setattr("time.time", lambda: 1_900_000_000)
        with pytest.raises(CoordinationError, match="SHARED_LEASE_BUSY"):
            second.acquire("episode_a", "request_1")
        with lock:
            data["now"] = token["lease_expires_at"] + 1  # Still inside Date precision grace.
        with pytest.raises(CoordinationError, match="SHARED_LEASE_BUSY"):
            second.acquire("episode_a", "request_1")
        with lock:
            data["now"] += 2
        taken = second.acquire("episode_a", "request_1")
        assert taken["generation"] > token["generation"]
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            first.commit_mutation(token, {"episodes/episode_a/episode.json": b"old"})


def test_expired_slot_requires_same_slug_recovery_and_heartbeat_extends(tmp_path):
    with authority() as (url, data, lock):
        first = _coordinator(tmp_path, url)
        token = first.acquire("episode_a", "request_1")
        with lock:
            data["now"] += 8
        renewed = first.heartbeat(token)
        assert renewed["lease_expires_at"] > token["lease_expires_at"]
        with lock:
            data["now"] += 20
        second = _coordinator(tmp_path / "two", url, "second")
        with pytest.raises(CoordinationError, match="SHARED_SLOT_OCCUPIED"):
            second.acquire("episode_b", "request_2")
        assert second.acquire("episode_a", "request_1")["owner_id"] == "second"


def test_old_process_cannot_complete_after_new_generation_started_publication(tmp_path):
    context = multiprocessing.get_context("spawn")
    with authority() as (url, data, lock):
        ready, resume, results = context.Event(), context.Event(), context.Queue()
        process = context.Process(target=_old_mutation_worker, args=(str(tmp_path / "old"), url, ready, resume, results))
        process.start()
        assert ready.wait(15)
        old = results.get(timeout=10)
        with lock:
            data["now"] = old["lease_expires_at"] + 3
        publisher = _coordinator(tmp_path / "publisher", url, "publisher")
        token = publisher.acquire("episode_a", "request_1")
        token = publisher.set_phase(token, "QUEUED")
        closed = publisher.close_for_publication(token, "a" * 64,
            files={".publication-attempts/episode_a.json": b'{"publisher_started": true}'})
        resume.set()
        assert results.get(timeout=15) == "SHARED_STALE_FENCE"
        process.join(10)
        assert process.exitcode == 0
        assert "episodes/episode_a/episode.json" not in data["files"]
        assert data["files"][".publication-attempts/episode_a.json"] == b'{"publisher_started": true}'
        assert closed["phase"] == "PUBLISHING"


def test_remote_cas_rejects_process_paused_after_final_fence_check(tmp_path):
    context = multiprocessing.get_context("spawn")
    with authority() as (url, data, lock):
        validated, resume, results = context.Event(), context.Event(), context.Queue()
        process = context.Process(target=_validated_mutation_worker,
            args=(str(tmp_path / "old"), url, validated, resume, results))
        process.start()
        assert validated.wait(15)
        old = results.get(timeout=10)
        with lock:
            data["now"] = old["lease_expires_at"] + 3
        publisher = _coordinator(tmp_path / "publisher", url, "publisher")
        token = publisher.set_phase(publisher.acquire("episode_a", "request_1"), "QUEUED")
        publisher.close_for_publication(token, "a" * 64)
        resume.set()
        assert results.get(timeout=15) == "SHARED_CAS_CONFLICT"
        process.join(10)
        assert process.exitcode == 0
        assert "episodes/episode_a/episode.json" not in data["files"]


def test_stale_same_generation_version_is_rejected_and_queue_is_sealed(tmp_path):
    with authority() as (url, data, _):
        client = _coordinator(tmp_path, url)
        old = client.acquire("episode_a", "request_1")
        current = client.commit_mutation(old, {"episodes/episode_a/episode.json": b"approved"})
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            client.commit_mutation(old, {"episodes/episode_a/episode.json": b"stale"})
        current = client.set_phase(current, "QUEUED")
        with pytest.raises(CoordinationError, match="SHARED_MUTATION_CLOSED"):
            client.commit_mutation(current, {"episodes/episode_a/episode.json": b"late"})
        assert data["files"]["episodes/episode_a/episode.json"] == b"approved"


def test_captured_bytes_and_fence_commit_in_one_transaction(tmp_path):
    with authority() as (url, data, _):
        client = _coordinator(tmp_path, url)
        token = client.acquire("episode_a", "request_1")
        path = tmp_path / "authored.json"
        path.write_bytes(b"validated")
        payload = path.read_bytes()
        path.write_bytes(b"changed after validation")
        token = client.commit_mutation(token, {"episodes/episode_a/episode.json": payload})
        assert data["files"]["episodes/episode_a/episode.json"] == b"validated"
        assert token["version"] == data["states"]["default"]["version"]


def test_release_handoff_and_request_change_invalidate_old_tokens(tmp_path):
    with authority() as (url, data, _):
        first = _coordinator(tmp_path / "one", url)
        token = first.acquire("episode_a", "duplicate_1")
        changed = first.change_request(token, "media_1")
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            first.assert_current(token)
        released = first.release(changed)
        second = _coordinator(tmp_path / "two", url, "second")
        taken = second.acquire("episode_a", "media_1")
        assert taken["generation"] > released["generation"]
        handoff = second.handoff(taken, "publisher")
        publisher = _coordinator(tmp_path / "three", url, "publisher")
        assert publisher.assert_current(handoff)["owner_id"] == "publisher"
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            second.assert_current(taken)


def test_expired_uncertain_dispatch_is_terminal_and_late_publisher_fenced(tmp_path):
    with authority() as (url, data, lock):
        first = _coordinator(tmp_path / "one", url)
        queued = first.set_phase(first.acquire("episode_a", "request_1"), "QUEUED")
        second = _coordinator(tmp_path / "two", url, "second")
        with pytest.raises(CoordinationError, match="SHARED_LEASE_BUSY"):
            second.abandon_expired("episode_a", "request_1")
        with lock:
            data["now"] = queued["lease_expires_at"] + 3
        second.abandon_expired("episode_a", "request_1")
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            first.close_for_publication(queued, "b" * 64)
        new = second.acquire("episode_b", "request_2")
        assert new["slug"] == "episode_b"
        with pytest.raises(CoordinationError, match="SHARED_SLUG_CLOSED"):
            first.acquire("episode_a", "request_1")


def test_dead_publisher_recovery_never_reopens_slug_or_overwrites_new_episode(tmp_path):
    with authority() as (url, data, lock):
        first = _coordinator(tmp_path / "one", url)
        queued = first.set_phase(first.acquire("episode_a", "request_1"), "QUEUED")
        publishing = first.close_for_publication(queued, "c" * 64)
        recovery = _coordinator(tmp_path / "two", url, "recovery")
        with pytest.raises(CoordinationError, match="SHARED_LEASE_BUSY"):
            recovery.recover_publication_expired("episode_a", "request_1")
        with lock:
            data["now"] = publishing["lease_expires_at"] + 3
        terminal = recovery.recover_publication_expired("episode_a", "request_1")
        assert terminal["phase"] == "PUBLISH_UNCERTAIN"
        assert recovery.recover_publication_expired("episode_a", "request_1") == terminal
        recovery.acquire("episode_b", "request_2")
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            recovery.recover_publication_expired("episode_a", "request_1")
        assert recovery.status("episode_b")["slug"] == "episode_b"


def test_finish_response_loss_is_idempotent_and_closed_slug_stays_closed(tmp_path):
    with authority() as (url, data, _):
        client = _coordinator(tmp_path, url)
        token = client.acquire("episode_a", "request_1")
        terminal = client.finish(token, "REJECTED")
        assert client.finish(token, "REJECTED") == terminal
        assert data["revision"] == 2
        with pytest.raises(CoordinationError, match="SHARED_SLUG_CLOSED"):
            client.acquire("episode_a", "request_2")
        assert client.acquire("episode_b", "request_2")["phase"] == "AUTHORING"


def test_github_ref_update_is_nonforce_parent_cas_and_clock_is_authoritative(tmp_path):
    backend = GitHubRefBackend(tmp_path, session=Mock())
    responses = [Mock(status_code=200, json=lambda: {"tree": {"sha": "tree0"}}),
                 Mock(status_code=201, json=lambda: {"sha": "blob1"}),
                 Mock(status_code=201, json=lambda: {"sha": "tree1"}),
                 Mock(status_code=201, json=lambda: {"sha": "commit1"}),
                 Mock(status_code=200, json=lambda: {"object": {"sha": "parent0"}}),
                 Mock(status_code=409)]
    backend._request = Mock(side_effect=responses)
    with pytest.raises(CASConflict):
        backend.compare_and_swap("default", "parent0", {"generation": 2, "released": True}, {})
    calls = backend._request.call_args_list
    assert calls[3].kwargs["json"]["parents"] == ["parent0"]
    assert calls[5].kwargs["json"] == {"sha": "commit1", "force": False}
    with pytest.raises(CoordinationError, match="SHARED_CLOCK_UNAVAILABLE"):
        backend._time(Mock(headers={}))


def test_malformed_state_and_control_path_changes_fail_closed(tmp_path):
    with authority() as (url, data, lock):
        client = _coordinator(tmp_path, url)
        token = client.acquire("episode_a", "request_1")
        for path in ("../secret", ".", ".pipeline", ".git/config", ".pipeline/coordination/default.json",
                     ".pipeline/closed/episode_a.json", ".publication-attempts", ".publication-attempts/episode_a.json"):
            with pytest.raises(CoordinationError):
                client.commit_mutation(token, {path: b"overwrite"})
        with lock:
            data["states"]["default"]["generation"] = "invalid"
        with pytest.raises(CoordinationError, match="SHARED_STATE_INVALID"):
            client.acquire("episode_b", "request_2")


def test_historical_tombstones_remain_exact_and_index_stays_bounded(tmp_path):
    with authority() as (url, data, _):
        client = _coordinator(tmp_path, url)
        for number in range(8):
            slug = f"episode_{number}"
            client.finish(client.acquire(slug, f"request_{number}"), "REJECTED")
            assert len(data["states"]["default"]["closed_slugs"]) == 1
        assert len([path for path in data["files"] if path.startswith(".pipeline/closed/")]) == 8
        assert client.status("episode_0")["requested_slug_closed"]["reason"] == "REJECTED"
        with pytest.raises(CoordinationError, match="SHARED_SLUG_CLOSED"):
            client.acquire("episode_0", "new_request")


def test_terminal_phase_persists_files_and_queued_recheck_is_read_only(tmp_path):
    with authority() as (url, data, _):
        client = _coordinator(tmp_path, url)
        token = client.acquire("episode_a", "request_1")
        token = client.set_phase(token, "QUEUED")
        assert client.set_phase(token, "QUEUED") == token
        assert data["revision"] == 2
        done = client.set_phase(token, "REJECTED", {".pipeline/episodes/episode_a.json": b"rejected"})
        assert done["phase"] == "REJECTED"
        assert data["files"][".pipeline/episodes/episode_a.json"] == b"rejected"


def test_dispatch_uses_authoritative_generation_and_owner(tmp_path):
    with authority() as (url, _, _):
        client = _coordinator(tmp_path, url)
        queued = client.set_phase(client.acquire("episode_a", "request_1"), "QUEUED")
        state = {"shared_generation": queued["generation"]}
        assert client.validate_dispatch("episode_a", "request_1", state) == queued
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            client.validate_dispatch("episode_a", "request_1", {"shared_generation": -1})
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            _coordinator(tmp_path / "other", url, "other").validate_dispatch("episode_a", "request_1", state)


def test_publication_stage_is_atomic_and_late_sender_is_fenced(tmp_path):
    with authority() as (url, data, lock):
        client = _coordinator(tmp_path, url)
        queued = client.set_phase(client.acquire("episode_a", "request_1"), "QUEUED")
        publishing = client.close_for_publication(queued, "a" * 64)
        path = ".publication-attempts/episode_a.json"
        active = client.update_publication(publishing, {path: b"sending"})
        assert data["files"][path] == b"sending"
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            client.update_publication(publishing, {path: b"old_stage"})
        with lock:
            data["now"] = active["lease_expires_at"] + 3
        recovery = _coordinator(tmp_path / "other", url, "recovery")
        recovery.recover_publication_expired("episode_a", "request_1", files={path: b"uncertain"})
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            client.update_publication(active, {path: b"late_send"})
        assert data["files"][path] == b"uncertain"


def test_abandoned_dispatch_response_loss_cannot_close_next_episode(tmp_path):
    with authority() as (url, data, lock):
        client = _coordinator(tmp_path, url)
        queued = client.set_phase(client.acquire("episode_a", "request_1"), "QUEUED")
        with lock:
            data["now"] = queued["lease_expires_at"] + 3
        client.abandon_expired("episode_a", "request_1", expected_generation=queued["generation"])
        next_token = client.acquire("episode_b", "request_2")
        proof = client.abandon_expired("episode_a", "request_1", expected_generation=queued["generation"])
        assert proof["requested_slug_closed"]["request_id"] == "request_1"
        assert client.assert_current(next_token) == next_token


def test_read_files_is_snapshot_exact_and_override_is_explicit(tmp_path):
    from engine.shared_coordination import coordinator_for, override_for, using_coordinator
    with authority() as (url, _, _):
        client = _coordinator(tmp_path, url)
        token = client.acquire("episode_a", "request_1")
        first = client.commit_mutation(token, {"episodes/episode_a/episode.json": b"first"})
        client.commit_mutation(first, {"episodes/episode_a/episode.json": b"second"})
        assert client.read_files(["episodes/episode_a/episode.json"], first["revision"]) == {"episodes/episode_a/episode.json": b"first"}
        assert override_for(tmp_path) is None
        with using_coordinator(tmp_path, client):
            assert coordinator_for(tmp_path) is client
            assert override_for(tmp_path) is client
        assert override_for(tmp_path) is None


def test_github_slow_blob_preparation_cannot_commit_expired_lease(tmp_path):
    from email.utils import formatdate
    backend = GitHubRefBackend(tmp_path, session=Mock())
    backend._request = Mock(side_effect=[
        Mock(status_code=200, json=lambda: {"tree": {"sha": "tree0"}}),
        Mock(status_code=201, json=lambda: {"sha": "blob1"}),
        Mock(status_code=201, json=lambda: {"sha": "tree1"}),
        Mock(status_code=201, json=lambda: {"sha": "commit1"}),
        Mock(status_code=200, headers={"Date": formatdate(1800000030, usegmt=True)},
             json=lambda: {"object": {"sha": "parent0"}})])
    with pytest.raises(CoordinationError, match="SHARED_LEASE_EXPIRED"):
        backend.compare_and_swap("default", "parent0", {"generation": 2, "lease_expires_at": 1800000020}, {})
    assert not any(call.args[0] == "PATCH" for call in backend._request.call_args_list)


def test_owner_identity_creation_is_atomic_and_corruption_fails_closed(tmp_path):
    from engine.shared_coordination import _owner
    owner = _owner(tmp_path)
    assert _owner(tmp_path) == owner
    assert not list((tmp_path / ".pipeline").glob(".owner-*"))
    (tmp_path / ".pipeline/owner.json").write_text('{"owner_id":', encoding="utf-8")
    with pytest.raises(CoordinationError, match="SHARED_OWNER_INVALID"):
        _owner(tmp_path)


def test_git_backend_exact_content_snapshot_and_successful_cas(tmp_path):
    from email.utils import formatdate
    backend = GitHubRefBackend(tmp_path, session=Mock())
    backend._request = Mock(side_effect=[
        Mock(status_code=200, json=lambda: {"encoding": "base64", "type": "file", "content": base64.b64encode(b"approved").decode()}),
        Mock(status_code=404)])
    assert backend.read_files(["episodes/episode_a/episode.json", "missing.json"], "exactsha") == {
        "episodes/episode_a/episode.json": b"approved", "missing.json": None}
    assert all(call.kwargs["params"] == {"ref": "exactsha"} for call in backend._request.call_args_list)
    backend._request = Mock(side_effect=[
        Mock(status_code=200, json=lambda: {"tree": {"sha": "tree0"}}),
        Mock(status_code=201, json=lambda: {"sha": "payloadblob"}),
        Mock(status_code=201, json=lambda: {"sha": "stateblob"}),
        Mock(status_code=201, json=lambda: {"sha": "tree1"}),
        Mock(status_code=201, json=lambda: {"sha": "commit1"}),
        Mock(status_code=200, headers={"Date": formatdate(1800000001, usegmt=True)},
             json=lambda: {"object": {"sha": "parent0"}}),
        Mock(status_code=200, headers={"Date": formatdate(1800000002, usegmt=True)}, json=lambda: {})])
    result = backend.compare_and_swap("default", "parent0", {"generation": 2, "lease_expires_at": 1800000100},
                                      {"episodes/episode_a/episode.json": b"approved"})
    assert result == ("commit1", 1800000002)
    calls = backend._request.call_args_list
    assert base64.b64decode(calls[1].kwargs["json"]["content"]) == b"approved"
    assert calls[3].kwargs["json"]["tree"] == [
        {"path": "episodes/episode_a/episode.json", "mode": "100644", "type": "blob", "sha": "payloadblob"},
        {"path": ".pipeline/coordination/default.json", "mode": "100644", "type": "blob", "sha": "stateblob"}]
    assert calls[4].kwargs["json"]["parents"] == ["parent0"]
    assert calls[6].kwargs["json"] == {"sha": "commit1", "force": False}


def test_recovery_cannot_overwrite_a_newer_send_stage(tmp_path):
    with authority() as (url, data, lock):
        sender = _coordinator(tmp_path, url)
        queued = sender.set_phase(sender.acquire("episode_a", "request_1"), "QUEUED")
        prepared = sender.close_for_publication(queued, "a" * 64)
        marker = ".publication-attempts/episode_a.json"
        sending = sender.update_publication(prepared, {marker: b"sending"})
        with lock:
            data["now"] = sending["lease_expires_at"] + 3
        recovery = _coordinator(tmp_path / "other", url, "recovery")
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            recovery.recover_publication_expired("episode_a", "request_1", expected_version=prepared["version"],
                                                 files={marker: b"confirmed_not_sent"})
        assert data["files"][marker] == b"sending"
        assert recovery.recover_publication_expired("episode_a", "request_1", expected_version=sending["version"],
                                                    files={marker: b"uncertain"})["phase"] == "PUBLISH_UNCERTAIN"


def test_historical_receipt_cas_preserves_current_episode_fence(tmp_path):
    with authority() as (url, data, _):
        client = _coordinator(tmp_path, url)
        token = client.set_phase(client.acquire("episode_a", "request_1"), "QUEUED")
        publishing = client.close_for_publication(token, "a" * 64)
        client.finish(publishing, "PUBLISH_UNCERTAIN")
        next_episode = client.acquire("episode_b", "request_2")
        current = deepcopy(data["states"]["default"])
        marker = ".publication-attempts/episode_a.json"
        client.update_closed_publication("episode_a", "request_1", {marker: b"confirmed_sent"}, next_episode["revision"])
        assert data["states"]["default"] == current
        assert client.assert_current(next_episode)["version"] == next_episode["version"]
        with pytest.raises(CASConflict):
            client.update_closed_publication("episode_a", "request_1", {marker: b"stale"}, next_episode["revision"])
        assert data["files"][marker] == b"confirmed_sent"


def test_only_same_publisher_owner_and_fence_can_resume_expired_session(tmp_path):
    with authority() as (url, data, lock):
        publisher = _coordinator(tmp_path, url)
        token = publisher.set_phase(publisher.acquire("episode_a", "request_1"), "QUEUED")
        publishing = publisher.close_for_publication(token, "a" * 64)
        with lock:
            data["now"] = publishing["lease_expires_at"] + 3
        other = _coordinator(tmp_path / "other", url, "other")
        kwargs = {"expected_generation": publishing["generation"], "expected_version": publishing["version"]}
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            other.resume_publication("episode_a", "request_1", **kwargs)
        resumed = publisher.resume_publication("episode_a", "request_1", **kwargs)
        assert resumed["generation"] > publishing["generation"]
        assert data["states"]["default"]["mutation_allowed"] is False
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            publisher.update_publication(publishing, {".publication-attempts/episode_a.json": b"stale"})
        terminal = publisher.finish(resumed, "PUBLISH_UNCERTAIN")
        with pytest.raises(CoordinationError, match="SHARED_PUBLICATION_NOT_STARTED"):
            publisher.resume_publication("episode_a", "request_1", expected_generation=terminal["generation"],
                                          expected_version=terminal["version"])


def test_slug_scope_and_unknown_phases_cannot_bypass_fence(tmp_path):
    with authority() as (url, data, _):
        client = _coordinator(tmp_path, url)
        token = client.acquire("episode_a", "request_1")
        for path in ("episodes/episode_b/story.txt", ".pipeline/episodes/episode_b.json",
                     ".publish-queue/episode_b.txt", ".episode-check/episode_b--request_1.json",
                     ".github/workflows/publish-episode.yml", ".pipeline/state.json"):
            with pytest.raises(CoordinationError, match="SHARED_CONTROL_PATH_FORBIDDEN"):
                client.commit_mutation(token, {path: b"unrelated"})
        with pytest.raises(CoordinationError, match="SHARED_PHASE_INVALID"):
            client.set_phase(token, "typo")
        queued = client.set_phase(token, "QUEUED")
        with pytest.raises(CoordinationError, match="SHARED_CONTROL_PATH_FORBIDDEN"):
            client.close_for_publication(queued, "a" * 64, {"episodes/episode_a/story.txt": b"late edit"})
        assert data["states"]["default"]["phase"] == "QUEUED"
