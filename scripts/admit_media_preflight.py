"""CAS-admit one media run for the prepared request at an exact main commit."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.coordination_runtime import release_episode, sync_authority
from engine.media_provenance import WORKFLOW_FILE, matches_prepared_request
from engine.pipeline_runtime import episode_fingerprint
from engine.pipeline_state import PipelineError, PipelineStore, safe_request, safe_slug


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise PipelineError("PREFLIGHT_SOURCE_UNVERIFIED", "Source SHA is not a verified commit on main.")
    return result.stdout.strip()


def admit(root: Path, slug: str, request_id: str, source_sha: str, run_id: str,
          event: str, attempt: str = "1") -> dict:
    slug, request_id = safe_slug(slug), safe_request(request_id)
    if (not re.fullmatch(r"[a-fA-F0-9]{40}", source_sha) or not re.fullmatch(r"[1-9][0-9]*", run_id)
            or event not in {"push", "workflow_dispatch"} or attempt != "1"):
        raise PipelineError("PREFLIGHT_IDENTITY_INVALID", "Exact SHA, first-attempt run and allowed event required.")
    if _git(root, "rev-parse", "HEAD") != source_sha:
        raise PipelineError("PREFLIGHT_SOURCE_MISMATCH", "Checkout must be the exact requested source SHA.")
    _git(root, "merge-base", "--is-ancestor", source_sha, "origin/main")
    path = f".episode-check/{slug}--{request_id}.json"
    if event == "workflow_dispatch":
        added = _git(root, "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A",
                     "--root", "-r", source_sha, "--", path)
        if added != path:
            raise PipelineError("PREFLIGHT_SOURCE_MISMATCH", "source_sha must be the CAS commit that added this request.")
    try:
        receipt = json.loads(_git(root, "show", f"{source_sha}:{path}"))
        prepared = json.loads(_git(root, "show", f"{source_sha}:.pipeline/episodes/{slug}.json"))
    except (ValueError, TypeError) as exc:
        raise PipelineError("PREFLIGHT_REQUEST_INVALID", "Prepared request/state JSON is invalid at source SHA.") from exc
    fingerprint = episode_fingerprint(root, slug)
    if (not matches_prepared_request(receipt, slug, request_id, fingerprint)
            or prepared.get("slug") != slug or prepared.get("request_id") != request_id
            or prepared.get("stage") != "MEDIA_PREFLIGHT"
            or prepared.get("request_path") != path or prepared.get("workflow") != WORKFLOW_FILE
            or prepared.get("local_preflight_passed") is not True
            or prepared.get("fingerprint") != fingerprint):
        raise PipelineError("PREFLIGHT_REQUEST_MISMATCH", "The exact commit does not contain the prepared episode/request bytes.")
    sync_authority(root, slug, download=True)
    store = PipelineStore(root)
    state = store.status(slug)
    if (state.get("stage") != "MEDIA_PREFLIGHT" or state.get("request_id") != request_id
            or state.get("request_path") != path or state.get("workflow") != WORKFLOW_FILE
            or state.get("fingerprint") != fingerprint or state.get("local_preflight_passed") is not True
            or episode_fingerprint(root, slug) != fingerprint
            or state.get("run_id") or state.get("publisher_started") or state.get("queue_created")):
        raise PipelineError("PREFLIGHT_AUTHORITY_MISMATCH", "Only the current unclaimed prepared request can admit a run.")
    try:
        store.transition(slug, "MEDIA_PREFLIGHT", request_id=request_id,
                         commit_sha=source_sha, run_id=run_id)
    finally:
        release_episode(root, slug)
    return {"slug": slug, "request_id": request_id, "source_sha": source_sha, "run_id": run_id}


def main() -> int:
    try:
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise PipelineError("PREFLIGHT_BRANCH_INVALID", "Only main can admit a media run.")
        result = admit(Path.cwd(), os.environ.get("EPISODE", ""),
                       os.environ.get("PIPELINE_REQUEST_ID", ""), os.environ.get("PIPELINE_SOURCE_SHA", ""),
                       os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_EVENT_NAME", ""),
                       os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
        print(json.dumps(result, sort_keys=True))
        return 0
    except PipelineError as exc:
        print(json.dumps(exc.record(stage="MEDIA_PREFLIGHT_ADMISSION")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
