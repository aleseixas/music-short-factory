from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

from engine.media_provenance import WORKFLOW, WORKFLOW_FILE, matches_media_run
from engine.pipeline_runtime import prepare_request
from engine.pipeline_state import PipelineError, PipelineStore, atomic_json
from scripts.admit_media_preflight import admit
from scripts.parse_prepare_output import parse_prepare_output
from scripts.pipeline_workflow import media_pass
from scripts.verify_media_run import verify


SHA = "a" * 40


def test_prepare_output_ignores_logs_but_requires_final_correlated_result():
    result = {"slug": "demo", "request_id": "request", "commit_sha": SHA,
              "next_action": "wait_for_correlated_run"}
    output = "render log\n" + json.dumps({"diagnostic": "ignored"}) + "\n" + json.dumps(result) + "\n"
    assert parse_prepare_output(output, "demo", "request") == result
    with pytest.raises(ValueError, match="PREPARE_RESULT_INVALID"):
        parse_prepare_output(output, "other", "request")
    with pytest.raises(ValueError, match="PREPARE_RESULT_MISSING"):
        parse_prepare_output("render log\n", "demo", "request")


def test_dispatch_run_requires_exact_slug_request_and_source_sha():
    run = {"name": "Episode media preflight", "path": WORKFLOW, "event": "workflow_dispatch",
           "head_branch": "main", "head_sha": "b" * 40,
           "display_title": f"media/demo/request/{SHA}", "status": "completed", "conclusion": "success"}
    assert matches_media_run(run, "demo", "request", SHA, require_success=True)
    for slug, request, sha in (("other", "request", SHA), ("demo", "other", SHA), ("demo", "request", "c" * 40)):
        assert not matches_media_run(run, slug, request, sha, require_success=True)
    assert not matches_media_run({**run, "conclusion": "failure"}, "demo", "request", SHA, require_success=True)


def test_publisher_read_gate_checks_dispatch_identity_and_prepared_receipt():
    source_sha = SHA
    run = {"id": 123, "name": "Episode media preflight", "path": WORKFLOW,
           "event": "workflow_dispatch", "head_branch": "main", "head_sha": "b" * 40,
           "display_title": f"media/demo/request/{source_sha}", "status": "completed", "conclusion": "success"}
    state = {"slug": "demo", "request_id": "request", "request_path": ".episode-check/demo--request.json",
             "stage": "QUEUED", "media_preflight_passed": True, "run_id": "123", "commit_sha": source_sha}
    receipt = {"slug": "demo", "request_id": "request", "local_preflight_passed": True, "workflow": WORKFLOW_FILE}
    def envelope(value):
        import base64
        return {"content": base64.b64encode(json.dumps(value).encode()).decode()}
    def api(path):
        if "/actions/runs/123" in path:
            return run
        if "/.pipeline/episodes/demo.json?ref=main" in path:
            return envelope(state)
        if "/.publish-queue/demo.txt?ref=main" in path:
            import base64
            return {"content": base64.b64encode(b"demo\n123\nrequest\n").decode()}
        if "/.episode-check/demo--request.json?ref=" in path:
            return envelope(receipt)
        raise AssertionError(path)
    assert verify("owner/repo", "demo", "123", api=api)["id"] == 123
    state.update(stage="PUBLISHING", publisher_started=True, run_id="999", commit_sha="c" * 40)
    assert verify("owner/repo", "demo", "123", api=api)["id"] == 123
    state.update(stage="QUEUED", publisher_started=False, run_id="123", commit_sha=source_sha)
    with pytest.raises(PipelineError, match="PREFLIGHT_RUN_ORIGIN_INVALID"):
        run["display_title"] = f"media/other/request/{source_sha}"
        verify("owner/repo", "demo", "123", api=api)
    run["display_title"] = f"media/demo/request/{source_sha}"
    with pytest.raises(PipelineError, match="PREFLIGHT_REQUEST_MISMATCH"):
        receipt["request_id"] = "other"
        verify("owner/repo", "demo", "123", api=api)


def test_media_admission_claims_one_run_and_rejects_other_sha(tmp_path: Path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True).stdout.strip()
    with patch("engine.coordination_runtime._factory", return_value=None):
        project = Path(__file__).resolve().parents[1]
        (tmp_path / "config").mkdir()
        shutil.copyfile(project / "config/pipeline-contract.json", tmp_path / "config/pipeline-contract.json")
        (tmp_path / ".github/workflows").mkdir(parents=True)
        shutil.copyfile(project / ".github/workflows/episode-media-preflight.yml",
                        tmp_path / ".github/workflows/episode-media-preflight.yml")
        store = PipelineStore(tmp_path)
        store.start("demo", "request")
        for stage in ("UNIQUE", "AUTHORING"):
            store.transition("demo", stage)
        atomic_json(tmp_path / "episodes/demo/story.json", {"slug": "demo"})
        git("init", "-q", "-b", "main")
        git("remote", "add", "origin", "https://github.com/owner/repo.git")
        git("add", ".")
        git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "base")
        with patch("publishing.attempts.AttemptStore.assert_remote_unstarted"):
            prepared = prepare_request(tmp_path, "demo", "request", validator=lambda *a, **k: [])
        git("add", ".")
        git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "prepared")
        source_sha = git("rev-parse", "HEAD")
        (tmp_path / "unrelated.txt").write_text("later", encoding="utf-8")
        git("add", "unrelated.txt")
        git("-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-qm", "later")
        later_sha = git("rev-parse", "HEAD")
        git("update-ref", "refs/remotes/origin/main", later_sha)
        with pytest.raises(PipelineError, match="PREFLIGHT_SOURCE_MISMATCH"):
            admit(tmp_path, "demo", "request", later_sha, "123", "workflow_dispatch")
        git("switch", "--detach", source_sha)
        assert prepared["request_path"] == ".episode-check/demo--request.json"
        for wrong_slug, wrong_request in (("other", "request"), ("demo", "other")):
            with pytest.raises(PipelineError, match="PREFLIGHT_SOURCE_MISMATCH"):
                admit(tmp_path, wrong_slug, wrong_request, source_sha, "123", "workflow_dispatch")
        with pytest.raises(PipelineError, match="PREFLIGHT_IDENTITY_INVALID"):
            admit(tmp_path, "demo", "request", source_sha, "123", "workflow_dispatch", attempt="2")
        assert admit(tmp_path, "demo", "request", source_sha, "123", "workflow_dispatch")["run_id"] == "123"
        with pytest.raises(PipelineError, match="PREFLIGHT_AUTHORITY_MISMATCH"):
            admit(tmp_path, "demo", "request", source_sha, "456", "workflow_dispatch")
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "456",
                                    "PIPELINE_SOURCE_SHA": source_sha}), \
             patch("publishing.attempts.AttemptStore.assert_remote_unstarted"):
            with pytest.raises(PipelineError, match="PREFLIGHT_ADMISSION_REQUIRED"):
                media_pass(tmp_path, "demo", "request")
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "123",
                                    "PIPELINE_SOURCE_SHA": source_sha}), \
             patch("publishing.attempts.AttemptStore.assert_remote_unstarted"):
            media_pass(tmp_path, "demo", "request")
        with pytest.raises(PipelineError, match="PREFLIGHT_SOURCE_MISMATCH"):
            admit(tmp_path, "demo", "request", later_sha, "456", "workflow_dispatch")
