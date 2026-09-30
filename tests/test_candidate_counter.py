"""Candidate limits use one shared CAS window across independent clones."""
from __future__ import annotations

import multiprocessing
from pathlib import Path
import subprocess

import pytest

from engine.pipeline_state import atomic_json
from engine.shared_coordination import CASConflict, CoordinationError, SharedCoordinator, using_coordinator
from scripts.pipeline_workflow import _legacy_candidate_ids, duplicate_guard
from tests.test_shared_coordination import ServerBackend, authority


pytestmark = pytest.mark.distributed_coordination


def _candidate_worker(root, url, owner, slug, request_id, start, results):
    coordinator = SharedCoordinator(Path(root), ServerBackend(url), owner_id=owner, lease_seconds=10)
    start.wait(10)
    for _ in range(5):
        status = coordinator.status(slug)
        missing = "candidate_window" not in status
        try:
            decision = coordinator.reserve_candidate(
                slug, request_id, migration_ids=[] if missing else None,
                migration_revision=status["revision"] if missing else None,
            )
            results.put((decision["result"], decision["attempts"]))
            return
        except CASConflict:
            continue
    results.put(("CAS_FAILED", 0))


def test_two_independent_clones_count_only_the_winning_candidate(tmp_path):
    with authority() as (url, data, lock):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        workers = [context.Process(target=_candidate_worker,
                   args=(tmp_path, url, f"clone-{index}", f"candidate_{index}",
                         f"request_{index}", start, results)) for index in (1, 2)]
        for worker in workers:
            worker.start()
        start.set()
        observed = [results.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(10)
            assert worker.exitcode == 0
        assert sorted(result for result, _ in observed) == ["OPEN", "RESUME_EXISTING_EPISODE"]
        with lock:
            assert len(data["states"]["default"]["candidate_window"]["request_ids"]) == 1


def test_queue_resets_global_window_in_the_same_cas(tmp_path):
    with authority() as (url, data, lock):
        first = SharedCoordinator(tmp_path / "first", ServerBackend(url), owner_id="first", lease_seconds=10)
        second = SharedCoordinator(tmp_path / "second", ServerBackend(url), owner_id="second", lease_seconds=10)
        for index in range(14):
            result = first.reserve_candidate(f"candidate_{index}", f"request_{index}",
                                             migration_ids=[] if index == 0 else None,
                                             migration_revision="0" if index == 0 else None)
            assert result["result"] == "OPEN"
            first.finish(result["token"], "REJECTED")
        last = first.reserve_candidate("candidate_final", "request_final")
        assert last["attempts"] == 15
        blocked = second.reserve_candidate("candidate_over_limit", "request_over_limit")
        assert blocked["result"] == "RESUME_EXISTING_EPISODE"
        queued = first.set_phase(last["token"], "QUEUED", files={
            ".publish-queue/candidate_final.txt": b"candidate_final\n123\nrequest_final\n"})
        with lock:
            assert data["states"]["default"]["candidate_window"]["request_ids"] == []
            assert data["files"][".publish-queue/candidate_final.txt"].startswith(b"candidate_final")
        first.finish(queued, "CANCELLED")
        fresh = second.reserve_candidate("candidate_after_queue", "request_after_queue")
        assert fresh["result"] == "OPEN"
        assert fresh["attempts"] == 1


def test_queue_without_exact_remote_marker_cannot_reset_candidate_window(tmp_path):
    with authority() as (url, _, _):
        owner = SharedCoordinator(tmp_path / "owner", ServerBackend(url), owner_id="owner", lease_seconds=10)
        claim = owner.reserve_candidate("candidate_a", "request_a", migration_ids=[], migration_revision="0")
        before = owner.status("candidate_a")
        with pytest.raises(CoordinationError, match="SHARED_QUEUE_EVIDENCE_REQUIRED"):
            owner.set_phase(claim["token"], "QUEUED")
        after = owner.status("candidate_a")
        assert after["revision"] == before["revision"]
        assert after["candidate_window"]["request_ids"] == ["request_a"]
        assert after["phase"] == "CANDIDATE"


def test_expired_same_candidate_takeover_keeps_one_attempt(tmp_path):
    with authority() as (url, data, lock):
        first = SharedCoordinator(tmp_path / "first", ServerBackend(url), owner_id="first", lease_seconds=10)
        second = SharedCoordinator(tmp_path / "second", ServerBackend(url), owner_id="second", lease_seconds=10)
        claimed = first.reserve_candidate("candidate_a", "request_a", migration_ids=[], migration_revision="0")
        assert second.reserve_candidate("candidate_a", "request_a")["result"] == "RESUME_EXISTING_EPISODE"
        with lock:
            data["now"] += 13
        taken = second.reserve_candidate("candidate_a", "request_a")
        assert taken["result"] == "OPEN"
        assert taken["attempts"] == 1
        assert taken["token"]["generation"] > claimed["token"]["generation"]


def test_stale_migration_revision_cannot_reintroduce_old_counter(tmp_path):
    with authority() as (url, _, _):
        client = SharedCoordinator(tmp_path, ServerBackend(url), owner_id="clone", lease_seconds=10)
        client.backend.compare_and_swap("default", "0", client._empty("default"), {})
        with pytest.raises(CASConflict):
            client.reserve_candidate("candidate_a", "request_a",
                                     migration_ids=["old_request"], migration_revision="0")


def test_fifteenth_attempt_is_last_allowed_across_clones(tmp_path):
    with authority() as (url, data, lock):
        first = SharedCoordinator(tmp_path / "first", ServerBackend(url), owner_id="first", lease_seconds=10)
        second = SharedCoordinator(tmp_path / "second", ServerBackend(url), owner_id="second", lease_seconds=10)
        for index in range(15):
            result = first.reserve_candidate(f"candidate_{index}", f"request_{index}",
                                             migration_ids=[] if index == 0 else None,
                                             migration_revision="0" if index == 0 else None)
            assert result["result"] == "OPEN"
            first.finish(result["token"], "REJECTED")
        result = second.reserve_candidate("candidate_16", "request_16")
        assert result == {"result": "CANDIDATE_LIMIT_REACHED", "attempts": 16, "token": None}
        with lock:
            assert len(data["states"]["default"]["candidate_window"]["request_ids"]) == 15


def test_new_clone_ignores_exhausted_local_cache_after_authoritative_queue(tmp_path):
    with authority() as (url, _, _):
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        first_root.mkdir()
        second_root.mkdir()
        first = SharedCoordinator(first_root, ServerBackend(url), owner_id="first", lease_seconds=10)
        second = SharedCoordinator(second_root, ServerBackend(url), owner_id="second", lease_seconds=10)
        first.reserve_candidate("candidate_a", "request_a", migration_ids=[], migration_revision="0")
        with using_coordinator(first_root, first):
            assert duplicate_guard(first_root, "candidate_a", "request_a", "default") == "OPEN"
        token = first.status("candidate_a")["token"]
        token = first.set_phase(token, "QUEUED", files={
            ".publish-queue/candidate_a.txt": b"candidate_a\n123\nrequest_a\n"})
        first.finish(token, "CANCELLED")
        atomic_json(second_root / ".pipeline/candidates-default.json", {
            "schema_version": 1, "request_ids": [f"stale_{i}" for i in range(16)],
            "exhausted": True,
        })
        with using_coordinator(second_root, second):
            assert duplicate_guard(second_root, "candidate_b", "request_b", "default") == "OPEN"
        assert second.status("candidate_b")["candidate_window"]["request_ids"] == ["request_b"]


def test_one_time_migration_uses_exact_queue_boundary_and_request_order(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def commit(path, body, message):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        subprocess.run(["git", "add", "--", path], cwd=tmp_path, check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", message], cwd=tmp_path, check=True)

    commit(".duplicate-check/z_first.json", '{"request_id":"z_first"}', "first request")
    commit(".duplicate-check/a_second.json", '{"request_id":"a_second"}', "second request")
    before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    assert _legacy_candidate_ids(tmp_path, "default", before) == ["z_first", "a_second"]
    commit(".publish-queue/finished.txt", "finished\n123\nold_request\n", "queue boundary")
    commit(".duplicate-check/new.json", '{"request_id":"new_request"}', "new request")
    after = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    assert _legacy_candidate_ids(tmp_path, "default", after) == ["new_request"]


def test_expired_orphan_claim_is_recovered_without_losing_candidate_count(tmp_path):
    with authority() as (url, data, lock):
        crashed = SharedCoordinator(tmp_path / "crashed", ServerBackend(url), owner_id="crashed", lease_seconds=10)
        successor = SharedCoordinator(tmp_path / "successor", ServerBackend(url), owner_id="successor", lease_seconds=10)
        old = crashed.reserve_candidate("orphan_a", "request_a", migration_ids=[], migration_revision="0")
        assert old["result"] == "OPEN"
        assert successor.status("orphan_a")["candidate_claim_pending"] is True
        # Simulate process termination before PipelineStore.start writes its ledger.
        with lock:
            data["now"] = old["token"]["lease_expires_at"] + 3
        assert successor.recover_orphan_candidate("default") is True
        recovered = successor.status("orphan_a")
        assert recovered["requested_slug_closed"]["reason"] == "CANCELLED"
        assert recovered["candidate_window"]["request_ids"] == ["request_a"]
        assert recovered["phase"] == "IDLE"
        next_claim = successor.reserve_candidate("candidate_b", "request_b")
        assert next_claim["result"] == "OPEN"
        assert next_claim["attempts"] == 2
        assert successor.status("candidate_b")["candidate_window"]["request_ids"] == ["request_a", "request_b"]


def test_new_clone_guard_recovers_expired_orphan_automatically(tmp_path):
    with authority() as (url, data, lock):
        first = SharedCoordinator(tmp_path / "first", ServerBackend(url), owner_id="first", lease_seconds=10)
        second_root = tmp_path / "second"
        second_root.mkdir()
        second = SharedCoordinator(second_root, ServerBackend(url), owner_id="second", lease_seconds=10)
        old = first.reserve_candidate("orphan_a", "request_a", migration_ids=[], migration_revision="0")
        with lock:
            data["now"] = old["token"]["lease_expires_at"] + 3
        with using_coordinator(second_root, second):
            assert duplicate_guard(second_root, "candidate_b", "request_b", "default") == "OPEN"
        assert second.status("orphan_a")["requested_slug_closed"]["reason"] == "CANCELLED"
        current = second.status("candidate_b")
        assert current["owner_id"] == "second"
        assert current["candidate_claim_pending"] is False
        assert second.read_files([".pipeline/episodes/candidate_b.json"], current["revision"])[
            ".pipeline/episodes/candidate_b.json"] is not None


def test_channel_mismatch_cannot_claim_or_recover_another_channel(tmp_path):
    from engine.pipeline_state import PipelineError

    with authority() as (url, data, _):
        root = tmp_path / "clone"
        root.mkdir()
        client = SharedCoordinator(root, ServerBackend(url), owner_id="clone", lease_seconds=10)
        with using_coordinator(root, client):
            with pytest.raises(PipelineError, match="CHANNEL_MISMATCH"):
                duplicate_guard(root, "nostalgia_candidate", "request_a", "default")
        assert data["states"] == {}


def test_standalone_candidate_acquire_also_recovers_after_crash(tmp_path):
    with authority() as (url, data, lock):
        first = SharedCoordinator(tmp_path / "first", ServerBackend(url), owner_id="first", lease_seconds=10)
        second = SharedCoordinator(tmp_path / "second", ServerBackend(url), owner_id="second", lease_seconds=10)
        old = first.acquire("orphan_a", "request_a", initial_phase="CANDIDATE")
        with lock:
            data["now"] = old["lease_expires_at"] + 3
        assert second.recover_orphan_candidate("default") is True
        assert second.acquire("candidate_b", "request_b")["slug"] == "candidate_b"


def test_active_candidate_lease_cannot_be_recovered(tmp_path):
    with authority() as (url, _, _):
        owner = SharedCoordinator(tmp_path / "owner", ServerBackend(url), owner_id="owner", lease_seconds=10)
        other = SharedCoordinator(tmp_path / "other", ServerBackend(url), owner_id="other", lease_seconds=10)
        original = owner.reserve_candidate("candidate_a", "request_a", migration_ids=[], migration_revision="0")
        before = other.status("candidate_a")
        assert other.recover_orphan_candidate("default") is False
        after = other.status("candidate_a")
        assert after["revision"] == before["revision"]
        assert after["generation"] == original["token"]["generation"]
        assert owner.assert_current(original["token"], mutation=True) == original["token"]


@pytest.mark.parametrize("marker", [
    ".pipeline/episodes/orphan_a.json",
    ".pipeline/artifacts/orphan_a.json",
    ".publish-queue/orphan_a.txt",
    ".publication-attempts/orphan_a.json",
])
def test_exact_remote_evidence_blocks_orphan_recovery(tmp_path, marker):
    with authority() as (url, data, lock):
        owner = SharedCoordinator(tmp_path / "owner", ServerBackend(url), owner_id="owner", lease_seconds=10)
        other = SharedCoordinator(tmp_path / "other", ServerBackend(url), owner_id="other", lease_seconds=10)
        claim = owner.reserve_candidate("orphan_a", "request_a", migration_ids=[], migration_revision="0")
        # Model a durable write between reserve_candidate and local bookkeeping,
        # leaving the pending flag set so this exact marker is the only guard.
        state, revision, _ = owner.backend.read("default")
        assert state["candidate_claim_pending"] is True
        owner.backend.compare_and_swap("default", revision, state, {marker: b"durable evidence"})
        with lock:
            data["now"] = claim["token"]["lease_expires_at"] + 3
        before = other.status("orphan_a")
        assert other.recover_orphan_candidate("default") is False
        after = other.status("orphan_a")
        assert after["revision"] == before["revision"]
        assert after["phase"] == "CANDIDATE"
        assert after["requested_slug_closed"] is None


def test_old_orphan_token_is_fenced_after_recovery(tmp_path):
    with authority() as (url, data, lock):
        old_owner = SharedCoordinator(tmp_path / "old", ServerBackend(url), owner_id="old", lease_seconds=10)
        successor = SharedCoordinator(tmp_path / "new", ServerBackend(url), owner_id="new", lease_seconds=10)
        old = old_owner.reserve_candidate("orphan_a", "request_a", migration_ids=[], migration_revision="0")
        with lock:
            data["now"] = old["token"]["lease_expires_at"] + 3
        assert successor.recover_orphan_candidate("default") is True
        with pytest.raises(CoordinationError, match="SHARED_STALE_FENCE"):
            old_owner.commit_mutation(old["token"], {"episodes/orphan_a/story.json": b"late write"})
        assert successor.read_files(["episodes/orphan_a/story.json"], successor.status("orphan_a")["revision"])[
            "episodes/orphan_a/story.json"] is None


def _orphan_recovery_worker(root, url, owner, slug, request_id, start, results):
    coordinator = SharedCoordinator(Path(root), ServerBackend(url), owner_id=owner, lease_seconds=10)
    start.wait(10)
    try:
        recovered = coordinator.recover_orphan_candidate("default")
    except CASConflict:
        recovered = False
    decision = None
    for _ in range(8):
        try:
            decision = coordinator.reserve_candidate(slug, request_id)
            break
        except CASConflict:
            continue
    results.put((recovered, decision["result"] if decision else "CAS_FAILED"))


def test_independent_processes_cannot_both_recover_and_claim_same_slot(tmp_path):
    with authority() as (url, data, lock):
        crashed = SharedCoordinator(tmp_path / "crashed", ServerBackend(url), owner_id="crashed", lease_seconds=10)
        old = crashed.reserve_candidate("orphan_a", "request_a", migration_ids=[], migration_revision="0")
        with lock:
            data["now"] = old["token"]["lease_expires_at"] + 3
        context = multiprocessing.get_context("spawn")
        start, results = context.Event(), context.Queue()
        workers = [context.Process(target=_orphan_recovery_worker,
                   args=(tmp_path / f"clone_{index}", url, f"clone-{index}",
                         f"candidate_{index}", f"request_{index}", start, results)) for index in (1, 2)]
        for worker in workers:
            worker.start()
        start.set()
        observed = [results.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
        assert sum(recovered for recovered, _ in observed) == 1
        assert sorted(result for _, result in observed) == ["OPEN", "RESUME_EXISTING_EPISODE"]
        with lock:
            state = data["states"]["default"]
            assert state["slug"] in {"candidate_1", "candidate_2"}
            assert len(state["candidate_window"]["request_ids"]) == 2
            assert "request_a" in state["candidate_window"]["request_ids"]
            assert state["candidate_claim_pending"] is True
