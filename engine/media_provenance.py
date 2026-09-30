"""Exact provenance for a media run started by push or correlated dispatch."""
from __future__ import annotations

import re


WORKFLOW = ".github/workflows/episode-media-preflight.yml"
WORKFLOW_FILE = "episode-media-preflight.yml"
SHA = re.compile(r"[a-fA-F0-9]{40}\Z")


def media_run_title(slug: str, request_id: str, source_sha: str) -> str:
    return f"media/{slug}/{request_id}/{source_sha}"


def matches_media_run(run: dict, slug: str, request_id: str, source_sha: str,
                      *, require_success: bool = False) -> bool:
    if (not SHA.fullmatch(str(source_sha)) or run.get("name") != "Episode media preflight"
            or str(run.get("path", "")).split("@", 1)[0] != WORKFLOW
            or run.get("head_branch") != "main"):
        return False
    event = run.get("event")
    if event == "push":
        if run.get("head_sha") != source_sha:
            return False
    elif event == "workflow_dispatch":
        # GitHub records the branch head at dispatch time. The prepared CAS
        # commit can be earlier because releasing its lease also advances main.
        if (run.get("display_title") != media_run_title(slug, request_id, source_sha)
                or not SHA.fullmatch(str(run.get("head_sha", "")))):
            return False
    else:
        return False
    if require_success:
        return run.get("status") == "completed" and run.get("conclusion") == "success"
    return (run.get("status") == "in_progress" or
            run.get("status") == "completed" and run.get("conclusion") == "success")


def matches_prepared_request(receipt: dict, slug: str, request_id: str,
                             fingerprint: str = "") -> bool:
    return (receipt.get("slug") == slug and receipt.get("request_id") == request_id
            and receipt.get("local_preflight_passed") is True
            and receipt.get("workflow") == WORKFLOW_FILE
            and (not fingerprint or receipt.get("episode_fingerprint") == fingerprint))
