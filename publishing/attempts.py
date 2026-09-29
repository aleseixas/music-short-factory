"""Durable, compare-and-set publication claims; an uncertain send is never retried."""
from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable
from uuid import uuid4

import requests

from .base import PublishingError


class PublicationLocked(PublishingError):
    """The episode has already crossed the irreversible publication boundary."""


def _marker(root: Path, slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", slug):
        raise PublicationLocked("PUBLICATION_SLUG_INVALID")
    return root / ".publication-attempts" / f"{slug}.json"


def session_id() -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        run = os.environ.get("GITHUB_RUN_ID", "")
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if not run.isdigit() or not attempt.isdigit():
            raise PublicationLocked("PUBLICATION_RUN_ID_REQUIRED")
        return f"github:{run}:{attempt}"
    return f"local:{uuid4().hex}"


class AttemptStore:
    def __init__(self, root: Path, session: Any = None, *, allow_local: bool = False):
        self.root = root.resolve()
        self.http = session or requests.Session()
        self.allow_local = allow_local  # Only injected offline fixtures may create local claims.
        self.in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
        self.repository, self.configuration_error = self._repository()
        self.remote = bool(self.repository)
        self._token = ""
        # Only serializes this object's heartbeat with its own CAS. Remote
        # coordination, never this lock, grants cross-clone authorization.
        self._coordination_lock = threading.RLock()
        self._observations: dict[str, dict] = {}

    def _repository(self) -> tuple[str, str]:
        configured = os.environ.get("GITHUB_REPOSITORY", "").strip()
        valid = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
        if configured and not re.fullmatch(valid, configured):
            return "", "PUBLICATION_REPOSITORY_INVALID"
        if self.in_actions:
            return configured, "" if configured else "PUBLICATION_REPOSITORY_REQUIRED"
        if not (self.root / ".git").exists():
            return configured, ""
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"], cwd=self.root, capture_output=True,
                text=True, encoding="utf-8", timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return "", "PUBLICATION_ORIGIN_UNAVAILABLE"
        origin = result.stdout.strip() if result.returncode == 0 else ""
        match = re.fullmatch(
            r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
            r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/?", origin,
        )
        if match is None:
            return "", "PUBLICATION_GITHUB_ORIGIN_REQUIRED"
        repository = match[1].removesuffix(".git")
        if configured and configured.casefold() != repository.casefold():
            return "", "PUBLICATION_REPOSITORY_MISMATCH"
        return repository, ""

    def _require_shared_store(self) -> None:
        if self.configuration_error:
            raise PublicationLocked(self.configuration_error)
        if not self.remote and not self.allow_local:
            raise PublicationLocked(
                "PUBLICATION_DURABLE_STORE_UNAVAILABLE: live publication requires the canonical GitHub repository"
            )

    def _credential(self) -> str:
        if self._token:
            return self._token
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
        if not token and not self.in_actions:
            try:
                result = subprocess.run(
                    ["gh", "auth", "token", "--hostname", "github.com"], cwd=self.root,
                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                )
                token = result.stdout.strip() if result.returncode == 0 else ""
            except (OSError, subprocess.SubprocessError):
                token = ""
        if not token:
            raise PublicationLocked("PUBLICATION_DURABLE_STORE_UNAVAILABLE: GitHub authentication required")
        self._token = token
        return token

    def _request(self, method: str, slug: str, **kwargs: Any):
        self._require_shared_store()
        token = self._credential()
        # Never print response bodies, URLs with credentials, or transport exceptions.
        url = f"https://api.github.com/repos/{self.repository}/contents/.publication-attempts/{slug}.json"
        try:
            return self.http.request(
                method, url, headers={"Authorization": f"Bearer {token}",
                                      "Accept": "application/vnd.github+json",
                                      "X-GitHub-Api-Version": "2026-03-10"},
                timeout=30, **kwargs,
            )
        except requests.RequestException as exc:
            raise PublicationLocked(
                "PUBLICATION_CLAIM_UNCERTAIN: remote claim may exist; no publisher was called"
            ) from exc

    def _read(self, slug: str, *, remote: bool | None = None) -> tuple[dict[str, Any] | None, str | None]:
        path = _marker(self.root, slug)
        try:
            from engine.coordination_runtime import coordinator_for
            coordinator = coordinator_for(self.root) if remote is not False else None
            if coordinator is not None:
                self._require_shared_store()
                observation = coordinator.status(slug)
                name = f".publication-attempts/{slug}.json"
                payload = coordinator.read_files([name], observation["revision"])[name]
                self._observations[slug] = observation
                if payload is None:
                    return None, None
                raw = payload.decode("utf-8")
                sha = observation["revision"]
            elif self.remote if remote is None else remote:
                response = self._request("GET", slug, params={"ref": "main"})
                if response.status_code == 404:
                    return None, None
                if response.status_code != 200:
                    raise PublicationLocked(f"PUBLICATION_STORE_READ_FAILED: HTTP {response.status_code}")
                envelope = response.json()
                raw = base64.b64decode(envelope["content"]).decode("utf-8")
                sha = envelope["sha"]
            else:
                if not path.exists():
                    return None, None
                raw = path.read_text(encoding="utf-8")
                sha = None
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("slug") != slug:
                raise ValueError("invalid marker")
            if coordinator is not None or (self.remote if remote is None else remote):
                from engine.pipeline_state import atomic_json
                atomic_json(path, data)  # Future offline reads retain observed publication evidence.
            return data, sha
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PublicationLocked("PUBLICATION_STATE_INVALID: existing marker locks the episode") from exc

    def assert_remote_unstarted(self, slug: str) -> None:
        data, _ = self._read(slug)
        if data is not None:
            raise PublicationLocked("PUBLICATION_ALREADY_ATTEMPTED: mutation and republication forbidden")

    def assert_unstarted(self, slug: str) -> None:
        # A connected checkout may be stale even outside Actions. Check the exact
        # central marker before materializing or changing publishable content.
        self.assert_remote_unstarted(slug)
        from engine.pipeline_state import PipelineStore
        PipelineStore(self.root).assert_mutation_allowed(slug, check_remote=False)

    @contextmanager
    def _local_lock(self, slug: str):
        from engine.process_lock import process_lock
        try:
            with process_lock(_marker(self.root, slug).with_suffix(".lock")):
                yield
        except TimeoutError as exc:
            raise PublicationLocked("PUBLICATION_CLAIM_BUSY: an active process owns the claim") from exc

    def _verify_live_preflight(self, slug: str, state: dict, fingerprint: str = "") -> None:
        if not self.remote and self.allow_local:
            return  # Explicit offline test fixture; never a production fallback.
        from publishing.snapshot import approved_manifest
        try:
            values = (self.root / ".publish-queue" / f"{slug}.txt").read_text(encoding="utf-8").splitlines()
            if len(values) != 3 or values[0] != slug or values[2] != state["request_id"] or not values[1].isdigit():
                raise ValueError("queue identity")
            source_run = values[1]
            if str(state.get("run_id", "")) != source_run:
                raise ValueError("source run identity")
            manifest, actual_fingerprint = approved_manifest(self.root, slug, source_run, values[2])
            if actual_fingerprint != fingerprint:
                raise ValueError("approved snapshot fingerprint changed")
            deadline = time.monotonic() + 300
            while True:
                response = self.http.request("GET", f"https://api.github.com/repos/{self.repository}/actions/runs/{source_run}",
                                             headers={"Authorization": f"Bearer {self._credential()}", "Accept": "application/vnd.github+json"}, timeout=30)
                run = response.json() if response.status_code == 200 else {}
                # Queue dispatch may begin before the source workflow finishes.
                # Observe only this exact run; never rerun or replace the hard gate.
                if (str(run.get("id")) != source_run or run.get("status") == "completed"
                        or time.monotonic() >= deadline):
                    break
                time.sleep(5)
            if (str(run.get("id")) != source_run or run.get("status") != "completed" or run.get("conclusion") != "success"
                    or run.get("event") != "push" or run.get("head_branch") != "main"
                    or str(run.get("path", "")).split("@", 1)[0] != ".github/workflows/episode-media-preflight.yml"
                    or not re.fullmatch(r"[a-fA-F0-9]{40}", str(run.get("head_sha", "")))
                    or run.get("head_sha") != state.get("commit_sha")):
                raise ValueError("external preflight has not succeeded")
            # Check the exact receipt at the source run's triggering commit.
            request_path = state.get("request_path") or f".episode-check/{slug}--{state['request_id']}.json"
            if not re.fullmatch(r"\.episode-check/[A-Za-z0-9_-]+\.json", request_path):
                raise ValueError("request path")
            response = self.http.request("GET", f"https://api.github.com/repos/{self.repository}/contents/{request_path}",
                                         params={"ref": run["head_sha"]},
                                         headers={"Authorization": f"Bearer {self._credential()}", "Accept": "application/vnd.github+json"}, timeout=30)
            request = json.loads(base64.b64decode(response.json()["content"])) if response.status_code == 200 else {}
            if request.get("slug") != slug or request.get("request_id") != state["request_id"] or request.get("local_preflight_passed") is not True:
                raise ValueError("request does not match the successful run")
        except Exception as exc:
            raise PublicationLocked("PUBLICATION_PREFLIGHT_UNVERIFIED: exact successful external run and approved staged bytes are required") from exc

    def claim(self, slug: str, platform: str, publication_session: str, *, payload_fingerprint: str = "") -> dict[str, Any]:
        self._require_shared_store()
        with self._local_lock(slug):
            data, sha = self._read(slug)
            if data is not None:
                if data.get("session_id") != publication_session:
                    raise PublicationLocked("PUBLICATION_ALREADY_ATTEMPTED: another run/session owns the episode")
                if data.get("stage") != "PUBLISHING":
                    raise PublicationLocked("PUBLICATION_ALREADY_ATTEMPTED: a finished session cannot send again")
                if data.get("payload_fingerprint", "") != payload_fingerprint:
                    raise PublicationLocked("PUBLICATION_PAYLOAD_CHANGED: all platforms must use the same approved snapshot")
                attempted = data.get("platforms")
                if not isinstance(attempted, dict) or platform in attempted:
                    raise PublicationLocked("PUBLICATION_ALREADY_ATTEMPTED: platform cannot be retried")
                from engine.coordination_runtime import coordinator_for, current_token, save_token
                coordinator = coordinator_for(self.root)
                if coordinator is not None:
                    observation = self._observations[slug]
                    token = coordinator.resume_publication(slug, data["request_id"],
                        expected_generation=observation["generation"], expected_version=observation["version"])
                    save_token(self.root, token)
                current_token(self.root, slug)
            else:
                from engine.pipeline_state import PipelineStore
                pipeline = PipelineStore(self.root)
                state = pipeline.status(slug)
                from engine.coordination_runtime import coordinator_for, sync_authority
                coordinator = coordinator_for(self.root)
                if coordinator is not None:
                    authoritative = coordinator.status(slug)
                    if authoritative.get("slug") != slug or authoritative.get("request_id") != state["request_id"]:
                        raise PublicationLocked("PUBLICATION_REQUEST_MISMATCH: reconcile the exact shared request first")
                    sync_authority(self.root, slug, download=True)
                    state = pipeline.status(slug)
                if state["stage"] != "QUEUED" or not state["media_preflight_passed"]:
                    raise PublicationLocked("PUBLICATION_PROVENANCE_REQUIRED: a validated queued episode is required before any live publisher")
                requested = os.environ.get("PIPELINE_REQUEST_ID")
                source_run = os.environ.get("SOURCE_RUN_ID")
                if requested and requested != state["request_id"]:
                    raise PublicationLocked("PUBLICATION_REQUEST_MISMATCH: queued state belongs to another request")
                if source_run and source_run != str(state.get("run_id", "")):
                    raise PublicationLocked("PUBLICATION_SOURCE_RUN_MISMATCH: queued state belongs to another preflight run")
                self._verify_live_preflight(slug, state, payload_fingerprint)
                data = {"schema_version": 2, "slug": slug, "payload_fingerprint": payload_fingerprint,
                        "request_id": os.environ.get("PIPELINE_REQUEST_ID") or state["request_id"],
                        "commit_sha": os.environ.get("GITHUB_SHA", ""),
                        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
                        "session_id": publication_session, "publisher_started": True, "stage": "PUBLISHING",
                        "EVER_PUBLISHED_OR_ATTEMPTED": True, "REPUBLICATION_ALLOWED": False,
                        "recovery_mutation_allowed": False, "platforms": {}}
            data["platforms"][platform] = {
                "attempt_id": uuid4().hex, "platform": platform,
                "idempotency_key": None,  # Current platform adapters expose no supported send key.
                "payload_fingerprint": payload_fingerprint,
                "status": "prepared", "stage": "prepared", "remote_id": None,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_shared_claim = False
            if sha is None and len(data["platforms"]) == 1:
                atomic_shared_claim = self._close_shared(slug, data, payload_fingerprint)
            if atomic_shared_claim:
                from engine.pipeline_state import atomic_json
                atomic_json(_marker(self.root, slug), data)
            else:
                self._persist_publication(slug, data, sha, "claim")
            from engine.pipeline_state import PipelineStore
            PipelineStore(self.root).mark_publisher_started(
                slug, request_id=data["request_id"], commit_sha=data["commit_sha"], run_id=data["run_id"]
            )
            return data

    def _close_shared(self, slug: str, data: dict, fingerprint: str) -> bool:
        from engine.coordination_runtime import acquire_token, save_token
        coordinator, token = acquire_token(self.root, slug, data["request_id"])
        if coordinator is None:
            return False  # Explicit injected offline fixtures only.
        if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise PublicationLocked("PUBLICATION_PAYLOAD_FINGERPRINT_REQUIRED")
        closed = coordinator.close_for_publication(token, fingerprint, files={
            f".publication-attempts/{slug}.json": (json.dumps(data, sort_keys=True) + "\n").encode(),
        })
        save_token(self.root, closed)
        return True

    def _finish_shared(self, slug: str, data: dict, *, recovery: bool = False) -> bool:
        from engine.coordination_runtime import coordinator_for, token_path, save_token
        from engine.pipeline_state import read_json
        from engine.shared_coordination import CoordinationError
        coordinator = coordinator_for(self.root)
        if coordinator is None:
            return False
        cached = read_json(token_path(self.root, slug))
        files = {f".publication-attempts/{slug}.json": (json.dumps(data, sort_keys=True) + "\n").encode()}
        state = coordinator.status(slug)
        closed = state.get("requested_slug_closed") or {}
        observation = self._observations.get(slug)
        if observation is None:
            raise PublicationLocked("PUBLICATION_OBSERVATION_REQUIRED")
        if (closed.get("request_id") == data["request_id"]
                and (closed.get("outcome") or closed.get("reason")) != "PUBLISHING"):
            coordinator.update_closed_publication(slug, data["request_id"], files,
                                                  expected_revision=observation["revision"])
            from engine.pipeline_state import atomic_json
            atomic_json(_marker(self.root, slug), data)
            return True
        try:
            if cached is None or recovery:
                raise CoordinationError("SHARED_TOKEN_INVALID")
            finished = coordinator.finish(cached, data["stage"], files=files)
        except CoordinationError:
            # An expired owner can only release conservatively. The marker may
            # retain stronger positive platform evidence than that channel state.
            finished = coordinator.recover_publication_expired(slug, data["request_id"], files=files,
                                                              expected_version=observation["version"])
        if finished is not None:
            save_token(self.root, finished)
        from engine.pipeline_state import atomic_json
        atomic_json(_marker(self.root, slug), data)
        return True

    @contextmanager
    def keepalive(self, slug: str):
        from engine.coordination_runtime import current_token, save_token
        coordinator, token = current_token(self.root, slug)
        if coordinator is None:
            yield
            return
        stop = threading.Event()
        errors = []
        def heartbeat():
            nonlocal token
            while not stop.wait(max(1, coordinator.lease_seconds / 3)):
                try:
                    with self._coordination_lock:
                        from engine.coordination_runtime import token_path
                        from engine.pipeline_state import read_json
                        token = read_json(token_path(self.root, slug))
                        token = coordinator.heartbeat(token)
                        save_token(self.root, token)
                except Exception as exc:
                    errors.append(exc)
                    return
        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            worker.join(timeout=35)
        if worker.is_alive() or errors:
            raise PublicationLocked("PUBLICATION_LEASE_UNCERTAIN: reconcile without resending")

    def _persist(self, slug: str, data: dict, sha: str | None, operation: str) -> None:
        from engine.pipeline_state import atomic_json
        raw = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if self.remote:
            payload = {"message": f"Record publication {operation} for {slug}", "branch": "main",
                       "content": base64.b64encode(raw.encode()).decode()}
            if sha:
                payload["sha"] = sha
            response = self._request("PUT", slug, json=payload)
            if response.status_code not in (200, 201):
                raise PublicationLocked(f"PUBLICATION_{operation.upper()}_REJECTED: HTTP {response.status_code}")
        atomic_json(_marker(self.root, slug), data)

    def _persist_publication(self, slug: str, data: dict, sha: str | None, operation: str) -> None:
        from engine.coordination_runtime import current_token, save_token
        from engine.pipeline_state import atomic_json
        with self._coordination_lock:
            coordinator, token = current_token(self.root, slug)
            if coordinator is None:
                self._persist(slug, data, sha, operation)
                return
            token = coordinator.update_publication(token, {
                f".publication-attempts/{slug}.json": (json.dumps(data, sort_keys=True) + "\n").encode(),
            })
            save_token(self.root, token)
            atomic_json(_marker(self.root, slug), data)

    def _update_platform(self, slug: str, platform: str, publication_session: str, update: dict) -> dict:
        self._require_shared_store()
        with self._local_lock(slug):
            data, sha = self._read(slug)
            if (data is None or data.get("session_id") != publication_session
                    or data.get("stage") != "PUBLISHING" or platform not in data.get("platforms", {})):
                raise PublicationLocked("PUBLICATION_SESSION_CLOSED")
            attempt = data["platforms"][platform]
            if attempt.get("status") in {"confirmed_sent", "confirmed_not_sent", "uncertain"}:
                raise PublicationLocked("PUBLICATION_ATTEMPT_TERMINAL")
            attempt.update(update, updated_at=datetime.now(timezone.utc).isoformat())
            self._persist_publication(slug, data, sha, "stage")
            return data

    def mark_sending(self, slug: str, platform: str, publication_session: str) -> dict:
        # This durable boundary MUST complete before temporary hosting/upload.
        return self._update_platform(slug, platform, publication_session, {"stage": "sending", "status": "sending"})

    def record_uploaded(self, slug: str, platform: str, publication_session: str, uploaded: dict) -> dict:
        from .recovery import remote_resource
        return self._update_platform(slug, platform, publication_session,
                                     {"stage": "uploaded", "status": "sending", "remote_id": remote_resource(uploaded)})

    def record_receipt(self, slug: str, platform: str, publication_session: str, result: Any) -> dict:
        from .recovery import confirmed_receipt
        return self._update_platform(slug, platform, publication_session, {
            "stage": "receipt", "status": "confirmed_sent" if confirmed_receipt(platform, result) else "uncertain",
            "external_id": result.external_id, "receipt_status": result.status,
        })

    def reconcile(self, slug: str, observer: Callable[[str, dict], str] | None = None) -> dict | None:
        """Reconcile an abandoned local publisher, permanently forbidding resends.

        CAS against the send-stage write makes prepared -> not_sent proof safe:
        a live process losing that CAS cannot cross mark_sending into upload.
        """
        self._require_shared_store()
        with self._local_lock(slug):
            data, sha = self._read(slug)
            if data is None:
                return None
            if observer is None:
                from .recovery import platform_observer
                observer = platform_observer(self.root)
            for platform, attempt in data.get("platforms", {}).items():
                status = attempt.get("status")
                if status not in {"confirmed_sent", "confirmed_not_sent"}:
                    if attempt.get("stage") == "prepared":
                        status = "confirmed_not_sent"
                    else:
                        try:
                            observed = observer(platform, dict(attempt))
                        except Exception:
                            observed = "uncertain"
                        # Absence at a platform never proves non-delivery after sending.
                        status = "confirmed_sent" if observed == "confirmed_sent" else "uncertain"
                    attempt.update(status=status, reconciled_at=datetime.now(timezone.utc).isoformat())
            outcomes = [item.get("status") for item in data.get("platforms", {}).values()]
            data["stage"] = "PUBLISHED" if outcomes and all(item == "confirmed_sent" for item in outcomes) else "PUBLISH_UNCERTAIN"
            data["finished_at"] = datetime.now(timezone.utc).isoformat()
            if not self._finish_shared(slug, data, recovery=True):
                self._persist(slug, data, sha, "reconciliation")
            from engine.pipeline_state import PipelineStore
            PipelineStore(self.root).reconcile(slug)
            return data

    def complete(self, slug: str, publication_session: str, *, success: bool) -> dict[str, Any] | None:
        """Persist only the final workflow observation, never authorize another send."""
        self._require_shared_store()
        with self._local_lock(slug):
            data, sha = self._read(slug)
            if data is None:
                return None  # A preparation failure never crossed the send boundary.
            if data.get("session_id") != publication_session:
                raise PublicationLocked("PUBLICATION_ALREADY_ATTEMPTED: cannot finalize another session")
            outcomes = [item.get("status") for item in data.get("platforms", {}).values()]
            data["stage"] = "PUBLISHED" if success and outcomes and all(item == "confirmed_sent" for item in outcomes) else "PUBLISH_UNCERTAIN"
            data["finished_at"] = datetime.now(timezone.utc).isoformat()
            if not self._finish_shared(slug, data):
                self._persist(slug, data, sha, "outcome")
            from engine.pipeline_state import PipelineStore
            # The permanent marker remains authoritative after the slot is released.
            PipelineStore(self.root).reconcile(slug)
            return data
