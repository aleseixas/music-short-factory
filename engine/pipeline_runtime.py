"""Deterministic local gate and exact request -> GitHub run correlation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import tempfile
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Callable

from engine.pipeline_state import PipelineError, PipelineStore, atomic_json, safe_request, safe_slug


def workflow_events(root: Path, filename: str) -> tuple[set[str], str]:
    source = (root / ".github/workflows" / filename).read_text(encoding="utf-8")
    on = re.search(r"(?m)^(?:on|'on'|\"on\"):\s*\n((?:[ \t].*\n|\n)*)", source)
    block = on.group(1) if on else ""
    return set(re.findall(r"(?m)^  ([a-z_]+):", block)), block


def trigger_plan(root: Path, workflow: str) -> dict:
    contract = PipelineStore(root).contract
    try:
        rule = contract["workflows"][workflow]
    except KeyError as exc:
        raise PipelineError("WORKFLOW_UNKNOWN", workflow) from exc
    events, block = workflow_events(root, rule["file"])
    if rule["trigger"] not in events or rule["request_glob"] not in block:
        raise PipelineError("WORKFLOW_CONTRACT_DRIFT", f"{rule['file']} does not implement the declared request trigger.")
    return {**rule, "events": sorted(events), "action": "commit_and_push_request" if rule["trigger"] == "push" else "dispatch",
            "dispatch_required": rule["trigger"] != "push",
            "actions_handoff": "workflow_dispatch" if workflow == "media" else None,
            "actions_dispatch_required": workflow == "media"}


def _actions_prepare_has_native_dispatch() -> bool:
    """Only dedicated Actions jobs may prepare with the native token and explicit handoff."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return True
    if os.environ.get("PIPELINE_ACTIONS_PREPARE_AUTH") != "workflow-dispatch":
        return False
    workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF", "")
    allowed = (
        "/.github/workflows/recovery-prepare.yml@",
        "/.github/workflows/author-episode.yml@",
    )
    if not any(marker in workflow_ref for marker in allowed):
        return False
    token = os.environ.get("GH_TOKEN", "").strip()
    return bool(token and os.environ.get("PIPELINE_DEFAULT_GITHUB_TOKEN", "").strip() == token)


def episode_fingerprint(root: Path, slug: str) -> str:
    digest = hashlib.sha256()
    directory = root / "episodes" / safe_slug(slug)
    # Only the selected episode is inspected; historical episodes are irrelevant.
    for path in sorted(directory / name for name in ("story.json", "assets.json", "timeline.json", "visual_candidates.json", "post.json", "sources.txt")):
        if not path.is_file():
            continue
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare_request(root: Path, slug: str, request_id: str, **kwargs) -> dict:
    from engine.process_lock import process_lock
    safe_slug(slug)
    from engine.coordination_runtime import coordinator_for
    if (os.environ.get("GITHUB_ACTIONS") == "true"
            and coordinator_for(root) is not None
            and not _actions_prepare_has_native_dispatch()):
        raise PipelineError(
            "ACTIONS_PREPARE_DISPATCH_REQUIRED",
            "Actions prepare requires a dedicated recovery/author job using GITHUB_TOKEN "
            "and an explicit correlated workflow_dispatch handoff.",
        )
    try:
        with process_lock(root / ".pipeline" / "operations" / f"{slug}.lock"):
            return _prepare_request(root, slug, request_id, **kwargs)
    except TimeoutError as exc:
        raise PipelineError("EPISODE_BUSY", "Another local preflight owns this episode; resume its request.", recoverable=True) from exc


from engine.mutation_transaction import fenced_mutation


