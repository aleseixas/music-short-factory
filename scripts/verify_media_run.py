"""Read-only publication gate for the exact prepared media source and successful run."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.media_provenance import matches_media_run, matches_prepared_request
from engine.pipeline_state import PipelineError, safe_slug


def _api(path: str) -> dict:
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode:
        raise PipelineError("PREFLIGHT_API_UNAVAILABLE", "Exact source run or authority could not be read.", recoverable=True)
    return json.loads(result.stdout)


def verify(repository: str, slug: str, run_id: str, *, timeout: float = 900,
           api=_api, sleep=time.sleep, monotonic=time.monotonic) -> dict:
    slug = safe_slug(slug)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise PipelineError("PREFLIGHT_IDENTITY_INVALID", "Canonical repository and exact run ID required.")
    deadline = monotonic() + timeout
    def get(path: str) -> dict:
        while True:
            try:
                return api(path)
            except PipelineError as exc:
                remaining = deadline - monotonic()
                if not exc.recoverable or remaining <= 0:
                    raise
                sleep(min(15, remaining))

    state = json.loads(base64.b64decode(get(f"repos/{repository}/contents/.pipeline/episodes/{slug}.json?ref=main")["content"]))
    request_id = str(state.get("request_id", ""))
    path = f".episode-check/{slug}--{request_id}.json"
    queue = base64.b64decode(get(f"repos/{repository}/contents/.publish-queue/{slug}.txt?ref=main")["content"]).decode().splitlines()
    if (state.get("stage") not in {"QUEUED", "PUBLISHING"} or not state.get("media_preflight_passed")
            or state.get("slug") != slug or queue != [slug, run_id, request_id]
            or state.get("request_path") != path):
        raise PipelineError("PREFLIGHT_AUTHORITY_MISMATCH", "Source run does not own the queued authoritative request.")
    run = get(f"repos/{repository}/actions/runs/{run_id}")
    if run.get("event") == "push":
        source_sha = str(run.get("head_sha", ""))
    elif run.get("event") == "workflow_dispatch":
        prefix = f"media/{slug}/{request_id}/"
        title = str(run.get("display_title", ""))
        source_sha = title[len(prefix):] if title.startswith(prefix) else ""
    else:
        source_sha = ""
    if (not matches_media_run(run, slug, request_id, source_sha)
            or (state.get("stage") == "QUEUED" and
                (str(state.get("run_id", "")) != run_id or state.get("commit_sha") != source_sha))
            or (state.get("stage") == "PUBLISHING" and not state.get("publisher_started"))):
        raise PipelineError("PREFLIGHT_RUN_ORIGIN_INVALID", "Run provenance does not match the queued episode/request/SHA.")
    receipt = json.loads(base64.b64decode(get(f"repos/{repository}/contents/{path}?ref={source_sha}")["content"]))
    if not matches_prepared_request(receipt, slug, request_id, state.get("fingerprint", "")):
        raise PipelineError("PREFLIGHT_REQUEST_MISMATCH", "Source SHA lacks the exact validated request.")
    while True:
        run = get(f"repos/{repository}/actions/runs/{run_id}")
        if str(run.get("id", "")) != run_id or not matches_media_run(run, slug, request_id, source_sha):
            raise PipelineError("PREFLIGHT_RUN_ORIGIN_INVALID", "Run provenance does not match the queued episode/request/SHA.")
        if run.get("status") == "completed":
            return run
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise PipelineError("PREFLIGHT_SOURCE_TIMEOUT", "Exact media run is still incomplete.", recoverable=True)
        sleep(min(15, remaining))


def main() -> int:
    try:
        run = verify(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("EPISODE", ""),
                     os.environ.get("SOURCE_RUN_ID", ""))
        print(f"PREFLIGHT_SOURCE_VERIFIED={run['id']}")
        return 0
    except (PipelineError, ValueError, KeyError, TypeError) as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
