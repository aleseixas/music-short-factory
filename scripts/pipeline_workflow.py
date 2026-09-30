"""Small workflow adapters for the shared pipeline state machine."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline_state import PipelineError, PipelineStore, atomic_json, channel_for, read_json, safe_request
from scripts.workflow_diagnostic import write_report
from engine.coordination_runtime import (
    acquire_token, coordinator_for, release_episode, save_token, sync_authority, sync_channel,
)


def queue_episode(root: Path, slug: str, request_id: str) -> None:
    store = PipelineStore(root)
    coordinator, token = acquire_token(root, slug, request_id)
    state = store.status(slug)
    if state["request_id"] != request_id or state["stage"] not in {"MEDIA_PREFLIGHT", "READY_TO_QUEUE", "QUEUED"}:
        raise PipelineError("QUEUE_STATE_MISMATCH", "Only this successful media request can create its queue.")
    run_id = str(state.get("run_id") or os.environ.get("GITHUB_RUN_ID", ""))
    if not run_id.isdigit():
        raise PipelineError("QUEUE_SOURCE_REQUIRED", "Exact preflight run required.")
    if os.environ.get("GITHUB_ACTIONS") == "true" and run_id != os.environ.get("GITHUB_RUN_ID"):
        raise PipelineError("QUEUE_SOURCE_MISMATCH", "Only the admitted media run may queue this request.")
    value = dict(state, stage="QUEUED", media_preflight_passed=True, queue_created=True, run_id=run_id)
    queue_path = root / ".publish-queue" / f"{slug}.txt"
    queue_bytes = f"{slug}\n{run_id}\n{request_id}\n".encode()
    if coordinator is not None:
        if token["phase"] == "QUEUED":
            authoritative = coordinator.backend.read_files([f".publish-queue/{slug}.txt"], token["revision"])
            if authoritative.get(f".publish-queue/{slug}.txt") != queue_bytes:
                raise PipelineError("QUEUE_IDENTITY_CONFLICT", "A sealed queue cannot be replaced.")
        else:
            value.update(shared_generation=token["generation"], shared_owner_id=token["owner_id"])
            token = coordinator.set_phase(token, "QUEUED", files={
                f".publish-queue/{slug}.txt": queue_bytes,
                f".pipeline/episodes/{slug}.json": (json.dumps(value, sort_keys=True) + "\n").encode(),
            })
        save_token(root, token)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_bytes(queue_bytes)  # Local cache of the already committed CAS.
    atomic_json(store.episode_path(slug), store._decorate(value))
    release_episode(root, slug)


def persist_current_metadata(root: Path, slug: str) -> None:
    from engine.mutation_transaction import fenced_mutation
    @fenced_mutation(root_arg="root")
    def checkpoint(root, slug):
        return 0
    checkpoint(root, slug)
    coordinator = coordinator_for(root)
    if coordinator is not None:
        outputs(source_sha=coordinator.status(slug)["revision"])


def outputs(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    text = "".join(f"{key}={value}\n" for key, value in values.items())
    print(text, end="")
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text)


def _legacy_candidate_ids(root: Path, channel: str, revision: str) -> list[str]:
    """Migrate a pre-counter channel from complete Git history at one exact SHA."""
    def git(*args: str) -> str:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", check=False,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if result.returncode:
            raise PipelineError("CANDIDATE_HISTORY_UNAVAILABLE",
                                "Fetch the exact shared revision before migrating the candidate window.",
                                recoverable=True)
        return result.stdout.strip()

    try:
        git("cat-file", "-e", revision + "^{commit}")
    except PipelineError:
        git("fetch", "--quiet", "origin", "main")
        git("cat-file", "-e", revision + "^{commit}")
    queue_paths = ([".publish-queue/nostalgia_*.txt"] if channel == "nostalgia" else
                   [".publish-queue/*.txt", ":(exclude).publish-queue/nostalgia_*.txt"])
    request_glob = ".duplicate-check-nostalgia/*.json" if channel == "nostalgia" else ".duplicate-check/*.json"
    boundary = git("log", "-1", "--format=%H", "--diff-filter=A", revision,
                   "--", *queue_paths)
    history = git("log", "--reverse", f"{boundary}..{revision}" if boundary else revision,
                  "--diff-filter=A", "--format=commit:%H", "--name-only", "--", request_glob)
    identities: list[str] = []
    seen: set[str] = set()
    commit_sha = ""
    for name in history.splitlines():
        if name.startswith("commit:"):
            commit_sha = name.partition(":")[2]
            continue
        if not name or name.endswith("-guardfix-test.json"):
            continue
        if not commit_sha:
            raise PipelineError("CANDIDATE_HISTORY_INVALID", "Historical request has no creation commit.",
                                recoverable=True)
        try:
            payload = json.loads(git("show", f"{commit_sha}:{name}"))
            if not isinstance(payload, dict):
                raise ValueError("request must be an object")
            identity = safe_request(str(payload.get("request_id") or Path(name).stem))
            if identity not in seen:
                seen.add(identity)
                identities.append(identity)
        except (TypeError, ValueError, PipelineError) as exc:
            raise PipelineError("CANDIDATE_HISTORY_INVALID", "A historical request has no valid identity.",
                                recoverable=True) from exc
    return identities[:15]


def duplicate_guard(root: Path, slug: str, request_id: str, channel: str) -> str:
    if channel != channel_for(slug):
        raise PipelineError("CHANNEL_MISMATCH", "Candidate slug and channel must match.")
    store = PipelineStore(root)
    coordinator = coordinator_for(root)
    if coordinator is not None:
        # A crash between the candidate CAS and store.start leaves no remote
        # episode ledger. Reclaim only that expired, provably orphaned claim.
        coordinator.recover_orphan_candidate(channel)
    sync_channel(root, channel)
    index = read_json(store.index_path)
    if not (index and index.get("schema_version") == 1 and isinstance(index.get("active"), dict)
            and all(key in index["active"] for key in ("default", "nostalgia"))):
        # A continuity-only decision must still leave durable state for the
        # workflow's persistence step, including migration from legacy episodes.
        store.reconcile()
    state = store.status(channel=channel)
    if state.get("reconciliation_required"):
        outputs(result="CONTINUITY_CONFLICT", continuity_slugs=",".join(state.get("conflicts", [])))
        return "CONTINUITY_CONFLICT"
    if state["active"] and (state["slug"] != slug or state["stage"] not in {"CANDIDATE", "UNIQUE"}):
        outputs(result="RESUME_EXISTING_EPISODE", resume_slug=state["slug"], continuity_slugs=state["slug"])
        return "RESUME_EXISTING_EPISODE"
    if coordinator is not None:
        from engine.shared_coordination import CASConflict
        for _ in range(4):
            authority = coordinator.status(slug)
            migration = (_legacy_candidate_ids(root, channel, authority["revision"])
                         if "candidate_window" not in authority else None)
            try:
                decision = coordinator.reserve_candidate(
                    slug, request_id, migration_ids=migration,
                    migration_revision=authority["revision"] if migration is not None else None,
                )
                break
            except CASConflict:
                continue
        else:
            raise PipelineError("CANDIDATE_CAS_BUSY", "Candidate authority changed repeatedly; retry the exact request.",
                                recoverable=True)
        attempts = decision["attempts"]
        outputs(candidate_attempts=attempts, candidate_limit=15)
        if decision["result"] == "RESUME_EXISTING_EPISODE":
            resume = decision["resume_slug"]
            outputs(result="RESUME_EXISTING_EPISODE", resume_slug=resume, continuity_slugs=resume)
            return "RESUME_EXISTING_EPISODE"
        if decision["result"] == "CANDIDATE_LIMIT_REACHED":
            outputs(result="CANDIDATE_LIMIT_REACHED")
            return "CANDIDATE_LIMIT_REACHED"
        save_token(root, decision["token"])
        store.start(slug, request_id)
        outputs(result="OPEN")
        return "OPEN"
    # Only trusted offline fixtures use this local counter. Production uses the
    # bounded candidate_window in the canonical shared coordination commit.
    counter_path = root / ".pipeline" / f"candidates-{channel}.json"
    with store.lock():
        counter = read_json(counter_path)
        if counter is None:
            queue_glob = ".publish-queue/nostalgia_*.txt" if channel == "nostalgia" else ".publish-queue/*.txt"
            request_glob = ".duplicate-check-nostalgia/*.json" if channel == "nostalgia" else ".duplicate-check/*.json"
            queue_paths = [queue_glob] + ([":(exclude).publish-queue/nostalgia_*.txt"] if channel == "default" else [])
            boundary = subprocess.run(["git", "log", "-1", "--format=%H", "--diff-filter=A", "--", *queue_paths],
                                      cwd=root, capture_output=True, text=True, check=True).stdout.strip()
            history = subprocess.run(["git", "log", f"{boundary}..HEAD" if boundary else "HEAD",
                                      "--diff-filter=A", "--format=", "--name-only", "--", request_glob],
                                     cwd=root, capture_output=True, text=True, check=True).stdout
            requests = sorted({str((read_json(root / line) or {}).get("request_id") or Path(line).stem) for line in history.splitlines()
                               if line.strip() and not line.endswith("-guardfix-test.json")})
            counter = {"schema_version": 1, "request_ids": requests[:16], "exhausted": len(requests) > 15}
        attempts = counter["request_ids"]
        if request_id not in attempts:
            attempts.append(request_id)
        if len(attempts) > 15:
            counter["exhausted"] = True
            counter["request_ids"] = attempts[:16]
        atomic_json(counter_path, counter)
    outputs(candidate_attempts=len(attempts), candidate_limit=15)
    if counter.get("exhausted"):
        outputs(result="CANDIDATE_LIMIT_REACHED")
        return "CANDIDATE_LIMIT_REACHED"
    store.start(slug, request_id)
    outputs(result="OPEN")
    return "OPEN"


def media_pass(root: Path, slug: str, request_id: str) -> None:
    store = PipelineStore(root)
    coordinator, token = acquire_token(root, slug)
    if coordinator is not None and token["request_id"] != request_id:
        raise PipelineError("WORKFLOW_REQUEST_STALE", "Preflight cannot replace another request's generation.")
    store.assert_mutation_allowed(slug)
    state = store.status(slug)
    if os.environ.get("GITHUB_ACTIONS") == "true" and not str(state.get("run_id", "")).isdigit():
        raise PipelineError("PREFLIGHT_ADMISSION_REQUIRED", "CAS-admitted media run required before PASS.")
    if (state.get("request_id") != request_id or str(state.get("run_id", "")) != os.environ.get("GITHUB_RUN_ID", "")
            or state.get("commit_sha") != os.environ.get("PIPELINE_SOURCE_SHA", os.environ.get("GITHUB_SHA", ""))):
        raise PipelineError("PREFLIGHT_ADMISSION_REQUIRED", "This run did not claim the exact prepared request and SHA.")
    stage = state["stage"]
    if stage == "CANDIDATE":
        raise PipelineError("DUPLICATE_PASS_REQUIRED", "Candidate has not passed duplicate validation")
    if stage == "UNIQUE":
        store.transition(slug, "AUTHORING")
        stage = "AUTHORING"
    if stage in {"AUTHORING", "LOCAL_REPAIR", "VALIDATION_FAILED"}:
        store.transition(slug, "LOCAL_VALIDATION", request_id=request_id)
    store.transition(slug, "MEDIA_PREFLIGHT", request_id=request_id,
                     local_preflight_passed=True, commit_sha=os.environ.get("PIPELINE_SOURCE_SHA", os.environ.get("GITHUB_SHA", "")),
                     run_id=os.environ.get("GITHUB_RUN_ID", ""))



def media_failed(root: Path, slug: str, request_id: str, run_id: str) -> None:
    """Record only this request's failed preflight; never roll back a queued send."""
    store = PipelineStore(root)
    state = store.status(slug)
    if state["publisher_started"] or state["stage"] in {"READY_TO_QUEUE", "QUEUED"}:
        return
    if state["request_id"] != request_id or state["stage"] != "MEDIA_PREFLIGHT":
        return  # A newer request or an earlier authoring stage owns this record.
    if not state.get("run_id") or str(state["run_id"]) != run_id:
        return
    store.transition(slug, "VALIDATION_FAILED", request_id=request_id,
                     run_id=run_id, media_preflight_passed=False,
                     errors=[{"error_code": "EXTERNAL_PREFLIGHT_FAILED", "error_class": "local_preflight",
                              "recoverable": True, "stage": "MEDIA_PREFLIGHT", "slug": slug,
                              "request_id": request_id, "commit_sha": os.environ.get("PIPELINE_SOURCE_SHA", os.environ.get("GITHUB_SHA", "")),
                              "target": f"actions/runs/{run_id}",
                              "detail": "Inspect this run's structured diagnostics and repair before another request."}])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("duplicate-guard", "duplicate-result", "media-pass", "media-failed", "queue", "assert-unstarted", "publisher-result", "reconcile", "release", "persist"))
    parser.add_argument("slug")
    parser.add_argument("--request-id", default=os.environ.get("PIPELINE_REQUEST_ID", ""))
    parser.add_argument("--channel", default="default", choices=("default", "nostalgia"))
    parser.add_argument("--result", default="")
    args = parser.parse_args(argv)
    root = Path.cwd()
    store = PipelineStore(root)
    try:
        if args.command in {"reconcile", "persist", "media-pass", "media-failed", "queue", "assert-unstarted"}:
            sync_authority(root, args.slug, download=True)
        if args.command == "reconcile":
            return 0
        if args.command == "release":
            release_episode(root, args.slug)
            return 0
        if args.command == "persist":
            persist_current_metadata(root, args.slug)
            return 0
        if args.command == "duplicate-guard":
            result = duplicate_guard(root, args.slug, args.request_id, args.channel)
            print("PREFLIGHT_RESULT=" + result)
        elif args.command == "duplicate-result":
            if args.result == "UNIQUE_CANDIDATE":
                store.transition(args.slug, "UNIQUE", request_id=args.request_id)
            elif args.result == "DUPLICATE_CANDIDATE":
                store.transition(args.slug, "REJECTED", request_id=args.request_id)
        elif args.command == "media-pass":
            media_pass(root, args.slug, args.request_id)
        elif args.command == "media-failed":
            media_failed(root, args.slug, args.request_id, os.environ.get("GITHUB_RUN_ID", ""))
        elif args.command == "queue":
            queue_episode(root, args.slug, args.request_id)
            atomic_json(root / ".pipeline" / f"candidates-{channel_for(args.slug)}.json",
                        {"schema_version": 1, "request_ids": [], "exhausted": False})
        elif args.command == "publisher-result":
            from publishing.attempts import AttemptStore, session_id
            AttemptStore(root).complete(args.slug, session_id(), success=args.result == "success")
        else:
            from publishing.attempts import AttemptStore
            acquire_token(root, args.slug, args.request_id or None)
            AttemptStore(root).assert_unstarted(args.slug)
            release_episode(root, args.slug)
    except Exception as exc:
        write_report(root, stage=args.command.upper(), slug=args.slug, request_id=args.request_id,
                     error_code=getattr(exc, "code", "PIPELINE_WORKFLOW_FAILED"),
                     error_class="non_recoverable", detail=str(exc), target=args.slug)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