@fenced_mutation(root_arg="root")
def _prepare_request(root: Path, slug: str, request_id: str, *,
                    validator: Callable | None = None, repairer: Callable | None = None,
                    max_repairs: int = 2) -> dict:
    """Validate all errors, repair a batch, and create the trigger only after PASS."""
    slug, request_id = safe_slug(slug), safe_request(request_id)
    store = PipelineStore(root)
    store.assert_mutation_allowed(slug)
    plan = trigger_plan(root, "media")
    if validator is None:
        from check_episode_media_batch import validate_episode_media
        validator = validate_episode_media
    request_path = Path(".episode-check") / f"{slug}--{request_id}.json"
    if (root / request_path).exists():
        raise PipelineError("REQUEST_ALREADY_EXISTS", "A push trigger needs a new request file; resume its correlated run.")
    state = store.status(slug)
    if state["stage"] not in {"AUTHORING", "VALIDATION_FAILED", "LOCAL_VALIDATION", "LOCAL_REPAIR"}:
        raise PipelineError("AUTHORING_REQUIRED", f"Cannot prepare while {state['stage']}.")
    state = store.transition(slug, "LOCAL_VALIDATION", request_id=request_id, authoring_finished=True)
    diagnostics = root / ".pipeline" / "diagnostics" / f"{slug}.json"
    errors: list[dict] = []
    for attempt in range(max_repairs + 1):
        checked_fingerprint = episode_fingerprint(root, slug)
        errors = validator(root, slug, request_id=request_id, stage="LOCAL_VALIDATION", commit_sha="")
        if not errors and episode_fingerprint(root, slug) != checked_fingerprint:
            errors = [{"error_code": "EPISODE_CHANGED_DURING_VALIDATION", "recoverable": False,
                       "detail": "Authored files changed during validation; no request can certify those bytes."}]
        report = dict(schema_version=1, result="FAIL" if errors else "PASS", stage="LOCAL_VALIDATION",
                      slug=slug, request_id=request_id, commit_sha="", errors=errors)
        atomic_json(diagnostics, report)
        if not errors:
            fingerprint = episode_fingerprint(root, slug)
            # Recheck immediately before creating a publish-relevant request.
            store.assert_mutation_allowed(slug)
            state = store.transition(slug, "MEDIA_PREFLIGHT", request_id=request_id,
                                     local_preflight_passed=True, fingerprint=fingerprint,
                                     request_path=request_path.as_posix(), workflow=plan["file"],
                                     commit_sha="", run_id="", errors=[])
            payload = dict(slug=slug, request_id=request_id, local_preflight_passed=True,
                           episode_fingerprint=fingerprint, workflow=plan["file"])
            try:
                (root / request_path).parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(prefix=".request-", suffix=".tmp", dir=root / request_path.parent)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                        json.dump(payload, handle, ensure_ascii=False, indent=2)
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.link(temporary, root / request_path)  # Atomic, exclusive receipt: never partial or overwritten.
                finally:
                    Path(temporary).unlink(missing_ok=True)
            except OSError:
                store.transition(slug, "VALIDATION_FAILED", errors=[{"error_code": "REQUEST_WRITE_FAILED"}])
                raise
            return {**state, "trigger": plan, "request_path": request_path.as_posix(),
                    "next_action": "commit_and_push_request", "diagnostics": str(diagnostics)}
        if attempt == max_repairs or not any(error.get("recoverable") for error in errors):
            break
        before = episode_fingerprint(root, slug)
        store.transition(slug, "LOCAL_REPAIR", errors=errors)
        store.assert_mutation_allowed(slug)
        if repairer is not None:
            repairer(root, slug, errors)
        else:
            log = diagnostics.with_suffix(".log")
            log.write_text("MEDIA_PREFLIGHT_ERRORS_JSON=" + json.dumps(errors, ensure_ascii=False) + "\n", encoding="utf-8")
            from scripts.repair_media_preflight_batch import repair_batch
            repair_batch(root, slug, log)
        if episode_fingerprint(root, slug) == before:
            # A deterministic failure cannot justify running the same check again.
            break
        store.transition(slug, "LOCAL_VALIDATION")
    state = store.transition(slug, "VALIDATION_FAILED", errors=errors)
    return {**state, "result": "FAIL", "errors": errors, "diagnostics": str(diagnostics)}


@dataclass(frozen=True)
class RunIdentity:
    request_id: str
    slug: str
    commit_sha: str
    workflow: str
    event: str = "push"
    before_sha: str = ""
    run_id: str = ""


