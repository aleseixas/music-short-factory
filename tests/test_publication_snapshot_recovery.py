"""Approved bytes and crash recovery across independent publisher processes."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
from unittest.mock import Mock, patch

import pytest
import requests

from engine.coordination_runtime import save_token
from engine.pipeline_state import PipelineStore, atomic_json
from engine.shared_coordination import CoordinationError, SharedCoordinator, using_coordinator
from publishing.attempts import AttemptStore, PublicationLocked
from publishing.base import PublishResult, PublishingError
from publishing.recovery import classify_observation
from publishing.snapshot import approved_snapshot
from tests.test_publishing import valid_post
from tests.test_shared_coordination import ServerBackend, _queue, authority

pytestmark = pytest.mark.distributed_coordination
FINGERPRINT = "f" * 64


def make_bundle(root, post=None):
    post = valid_post() if post is None else post
    contents = {
        "video": ("output/demo.mp4", b"approved video bytes"),
        "cover": ("output/demo_cover.jpg", b"approved cover bytes"),
        "post": ("episodes/demo/post.json", json.dumps(post).encode()),
    }
    bundle = root / ".publish-ready"
    files = []
    for role, (name, content) in contents.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files.append({"role": role, "path": name, "size_bytes": len(content),
                      "sha256": hashlib.sha256(content).hexdigest()})
    atomic_json(bundle / "manifest.json", {"schema_version": 1, "episode": "demo", "media": {},
        "source": {"run_id": "123", "sha": "a" * 40, "request_id": "request_1"}, "files": files})
    return contents


def queued(root):
    # Build the local observation fixture separately; the live grant below
    # always comes from the independently hosted shared authority.
    with patch("engine.coordination_runtime._factory", return_value=None):
        store = PipelineStore(root)
        store.start("demo", "request_1")
        for stage in ("UNIQUE", "AUTHORING", "LOCAL_VALIDATION"):
            store.transition("demo", stage)
        store.transition("demo", "MEDIA_PREFLIGHT", local_preflight_passed=True, run_id="123")
        store.transition("demo", "READY_TO_QUEUE")
        store.transition("demo", "QUEUED")


class ServerAttempts(AttemptStore):
    """Uses production transitions and the same remote CAS as every clone."""
    def __init__(self, root, url):
        super().__init__(root, allow_local=True)
        self.url = url



def crash_worker(root_name, url, stage, ready=None, resume=None, result_queue=None):
    root = Path(root_name)
    queued(root)
    coordinator = SharedCoordinator(root, ServerBackend(url), owner_id="worker", lease_seconds=10)
    with using_coordinator(root, coordinator):
        token = coordinator.acquire("demo", "request_1")
        token = _queue(coordinator, token)
        save_token(root, token)
        store = ServerAttempts(root, url)
        store.claim("demo", "youtube", "local:crashed", payload_fingerprint=FINGERPRINT)
        if stage == "paused":
            ready.set()
            resume.wait(20)
            try:
                store.mark_sending("demo", "youtube", "local:crashed")
                result_queue.put("UPLOAD_ALLOWED")
            except (CoordinationError, PublicationLocked, RuntimeError) as exc:
                result_queue.put(str(exc))
            return
        if stage in {"sending", "uploaded", "receipt"}:
            store.mark_sending("demo", "youtube", "local:crashed")
        if stage in {"uploaded", "receipt"}:
            store.record_uploaded("demo", "youtube", "local:crashed", {"video_id": "remote-video"})
        if stage == "receipt":
            store.record_receipt("demo", "youtube", "local:crashed", PublishResult("youtube", "uploaded", "remote-video"))
    os._exit(71)  # No finally block, completion callback, or local cleanup.


def change_source_worker(name):
    Path(name).write_bytes(b"changed by an independent authoring process")


def test_snapshot_keeps_approved_bytes_when_source_changes_in_another_process(tmp_path):
    contents = make_bundle(tmp_path)
    with approved_snapshot(tmp_path, "demo", "123", "request_1") as snapshot:
        process = multiprocessing.get_context("spawn").Process(target=change_source_worker,
            args=(str(tmp_path / ".publish-ready/output/demo.mp4"),))
        process.start()
        process.join(15)
        assert process.exitcode == 0
        for platform in ("youtube", "instagram", "facebook", "tiktok"):
            context = snapshot.context(platform)
            assert context.video_path.read_bytes() == contents["video"][1]
            assert context.cover_path.read_bytes() == contents["cover"][1]
            assert not context.video_path.is_relative_to(tmp_path)
        directory = snapshot.directory
    assert not directory.exists()


def test_change_during_copy_is_rejected_before_any_send(tmp_path, monkeypatch):
    make_bundle(tmp_path)
    original = shutil.copyfileobj
    def altered_copy(source, destination, length):
        original(source, destination, length)
        destination.write(b"changed between inspection and snapshot")
    monkeypatch.setattr("publishing.snapshot.shutil.copyfileobj", altered_copy)
    with pytest.raises(PublishingError, match="SNAPSHOT_BYTES_CHANGED"):
        with approved_snapshot(tmp_path, "demo", "123", "request_1"):
            pytest.fail("unapproved bytes reached the upload boundary")


def test_external_media_override_cannot_bypass_snapshot(tmp_path):
    post = valid_post()
    post["instagram"]["video_url"] = "https://example.invalid/unapproved.mp4"
    make_bundle(tmp_path, post)
    with approved_snapshot(tmp_path, "demo", "123", "request_1") as snapshot:
        with pytest.raises(PublishingError, match="EXTERNAL_BYTES_UNVERIFIED"):
            snapshot.context("instagram")


@pytest.mark.parametrize("stage,observed,expected", [
    ("prepared", "uncertain", "confirmed_not_sent"),
    ("sending", "confirmed_not_sent", "uncertain"),
    ("uploaded", "confirmed_sent", "confirmed_sent"),
    ("receipt", "uncertain", "confirmed_sent"),
])
def test_killed_local_publisher_reconciles_without_resend(tmp_path, stage, observed, expected):
    context = multiprocessing.get_context("spawn")
    with authority() as (url, data, lock):
        worker = context.Process(target=crash_worker, args=(str(tmp_path / "worker"), url, stage))
        worker.start()
        worker.join(20)
        assert worker.exitcode == 71
        marker = json.loads(data["files"][".publication-attempts/demo.json"])
        attempt = marker["platforms"]["youtube"]
        assert attempt["attempt_id"] and attempt["started_at"] and attempt["platform"] == "youtube"
        assert attempt["payload_fingerprint"] == FINGERPRINT
        assert "idempotency_key" in attempt and marker["run_id"] == ""
        with lock:
            data["now"] = data["states"]["default"]["lease_expires_at"] + 3
        root = tmp_path / "recovery"
        coordinator = SharedCoordinator(root, ServerBackend(url), owner_id="recovery", lease_seconds=10)
        with using_coordinator(root, coordinator):
            store = ServerAttempts(root, url)
            observer = Mock(return_value=observed)
            result = store.reconcile("demo", observer)
            assert result["platforms"]["youtube"]["status"] == expected
            assert result["stage"] == ("PUBLISHED" if expected == "confirmed_sent" else "PUBLISH_UNCERTAIN")
            assert data["states"]["default"]["released"] is True
            with pytest.raises(PublicationLocked, match="ALREADY_ATTEMPTED"):
                store.claim("demo", "youtube", "local:new", payload_fingerprint=FINGERPRINT)
            result_again = store.reconcile("demo", observer)
            assert result_again["platforms"]["youtube"]["attempt_id"] == attempt["attempt_id"]
            if stage in {"prepared", "receipt"}:
                observer.assert_not_called()


def test_recovery_fences_paused_worker_before_it_can_mark_sending(tmp_path):
    context = multiprocessing.get_context("spawn")
    with authority() as (url, data, lock):
        ready, resume, results = context.Event(), context.Event(), context.Queue()
        worker = context.Process(target=crash_worker,
            args=(str(tmp_path / "worker"), url, "paused", ready, resume, results))
        worker.start()
        try:
            assert ready.wait(15)
            root = tmp_path / "recovery"
            coordinator = SharedCoordinator(root, ServerBackend(url), owner_id="recovery", lease_seconds=10)
            with using_coordinator(root, coordinator):
                store = ServerAttempts(root, url)
                with pytest.raises(CoordinationError, match="SHARED_LEASE_BUSY"):
                    store.reconcile("demo", Mock(return_value="uncertain"))
                assert data["states"]["default"]["phase"] == "PUBLISHING"
                with lock:
                    data["now"] = data["states"]["default"]["lease_expires_at"] + 3
                store.reconcile("demo", Mock(return_value="uncertain"))
                resume.set()
                result = results.get(timeout=15)
                assert "UPLOAD_ALLOWED" not in result
                assert "PUBLICATION_SESSION_CLOSED" in result or "SHARED_STALE_FENCE" in result
        finally:
            resume.set()
            worker.join(10)
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
        assert worker.exitcode == 0


def test_async_receipt_does_not_mark_episode_published(tmp_path):
    queued(tmp_path)
    with patch("engine.coordination_runtime._factory", return_value=None):
        store = AttemptStore(tmp_path, allow_local=True)
        store.claim("demo", "facebook", "local:first")
        store.mark_sending("demo", "facebook", "local:first")
        store.record_receipt("demo", "facebook", "local:first", PublishResult("facebook", "processing", "video-id"))
        result = store.complete("demo", "local:first", success=True)
        assert result["stage"] == "PUBLISH_UNCERTAIN"
        assert result["platforms"]["facebook"]["status"] == "uncertain"


@pytest.mark.parametrize("platform,payload,expected", [
    ("youtube", {"id": "video-id"}, "confirmed_sent"),
    ("instagram", {"status_code": "PUBLISHED"}, "confirmed_sent"),
    ("instagram", {"status_code": "FINISHED"}, "uncertain"),
    ("facebook", {"status": {"publishing_phase": {"status": "complete"}}}, "confirmed_sent"),
    ("tiktok", {"status": "SEND_TO_USER_INBOX"}, "confirmed_sent"),
    ("youtube", {}, "uncertain"),
])
def test_platform_observations_require_positive_delivery(platform, payload, expected):
    assert classify_observation(platform, payload) == expected


def test_manifest_must_match_exact_shared_approval(tmp_path):
    from publishing.snapshot import approved_manifest
    make_bundle(tmp_path)
    manifest_path = tmp_path / ".publish-ready/manifest.json"
    approved = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    with authority() as (url, data, lock):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id="author", lease_seconds=10)
        token = coordinator.acquire("demo", "request_1")
        token = coordinator.commit_mutation(token, {".pipeline/artifacts/demo.json": json.dumps({
            "slug": "demo", "bundle_approved": True, "files": {".publish-ready/manifest.json": approved}}).encode()})
        token = _queue(coordinator, token)
        with using_coordinator(tmp_path, coordinator):
            with approved_snapshot(tmp_path, "demo", "123", "request_1") as snapshot:
                assert approved_manifest(tmp_path, "demo", "123", "request_1")[1] == snapshot.fingerprint
            forged = json.loads(manifest_path.read_bytes())
            forged["files"][0]["sha256"] = "0" * 64
            atomic_json(manifest_path, forged)
            with pytest.raises(PublishingError, match="SNAPSHOT_NOT_APPROVED"):
                approved_manifest(tmp_path, "demo", "123", "request_1")


def test_dry_run_uses_bundle_without_restored_outputs(tmp_path):
    from publish import main
    from publishing.credentials import CredentialStore
    make_bundle(tmp_path)
    queued(tmp_path)
    publisher = Mock()
    publisher.dry_run.return_value = PublishResult("youtube", "validated", dry_run=True)
    with patch("publish.CredentialStore.load", return_value=CredentialStore.from_mapping({})), patch("publish.create_publisher", return_value=publisher):
        assert main(["demo", "--platform", "youtube", "--dry-run", "--project-root", str(tmp_path)]) == 0
    context = publisher.dry_run.call_args.args[0]
    assert not context.video_path.is_relative_to(tmp_path)
    assert not (tmp_path / "output/demo.mp4").exists()
    publisher.upload.assert_not_called()


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_preflight_waits_only_for_exact_source_run_and_never_dispatches(tmp_path, event):
    queued(tmp_path)
    queue = tmp_path / ".publish-queue/demo.txt"
    queue.parent.mkdir(parents=True)
    queue.write_text("demo\n123\nrequest_1\n", encoding="utf-8")
    run = {"id": 123, "name": "Episode media preflight", "status": "completed", "conclusion": "success", "event": event, "head_branch": "main",
           "path": ".github/workflows/episode-media-preflight.yml",
           "head_sha": "a" * 40 if event == "push" else "b" * 40,
           "display_title": "media/demo/request_1/" + "a" * 40}
    receipt = {"slug": "demo", "request_id": "request_1", "local_preflight_passed": True,
               "workflow": "episode-media-preflight.yml"}
    http = Mock()
    http.request.side_effect = [Mock(status_code=200, json=lambda: {**run, "status": "in_progress"}),
                               Mock(status_code=200, json=lambda: run),
                               Mock(status_code=200, json=lambda: {"content": base64.b64encode(json.dumps(receipt).encode()).decode()})]
    with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "fixture"}), patch("publishing.snapshot.approved_manifest", return_value=({}, FINGERPRINT)), patch("publishing.attempts.time.sleep") as sleep:
        store = AttemptStore(tmp_path, http)
        store._verify_live_preflight("demo", {"request_id": "request_1", "run_id": "123", "commit_sha": "a" * 40}, FINGERPRINT)
    sleep.assert_called_once_with(5)
    assert all(call.args[0] == "GET" for call in http.request.call_args_list)
    assert all(call.args[1].endswith("/actions/runs/123") for call in http.request.call_args_list[:2])


def test_gate_rejects_replaced_manifest_after_snapshot(tmp_path):
    queued(tmp_path)
    queue = tmp_path / ".publish-queue/demo.txt"
    queue.parent.mkdir(parents=True)
    queue.write_text("demo\n123\nrequest_1\n", encoding="utf-8")
    http = Mock()
    with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "fixture"}), patch("publishing.snapshot.approved_manifest", return_value=({}, "a" * 64)):
        store = AttemptStore(tmp_path, http)
        with pytest.raises(PublicationLocked, match="PREFLIGHT_UNVERIFIED"):
            store._verify_live_preflight("demo", {"request_id": "request_1", "run_id": "123", "commit_sha": "a" * 40}, FINGERPRINT)
    http.request.assert_not_called()


def test_live_cli_rejects_successful_run_with_different_commit(tmp_path):
    queued(tmp_path)
    queue = tmp_path / ".publish-queue/demo.txt"
    queue.parent.mkdir(parents=True)
    queue.write_text("demo\n123\nrequest_1\n", encoding="utf-8")
    run = {"id": 123, "status": "completed", "conclusion": "success", "event": "push", "head_branch": "main",
           "path": ".github/workflows/episode-media-preflight.yml", "head_sha": "b" * 40}
    http = Mock()
    http.request.return_value = Mock(status_code=200, json=lambda: run)
    with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "fixture"}), patch("publishing.snapshot.approved_manifest", return_value=({}, FINGERPRINT)):
        with pytest.raises(PublicationLocked, match="PREFLIGHT_UNVERIFIED"):
            AttemptStore(tmp_path, http)._verify_live_preflight("demo", {
                "request_id": "request_1", "run_id": "123", "commit_sha": "a" * 40}, FINGERPRINT)
    assert http.request.call_count == 1
    assert http.request.call_args.args[0] == "GET"
