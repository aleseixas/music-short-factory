"""Persist dispatch intent before asking GitHub to start the validated publisher."""
from __future__ import annotations

import base64
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline_state import PipelineError, PipelineStore, safe_request, safe_slug


def github_api(method, path, body=None):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise PipelineError("DISPATCH_AUTH_REQUIRED", "GitHub authentication required")
    request = Request("https://api.github.com/" + path, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + token,
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2026-03-10",
                               "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            value = json.loads(raw) if raw else {}
            value["_server_epoch"] = parsedate_to_datetime(response.headers["Date"]).timestamp()
            return response.status, value
    except HTTPError as exc:
        # Lease decisions use the authority's clock, never the clone's wall clock.
        return exc.code, {"_server_epoch": parsedate_to_datetime(exc.headers["Date"]).timestamp()}
    except (URLError, OSError, ValueError) as exc:
        raise PipelineError("DISPATCH_TRANSPORT_UNCERTAIN", "Observe the same dispatch; never send again blindly", recoverable=True) from exc


def dispatch(root, repository, slug, request_id, source_run_id, *, api=github_api,
             discovery_timeout=60, monotonic=time.monotonic, clock=None,
             owner_id=None, lease_seconds=120, settle_seconds=900,
             max_run_seconds=86400,
             coordinator=None, checkpoint=lambda stage: None):
    slug, request_id = safe_slug(slug), safe_request(request_id)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[1-9][0-9]*", source_run_id):
        raise PipelineError("DISPATCH_IDENTITY_INVALID", "Repository and exact source run are required")
    prefix = f"repos/{repository}"
    owner_id = owner_id or uuid.uuid4().hex
    coordinator_injected = coordinator is not None
    if lease_seconds <= 0 or settle_seconds < lease_seconds or max_run_seconds < settle_seconds:
        raise PipelineError("DISPATCH_LEASE_INVALID", "The observation deadline must exceed the send lease")
    server_epoch = None

    def call(method, target, body=None):
        nonlocal server_epoch
        status, value = api(method, target, body)
        if isinstance(value, dict) and "_server_epoch" in value:
            server_epoch = float(value["_server_epoch"])
        return status, value

    def now():
        if clock is not None:
            return float(clock())  # Explicit injected server clock for transport tests.
        if server_epoch is None:
            raise PipelineError("DISPATCH_CLOCK_UNVERIFIED", "The authority did not provide a server timestamp")
        return server_epoch

    title = f"publish/{slug}/{request_id}/{source_run_id}"
    path = f"{prefix}/contents/.pipeline/dispatch/{slug}--{request_id}.json"
    queue = (root / ".publish-queue" / f"{slug}.txt").read_text(encoding="utf-8").splitlines()
    if queue != [slug, source_run_id, request_id]:
        raise PipelineError("DISPATCH_QUEUE_MISMATCH", "Use the immutable queue's exact slug, request and source run")
    status, _ = call("GET", f"{prefix}/contents/.publication-attempts/{slug}.json?ref=main")
    if status == 200:
        return {"result": "PUBLISHER_ALREADY_STARTED", "next_action": "observe_publisher", "slug": slug, "request_id": request_id}
    if status != 404:
        raise PipelineError("DISPATCH_CLAIM_UNVERIFIED", f"Claim read returned HTTP {status}", recoverable=True)
    store = PipelineStore(root)
    state = store.status(slug)
    status, envelope = call("GET", path + "?ref=main")
    record, sha = None, None
    if status == 200:
        try:
            record = json.loads(base64.b64decode(envelope["content"]))
            sha = envelope["sha"]
            if any(record.get(key) != value for key, value in {
                "slug": slug, "request_id": request_id, "source_run_id": source_run_id}.items()):
                raise ValueError("mismatch")
            if record.get("expected_workflow", ".github/workflows/publish-episode.yml") != ".github/workflows/publish-episode.yml":
                raise ValueError("workflow mismatch")
            if (record.get("expected_source_sha") and state.get("commit_sha")
                    and record["expected_source_sha"] != state["commit_sha"]):
                raise ValueError("source SHA mismatch")
        except (ValueError, KeyError, TypeError) as exc:
            raise PipelineError("DISPATCH_RECEIPT_INVALID", "Existing intent must be reconciled; do not overwrite") from exc
    elif status != 404:
        raise PipelineError("DISPATCH_STORE_UNAVAILABLE", f"Dispatch store read returned HTTP {status}", recoverable=True)

    def save(value):
        nonlocal sha
        payload = {"message": f"Record publisher dispatch for {slug}", "branch": "main",
                   "content": base64.b64encode((json.dumps(value, indent=2) + "\n").encode()).decode()}
        if sha:
            payload["sha"] = sha
        code, saved = call("PUT", path, payload)
        if code not in (200, 201):
            raise PipelineError("DISPATCH_INTENT_CONFLICT", f"Intent write returned HTTP {code}; no automatic resend", recoverable=True)
        sha = saved.get("content", {}).get("sha")
        if not sha:
            raise PipelineError("DISPATCH_INTENT_UNVERIFIED", "Intent write did not return its version; observe the same dispatch", recoverable=True)

    def observed(run, *, expected_id=None, expected_sha=None):
        if (not isinstance(run, dict) or not re.fullmatch(r"[1-9][0-9]*", str(run.get("id", "")))
                or run.get("display_title") != title or run.get("event") != "workflow_dispatch"
                or str(run.get("path", "")).split("@", 1)[0] != ".github/workflows/publish-episode.yml"
                or run.get("head_branch") != "main"
                or not re.fullmatch(r"[a-fA-F0-9]{40}", str(run.get("head_sha", "")))
                or (expected_id is not None and str(run["id"]) != str(expected_id))
                or (expected_sha is not None and run["head_sha"] != expected_sha)):
            raise PipelineError("DISPATCH_RUN_IDENTITY_INVALID", "The observed run does not own this exact dispatch receipt")
        return {"result": "OBSERVED", "run_id": run["id"], "commit_sha": run["head_sha"],
                "status": run["status"], "conclusion": run.get("conclusion"),
                "slug": slug, "request_id": request_id, "next_action": "observe_publisher"}

    def pending(reason="observe_same_dispatch"):
        return {"result": "PENDING", "slug": slug, "request_id": request_id,
                "next_action": reason, "redispatch_allowed": False}

    def authority():
        nonlocal coordinator
        if coordinator is None:
            from engine.coordination_runtime import coordinator_for
            coordinator = coordinator_for(root)
        return coordinator

    def authorize():
        nonlocal coordinator, state
        # The local index is only a cache. A stale clone cannot authorize a send.
        if not coordinator_injected:
            from engine.coordination_runtime import acquire_token, sync_authority
            previous = state
            sync_authority(root, slug, download=True)
            state = store.status(slug)
            if any(previous.get(key) != state.get(key) for key in ("request_id", "run_id", "commit_sha")):
                raise PipelineError("DISPATCH_STATE_MISMATCH", "The authoritative request or source changed; reconcile before dispatch")
            coordinator, token = acquire_token(root, slug, request_id)
            if coordinator is None or token is None:
                raise PipelineError("DISPATCH_AUTHORITY_REQUIRED", "A shared reservation is required before dispatch")
            state["shared_token"] = token
        if state["stage"] != "QUEUED" or state["request_id"] != request_id or str(state.get("run_id", "")) != source_run_id:
            raise PipelineError("DISPATCH_STATE_MISMATCH", "Only the exact validated queued source may be dispatched")
        source_sha = state.get("commit_sha", "")
        if not re.fullmatch(r"[a-fA-F0-9]{40}", str(source_sha)):
            raise PipelineError("DISPATCH_SOURCE_SHA_REQUIRED", "Reconcile the exact validated source commit before dispatch")
        token = authority().validate_dispatch(slug, request_id, state)
        code, source = call("GET", f"{prefix}/actions/runs/{source_run_id}")
        # Queueing occurs in this media workflow, so its run may still be in
        # progress. Publisher admission independently requires completed/success.
        from engine.media_provenance import matches_media_run, matches_prepared_request
        if (code != 200 or str(source.get("id", "")) != source_run_id
                or not matches_media_run(source, slug, request_id, source_sha)):
            raise PipelineError("DISPATCH_SOURCE_UNVERIFIED", "The exact media workflow and triggering commit must match before dispatch")
        request_path = state.get("request_path") or f".episode-check/{slug}--{request_id}.json"
        if not re.fullmatch(r"\.episode-check/[A-Za-z0-9_-]+\.json", request_path):
            raise PipelineError("DISPATCH_SOURCE_UNVERIFIED", "The exact source request path is invalid")
        code, envelope = call("GET", f"{prefix}/contents/{request_path}?ref={source_sha}")
        try:
            receipt = json.loads(base64.b64decode(envelope["content"])) if code == 200 else {}
            if not matches_prepared_request(receipt, slug, request_id, state.get("fingerprint", "")):
                raise ValueError("source request mismatch")
        except (KeyError, ValueError, TypeError) as exc:
            raise PipelineError("DISPATCH_SOURCE_UNVERIFIED", "The source commit does not prove the exact validated request") from exc
        return token

    def terminal_uncertain(reason):
        # Empty/eventually consistent history is never proof of no dispatch.
        # Close the slug at the same authority used by publisher admission first:
        # a delayed runner or a paused former sender can then do no publication.
        shared = authority()
        current = shared.status(slug)
        closed = current.get("requested_slug_closed") or {}
        already_closed = (closed.get("request_id") == request_id
                          and (closed.get("outcome") or closed.get("reason")) == "DISPATCH_UNCERTAIN")
        if not already_closed and (current.get("slug") != slug or current.get("request_id") != request_id):
            raise PipelineError("SHARED_STALE_FENCE", "The active request changed; the old dispatch cannot close its successor")
        # A valid handoff/release can change the generation since PREPARED. Read
        # the current generation of this exact request, then close with CAS.
        # The original pinned generation is evidence, not permanent authority.
        shared.abandon_expired(slug, request_id, outcome="DISPATCH_UNCERTAIN",
                               expected_generation=current.get("generation"))
        checkpoint("SHARED_CLOSED")
        record.update(status="DISPATCH_UNCERTAIN", reconciled_at=now(),
                      reconciliation_reason=reason, redispatch_allowed=False)
        save(record)
        return {"result": "DISPATCH_UNCERTAIN", "slug": slug, "request_id": request_id,
                "next_action": "create_new_episode", "redispatch_allowed": False}

    def expired():
        return now() >= float(record.get("reconcile_after", now() + settle_seconds))

    def finish_observation(run, **identity):
        result = observed(run, **identity)
        if now() >= float(record["run_reconcile_after"]):
            return terminal_uncertain("run_without_publisher_exceeded_maximum_lifetime")
        if run["status"] == "completed" and expired():
            # Completed runs that never claimed a publisher cannot pin QUEUED
            # forever. Shared closure is CAS-serialized with publisher admission,
            # so a late or rerun worker cannot send after this terminal outcome.
            return terminal_uncertain("completed_run_without_publisher_after_deadline")
        return result

    # PREPARED proves the POST has not been authorized. Only a successful CAS to
    # SENDING permits POST; an expired PREPARED owner cannot race its successor.
    if record and record.get("status") == "PREPARED":
        if now() < float(record["lease_expires_at"]):
            return pending("wait_for_active_dispatch_owner")
        token = authorize()
        record.update(owner_id=owner_id, generation=int(record["generation"]) + 1,
                      reservation_generation=token.get("generation"),
                      heartbeat_at=now(), lease_expires_at=now() + lease_seconds,
                      reconcile_after=now() + settle_seconds,
                      run_reconcile_after=now() + max_run_seconds)
        save(record)
    elif record and record.get("status") == "DISPATCH_UNCERTAIN":
        return {"result": "DISPATCH_UNCERTAIN", "slug": slug, "request_id": request_id,
                "next_action": "create_new_episode", "redispatch_allowed": False}
    # Accepted and ambiguous attempts are observed, never sent again.
    elif record and record.get("status") != "REJECTED":
        # Migrate legacy un-timestamped INTENT records conservatively. They are
        # ambiguous; a new bounded observation window does not permit a resend.
        legacy_receipt = "reconcile_after" not in record or "run_reconcile_after" not in record
        if "reconcile_after" not in record:
            record.update(owner_id=record.get("owner_id", "legacy"),
                          generation=record.get("generation", 1), created_at=now(),
                          heartbeat_at=now(), lease_expires_at=now() + lease_seconds,
                          reconcile_after=now() + settle_seconds,
                          expected_workflow=".github/workflows/publish-episode.yml")
        if "run_reconcile_after" not in record:
            record["run_reconcile_after"] = float(record.get("created_at", now())) + max_run_seconds
        if legacy_receipt:
            save(record)
        if "run_id" in record:
            if not re.fullmatch(r"[1-9][0-9]*", str(record["run_id"])):
                raise PipelineError("DISPATCH_RECEIPT_INVALID", "The saved run identity must be reconciled; never redispatch")
            code, run = call("GET", f"{prefix}/actions/runs/{record['run_id']}")
            if code != 200:
                if expired():
                    return terminal_uncertain("exact_run_unavailable_after_deadline")
                raise PipelineError("DISPATCH_RUN_QUERY_FAILED", f"Exact run read returned HTTP {code}; never redispatch", recoverable=True)
            result = observed(run, expected_id=record["run_id"], expected_sha=record.get("head_sha"))
            if not record.get("head_sha"):
                record.update(head_sha=run["head_sha"], status="OBSERVED", observed_at=now())
                save(record)
            return finish_observation(run, expected_id=record["run_id"], expected_sha=record.get("head_sha"))
        matches = {}
        page, rows_seen = 1, 0
        deadline = monotonic() + discovery_timeout
        while True:
            if monotonic() >= deadline:
                if expired():
                    return terminal_uncertain("discovery_incomplete_after_deadline")
                raise PipelineError("DISPATCH_RUN_DISCOVERY_PENDING", "Discovery is incomplete; resume the same intent without redispatching", recoverable=True)
            code, payload = call("GET", f"{prefix}/actions/workflows/publish-episode.yml/runs?event=workflow_dispatch&per_page=100&page={page}")
            if code != 200:
                if expired():
                    return terminal_uncertain("discovery_unavailable_after_deadline")
                raise PipelineError("DISPATCH_RUN_QUERY_FAILED", f"Run query returned HTTP {code}", recoverable=True)
            runs = payload.get("workflow_runs", [])
            rows_seen += len(runs)
            for run in runs:
                if run.get("display_title") == title:
                    observed(run)
                    matches[str(run["id"])] = run
            if len(matches) > 1:
                raise PipelineError("DISPATCH_RUN_AMBIGUOUS", "More than one run owns this dispatch identity")
            if len(runs) < 100:
                if payload.get("total_count", rows_seen) > rows_seen:
                    if expired():
                        return terminal_uncertain("truncated_discovery_after_deadline")
                    raise PipelineError("DISPATCH_RUN_DISCOVERY_PENDING", "GitHub returned an incomplete run listing; never infer absence or redispatch", recoverable=True)
                break
            page += 1
        if matches:
            run = next(iter(matches.values()))
            record.update(status="OBSERVED", run_id=run["id"], head_sha=run["head_sha"], observed_at=now())
            save(record)  # Future restarts read the exact run instead of listing history.
            return finish_observation(run)
        if expired():
            return terminal_uncertain("no_identifiable_run_after_deadline")
        return pending()

    if not record or record.get("status") == "REJECTED":
        token = authorize()
        generation = int((record or {}).get("generation", 0)) + 1
        record = {"schema_version": 2, "slug": slug, "request_id": request_id,
                  "source_run_id": source_run_id, "status": "PREPARED", "display_title": title,
                  "expected_workflow": ".github/workflows/publish-episode.yml",
                  "expected_source_sha": state.get("commit_sha", ""),
                  "reservation_generation": token.get("generation"),
                  "owner_id": owner_id, "generation": generation, "created_at": now(),
                  "heartbeat_at": now(), "lease_expires_at": now() + lease_seconds,
                  "reconcile_after": now() + settle_seconds,
                  "run_reconcile_after": now() + max_run_seconds}
        save(record)
    checkpoint("PREPARED")
    authorize()
    if now() >= float(record["lease_expires_at"]):
        return pending("dispatch_owner_expired_before_send")
    record.update(status="SENDING", send_authorized_at=now(), heartbeat_at=now())
    save(record)  # Durable ambiguity boundary; never retry SENDING blindly.
    if not coordinator_injected:
        # Queue contents are sealed. The dispatch receipt now owns the send
        # decision, so release the authoring lease before a different Actions
        # run attempts publisher admission. Holding it would falsely block an
        # immediately scheduled publisher for the remainder of its lease.
        from engine.coordination_runtime import release_episode
        release_episode(root, slug)
    checkpoint("SENDING")
    try:
        code, response = call("POST", f"{prefix}/actions/workflows/publish-episode.yml/dispatches",
                      {"ref": "main", "inputs": {"episode": slug, "request_id": request_id, "source_run_id": source_run_id}})
    except PipelineError as exc:
        if exc.code != "DISPATCH_TRANSPORT_UNCERTAIN":
            raise
        return pending()
    checkpoint("POST_RETURNED")
    if code in (200, 201, 204):
        record["status"] = "DISPATCHED"
    elif code in (400, 401, 403, 404, 422, 429):
        record["status"] = "REJECTED"
    else:
        record["status"] = "UNCERTAIN"
    record["http_status"] = code
    record["response_received_at"] = now()
    if code == 200 and re.fullmatch(r"[1-9][0-9]*", str(response.get("workflow_run_id", ""))):
        record["run_id"] = response["workflow_run_id"]
    save(record)
    if record["status"] == "REJECTED":
        raise PipelineError("DISPATCH_REJECTED", f"GitHub rejected dispatch with HTTP {code}; correct its cause before rerunning queue job", recoverable=True)
    return {"result": record["status"], "slug": slug, "request_id": request_id,
            "next_action": "observe_same_dispatch", "redispatch_allowed": False}


def main():
    slug = os.environ.get("EPISODE", "")
    request_id = os.environ.get("PIPELINE_REQUEST_ID", "")
    try:
        result = dispatch(Path.cwd(), os.environ.get("GITHUB_REPOSITORY", ""), slug, request_id,
                          os.environ.get("GITHUB_RUN_ID", ""))
        print(json.dumps(result))
        return 0
    except (PipelineError, OSError, ValueError) as exc:
        error = exc if isinstance(exc, PipelineError) else PipelineError("DISPATCH_IO_ERROR", str(exc))
        print(json.dumps(error.record(slug=slug, request_id=request_id, stage="QUEUE_PUBLICATION")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