def verify_request_commit(root: Path, identity: RunIdentity, request_path: str = "") -> str:
    safe_request(identity.request_id)
    safe_slug(identity.slug)
    if not re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", identity.commit_sha):
        raise PipelineError("COMMIT_SHA_REQUIRED", "Run correlation requires the complete triggering commit SHA.")
    if identity.before_sha and not re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", identity.before_sha):
        raise PipelineError("BEFORE_SHA_INVALID", "Use the complete before SHA from the exact push event.")
    rules = PipelineStore(root).contract["workflows"]
    rule = next((item for item in rules.values() if item["file"] == identity.workflow), None)
    if not rule:
        raise PipelineError("WORKFLOW_UNKNOWN", identity.workflow)
    prefix = rule["request_glob"].split("*", 1)[0]
    if request_path and not request_path.startswith(prefix):
        request_path = ""
    if identity.before_sha and set(identity.before_sha) != {"0"}:
        # The real push workflow compares the event's two trees, including merge
        # and force-push events. Do not infer a boundary from a parent or latest run.
        command = ["git", "diff", "--name-only", "--diff-filter=A", identity.before_sha, identity.commit_sha]
    elif identity.before_sha:
        # A new-branch push has no before tree; mirror the workflow's git show.
        command = ["git", "show", "--pretty=", "--name-only", "--diff-filter=A", identity.commit_sha]
    else:
        command = ["git", "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A", "--root", "-r", identity.commit_sha]
    changed = subprocess.run([*command, "--", rule["request_glob"]], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
    if changed.returncode:
        raise PipelineError("REQUEST_COMMIT_MISMATCH", "Cannot verify the exact triggering commit or push range.")
    added = changed.stdout.splitlines()
    if len(added) != 1 or (request_path and request_path not in added):
        raise PipelineError("REQUEST_COMMIT_MISMATCH", "The triggering commit or explicit push range must add exactly one request for this workflow.")
    paths = added
    matches = []
    for filename in set(paths):
        path = Path(filename)
        if path.is_absolute() or ".." in path.parts or not filename.startswith(prefix):
            raise PipelineError("REQUEST_PATH_INVALID", filename)
        result = subprocess.run(["git", "show", f"{identity.commit_sha}:{path.as_posix()}"], cwd=root,
                                capture_output=True, text=True, encoding="utf-8", check=False)
        if result.returncode:
            continue
        try:
            if path.suffix == ".txt":
                lines = result.stdout.splitlines()
                request = {"slug": lines[0], "request_id": lines[2] if len(lines) > 2 else path.stem}
            else:
                request = json.loads(result.stdout)
            if (request.get("slug", request.get("episode")) == identity.slug
                    and request.get("request_id", path.stem) == identity.request_id
                    and request.get("workflow", identity.workflow) == identity.workflow):
                matches.append(filename)
        except (ValueError, IndexError, AttributeError):
            continue
    if len(matches) != 1:
        raise PipelineError("REQUEST_COMMIT_MISMATCH", "Exactly one request must match ID, slug and workflow at the supplied SHA.")
    return matches[0]


def wait_for_run(identity: RunIdentity, fetch_page: Callable, *, timeout: float = 900,
                 sleep: Callable = time.sleep, monotonic: Callable = time.monotonic) -> dict:
    """Paginate runs scoped to workflow/SHA. Delayed appearance is pending, not blocked."""
    deadline = monotonic() + timeout
    delay = 1.0
    while True:
        matches = []
        page = 1
        while True:
            try:
                payload = fetch_page(identity, page)
            except PipelineError as exc:
                if not exc.recoverable:
                    raise
                payload = {"workflow_runs": []}
            for run in payload.get("workflow_runs", []):
                path = str(run.get("path", "")).split("@", 1)[0].rsplit("/", 1)[-1]
                correlated = (run.get("display_title") ==
                              f"media/{identity.slug}/{identity.request_id}/{identity.commit_sha}"
                              if identity.event == "workflow_dispatch" else run.get("head_sha") == identity.commit_sha)
                if (correlated and path == identity.workflow and run.get("event") == identity.event
                        and run.get("head_branch") == "main"):
                    # Optional server-side identity fields must never contradict the request.
                    if run.get("request_id", identity.request_id) != identity.request_id or run.get("slug", identity.slug) != identity.slug:
                        continue
                    if identity.run_id and str(run.get("id", "")) != identity.run_id:
                        continue
                    matches.append(run)
            rows = payload.get("workflow_runs", [])
            more = payload.get("has_next", len(rows) == 100)
            if not more:
                break
            page += 1
            if monotonic() >= deadline:
                raise PipelineError("RUN_PENDING", "Run pagination is still pending; resume the same request.", recoverable=True)
        by_id = {str(run["id"]): run for run in matches}
        if len(by_id) > 1:
            raise PipelineError("RUN_AMBIGUOUS", "More than one run matches this exact workflow/event/SHA; do not pick the latest.")
        if by_id:
            run = next(iter(by_id.values()))
            if run.get("status") == "completed":
                return {**run, "request_id": identity.request_id, "slug": identity.slug,
                        "commit_sha": identity.commit_sha, "workflow": identity.workflow}
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise PipelineError("RUN_PENDING", "The exact request has not completed; resume polling without redispatching.", recoverable=True)
        sleep(min(delay, remaining, 30))
        delay = min(delay * 1.7, 30)


def github_fetcher(root: Path, repository: str) -> Callable:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise PipelineError("REPOSITORY_REQUIRED", "Use owner/repository for --repository.")

    def fetch(identity: RunIdentity, page: int) -> dict:
        sha_filter = f"head_sha={identity.commit_sha}&" if identity.event == "push" else ""
        endpoint = (f"repos/{repository}/actions/workflows/{identity.workflow}/runs"
                    f"?{sha_filter}event={identity.event}&per_page=100&page={page}")
        result = subprocess.run(["gh", "api", endpoint], cwd=root, capture_output=True,
                                text=True, encoding="utf-8", check=False, timeout=60)
        if result.returncode:
            raise PipelineError("GITHUB_QUERY_FAILED", "Could not read the correlated Actions run; request remains pending.", recoverable=True)
        return json.loads(result.stdout)
    return fetch
