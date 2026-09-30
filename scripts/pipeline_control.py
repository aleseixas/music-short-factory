"""Machine-readable pipeline control; shared mutations use an authoritative Git CAS."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
import os
import re
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline_state import PipelineError, PipelineStore, channel_for, read_json
from engine.pipeline_runtime import RunIdentity, github_fetcher, prepare_request, trigger_plan, verify_request_commit, wait_for_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "reconcile"):
        command = commands.add_parser(name)
        command.add_argument("--slug")
        command.add_argument("--channel", choices=("default", "nostalgia"), default="default")
        if name == "reconcile":
            command.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    command = commands.add_parser("contract")
    command.add_argument("--workflow", default="media")
    for name in ("start", "transition", "prepare", "publisher-started", "wait"):
        command = commands.add_parser(name)
        command.add_argument("slug")
        command.add_argument("--request-id", default="")
        command.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA", ""))
        command.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
        if name == "transition":
            command.add_argument("stage")
            command.add_argument("--local-preflight-passed", action="store_true")
        if name == "wait":
            command.add_argument("--before-sha", default="", help="Exact github.event.before for a push containing multiple commits.")
            command.add_argument("--event", choices=("push", "workflow_dispatch"))
            command.add_argument("--workflow", default="media")
            command.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
            command.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        store = PipelineStore(root)
        with redirect_stdout(sys.stderr):
            if args.command == "status":
                from engine.coordination_runtime import sync_authority, sync_channel
                if args.slug:
                    sync_authority(root, args.slug)
                else:
                    sync_channel(root, args.channel)
                cached = read_json(store.index_path)
                if not args.slug and not (cached and cached.get("schema_version") == 1 and isinstance(cached.get("active"), dict)):
                    store.reconcile()
                result = store.status(args.slug, channel=args.channel)
            elif args.command == "reconcile":
                from engine.coordination_runtime import coordinator_for
                coordinator = coordinator_for(root)
                if coordinator is not None:
                    coordinator.recover_orphan_candidate(channel_for(args.slug) if args.slug else args.channel)
                observer = None
                if args.repository:
                    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
                        raise PipelineError("REPOSITORY_REQUIRED", "Use owner/repository.")
                    if args.slug:
                        from publishing.attempts import AttemptStore, PublicationLocked
                        attempts = AttemptStore(root)
                        if attempts.configuration_error or (attempts.repository and attempts.repository.casefold() != args.repository.casefold()):
                            raise PipelineError("REPOSITORY_MISMATCH", "Reconcile only the checkout's canonical repository.")
                        attempts.repository, attempts.remote = args.repository, True
                        try:
                            attempts._read(args.slug)  # Synchronize the exact durable marker before observing a stale local index.
                        except PublicationLocked as exc:
                            raise PipelineError("PUBLICATION_OBSERVATION_FAILED", str(exc), recoverable=True) from exc
                    def observer(run_id):
                        if not run_id.isdigit():
                            raise PipelineError("INVALID_RUN_ID", "Publisher run id must be numeric.")
                        completed = subprocess.run(["gh", "api", f"repos/{args.repository}/actions/runs/{run_id}"], cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
                        if completed.returncode:
                            raise PipelineError("GITHUB_QUERY_FAILED", "Exact publisher run could not be observed; preserve the irreversible claim.", recoverable=True)
                        return json.loads(completed.stdout)
                result = store.reconcile(args.slug, observe_run=observer)
                if args.slug:
                    from engine.coordination_runtime import coordinator_for, sync_authority
                    coordinator = coordinator_for(root)
                    if coordinator is not None:
                        shared = coordinator.status(args.slug)
                        if shared.get("slug") == args.slug and shared.get("phase") == "PUBLISHING":
                            from publishing.attempts import AttemptStore
                            if shared.get("released") or shared["server_now"] > shared["lease_expires_at"] + coordinator.clock_grace_seconds:
                                AttemptStore(root).reconcile(args.slug)
                        elif shared.get("slug") == args.slug and shared.get("phase") == "QUEUED" and args.repository:
                            from scripts.dispatch_publication import dispatch
                            dispatch(root, args.repository, args.slug, result["request_id"], str(result.get("run_id", "")))
                        sync_authority(root, args.slug, download=True)
                        result = store.status(args.slug)
                if not args.slug:
                    from engine.coordination_runtime import sync_channel
                    sync_channel(root, args.channel)
                    result = store.status(channel=args.channel)
            elif args.command == "contract":
                result = trigger_plan(root, args.workflow)
            elif args.command == "start":
                result = store.start(args.slug, args.request_id)
            elif args.command == "transition":
                evidence = dict(commit_sha=args.commit_sha, run_id=args.run_id)
                if args.local_preflight_passed:
                    evidence["local_preflight_passed"] = True
                result = store.transition(args.slug, args.stage, request_id=args.request_id or None, **evidence)
            elif args.command == "publisher-started":
                result = store.mark_publisher_started(args.slug, args.request_id, args.commit_sha, args.run_id)
            elif args.command == "prepare":
                result = prepare_request(root, args.slug, args.request_id)
            else:
                plan = trigger_plan(root, args.workflow)
                state = store.status(args.slug)
                event = args.event or plan["trigger"]
                if event not in plan["events"]:
                    raise PipelineError("WORKFLOW_TRIGGER_UNSUPPORTED", event)
                identity = RunIdentity(args.request_id, args.slug, args.commit_sha, plan["file"], event, args.before_sha)
                if re.fullmatch(r"[a-fA-F0-9]{40}", args.commit_sha):
                    known = subprocess.run(["git", "cat-file", "-e", args.commit_sha + "^{commit}"], cwd=root, capture_output=True)
                    if known.returncode:
                        fetched = subprocess.run(["git", "fetch", "origin", "main"], cwd=root, capture_output=True, timeout=60)
                        if fetched.returncode:
                            raise PipelineError("REQUEST_COMMIT_UNAVAILABLE", "Fetch the authoritative main before correlating its request.", recoverable=True)
                verify_request_commit(root, identity, state.get("request_path", ""))
                result = wait_for_run(identity, github_fetcher(root, args.repository), timeout=args.timeout)
                current = store.status(args.slug)
                if args.workflow == "media" and current["stage"] == "MEDIA_PREFLIGHT" and current["request_id"] == args.request_id:
                    store.transition(args.slug, "READY_TO_QUEUE" if result.get("conclusion") == "success" else "VALIDATION_FAILED",
                                     request_id=args.request_id, run_id=str(result["id"]), commit_sha=args.commit_sha)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1 if result.get("result") == "FAIL" or (result.get("status") == "completed" and result.get("conclusion") != "success") else 0
    except (PipelineError, OSError, ValueError) as exc:
        error = exc if isinstance(exc, PipelineError) else PipelineError("PIPELINE_IO_ERROR", str(exc))
        print(json.dumps(error.record(slug=getattr(args, "slug", "") or "", request_id=getattr(args, "request_id", ""),
                                      commit_sha=getattr(args, "commit_sha", "")), ensure_ascii=False))
        return 2 if error.recoverable else 1


if __name__ == "__main__":
    raise SystemExit(main())
