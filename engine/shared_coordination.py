"""Cross-clone ownership and fencing on the canonical Git ref.

The commit and its parent form a compare-and-swap: a non-force ref update can
only fast-forward the parent we read. Authoritative mutation bytes and the new
fence live in that same commit. Working tree files are caches, never authority.
No network request is made by importing this module.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any
from uuid import uuid4

import requests

from .pipeline_state import PipelineError, channel_for, safe_request, safe_slug


class CoordinationError(PipelineError):
    def __init__(self, code: str, detail: str = "Reconcile the authoritative reservation before retrying."):
        super().__init__(code, detail, recoverable=code in {"SHARED_CAS_CONFLICT", "SHARED_LEASE_BUSY", "SHARED_LEASE_EXPIRED"})


class CASConflict(CoordinationError):
    def __init__(self):
        super().__init__("SHARED_CAS_CONFLICT")


def _json(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or value == "." or "\\" in value or path.is_absolute() or str(path) != value
            or any(part in {"", ".", ".."} or ":" in part for part in path.parts)):
        raise CoordinationError("SHARED_PATH_INVALID")
    if (value.startswith((".pipeline/coordination/", ".pipeline/closed/", ".git/"))
            or value in {".pipeline", ".pipeline/coordination", ".pipeline/closed", ".pipeline/owner.json", ".git"}):
        raise CoordinationError("SHARED_CONTROL_PATH_FORBIDDEN")
    return value


class GitHubRefBackend:
    """Git Data API backend; tokens must have contents write permission on main."""
    def __init__(self, root: Path, *, session=None):
        # Reuse canonical origin validation and credentials without import cycles.
        from publishing.attempts import AttemptStore
        self.attempts = AttemptStore(Path(root), session=session)
        self.http = session or self.attempts.http

    def _request(self, method: str, endpoint: str, **kwargs):
        self.attempts._require_shared_store()
        try:
            return self.http.request(
                method, f"https://api.github.com/repos/{self.attempts.repository}/{endpoint}",
                headers={"Authorization": f"Bearer {self.attempts._credential()}",
                         "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
                         "Cache-Control": "no-cache"}, timeout=30, **kwargs,
            )
        except requests.RequestException as exc:
            raise CoordinationError("SHARED_WRITE_UNCERTAIN" if method != "GET" else "SHARED_READ_UNAVAILABLE") from exc

    @staticmethod
    def _time(response) -> float:
        try:
            value = parsedate_to_datetime(response.headers["Date"])
            if value.tzinfo is None:
                raise ValueError("timezone missing")
            return value.timestamp()
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise CoordinationError("SHARED_CLOCK_UNAVAILABLE", "A server Date header is required; local wall clocks cannot expire leases.") from exc

    @staticmethod
    def _data(response, *statuses):
        if response.status_code not in statuses:
            raise CoordinationError("SHARED_API_REJECTED", f"Canonical store returned HTTP {response.status_code}.")
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise CoordinationError("SHARED_STATE_INVALID") from exc

    def read(self, channel: str) -> tuple[dict | None, str, float]:
        response = self._request("GET", "git/ref/heads/main")
        ref = self._data(response, 200)["object"]["sha"]
        response = self._request("GET", f"contents/.pipeline/coordination/{channel}.json", params={"ref": ref})
        now = self._time(response)
        if response.status_code == 404:
            return None, ref, now
        data = self._data(response, 200)
        try:
            state = json.loads(base64.b64decode(data["content"]))
            if not isinstance(state, dict):
                raise ValueError("object required")
        except (KeyError, ValueError, TypeError) as exc:
            raise CoordinationError("SHARED_STATE_INVALID") from exc
        return state, ref, now

    def read_files(self, paths: list[str], revision: str) -> dict[str, bytes | None]:
        """Read only exact paths at the already observed immutable revision."""
        result = {}
        for path in paths:
            normalized = str(PurePosixPath(path))
            if normalized != path or path.startswith("/") or ".." in PurePosixPath(path).parts or "\\" in path:
                raise CoordinationError("SHARED_PATH_INVALID")
            response = self._request("GET", f"contents/{path}", params={"ref": revision})
            if response.status_code == 404:
                result[path] = None
                continue
            data = self._data(response, 200)
            try:
                if data.get("encoding") != "base64" or data.get("type", "file") != "file":
                    raise ValueError("inline file content required")
                result[path] = base64.b64decode(data["content"])
            except (KeyError, ValueError, TypeError) as exc:
                raise CoordinationError("SHARED_STATE_INVALID") from exc
        return result

    def read_closed(self, slug: str, revision: str) -> dict | None:
        path = f".pipeline/closed/{safe_slug(slug)}.json"
        raw = self.read_files([path], revision)[path]
        if raw is None:
            return None
        try:
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("slug") != slug:
                raise ValueError("invalid closed slug record")
            return result
        except (ValueError, TypeError) as exc:
            raise CoordinationError("SHARED_STATE_INVALID") from exc

    def compare_and_swap(self, channel: str, expected: str, state: dict,
                         files: dict[str, bytes | None]) -> tuple[str, float]:
        parent = self._data(self._request("GET", f"git/commits/{expected}"), 200)
        entries = []
        changes = {**files, f".pipeline/coordination/{channel}.json": _json(state)}
        for path, data in changes.items():
            item = {"path": path, "mode": "100644", "type": "blob"}
            if data is None:
                item["sha"] = None
            else:
                blob = self._data(self._request("POST", "git/blobs", json={
                    "encoding": "base64", "content": base64.b64encode(data).decode("ascii")}), 201)
                item["sha"] = blob["sha"]
            entries.append(item)
        tree = self._data(self._request("POST", "git/trees", json={
            "base_tree": parent["tree"]["sha"], "tree": entries}), 201)
        # Unique operation ID prevents identical commits/ABA even in the same second.
        commit = self._data(self._request("POST", "git/commits", json={
            "message": f"Coordinate {channel} generation {state['generation']} [{uuid4().hex}]",
            "tree": tree["sha"], "parents": [expected]}), 201)
        # Blob preparation can take longer than a lease. Check the server clock
        # immediately before the authoritative ref update. CAS below still
        # arbitrates a takeover occurring after this check: only one can win.
        head_response = self._request("GET", "git/ref/heads/main")
        head = self._data(head_response, 200)
        if head["object"]["sha"] != expected:
            raise CASConflict()
        if not state.get("released") and self._time(head_response) >= state["lease_expires_at"]:
            raise CoordinationError("SHARED_LEASE_EXPIRED")
        response = self._request("PATCH", "git/refs/heads/main", json={"sha": commit["sha"], "force": False})
        if response.status_code in (409, 422):
            raise CASConflict()
        self._data(response, 200)
        return commit["sha"], self._time(response)


def _owner(root: Path) -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        run = os.environ.get("GITHUB_RUN_ID", "")
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if not run.isdigit() or not attempt.isdigit():
            raise CoordinationError("SHARED_ACTIONS_OWNER_INVALID")
        return f"github:{run}:{attempt}"
    path = root / ".pipeline/owner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = f"clone:{uuid4().hex}"
    # A local identity file is not a lock or authorization source. Only remote
    # CAS grants ownership. Exclusive creation just keeps a clone's ID stable.
    descriptor, temporary = tempfile.mkstemp(prefix=".owner-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"owner_id": candidate}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        return candidate
    except FileExistsError:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))["owner_id"]
            if not isinstance(value, str) or not re.fullmatch(r"clone:[a-f0-9]{32}", value):
                raise ValueError("invalid owner")
            return value
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CoordinationError("SHARED_OWNER_INVALID") from exc
    finally:
        Path(temporary).unlink(missing_ok=True)


TERMINAL = {"PUBLISHED", "PUBLISH_UNCERTAIN", "REJECTED", "DISPATCH_UNCERTAIN", "CANCELLED"}
SEALED = {"QUEUED", "PUBLISHING", *TERMINAL}
PHASES = {"IDLE", "CANDIDATE", "UNIQUE", "AUTHORING", "LOCAL_VALIDATION", "LOCAL_REPAIR",
          "MEDIA_PREFLIGHT", "READY_TO_QUEUE", "VALIDATION_FAILED", *SEALED}


class SharedCoordinator:
    def __init__(self, root: Path, backend=None, owner_id: str | None = None, lease_seconds: float = 120,
                 *, clock_grace_seconds: float = 2):
        self.root = Path(root).resolve()
        self.backend = backend or GitHubRefBackend(self.root)
        self.owner_id = owner_id or _owner(self.root)
        if not isinstance(self.owner_id, str) or not self.owner_id or len(self.owner_id) > 200:
            raise CoordinationError("SHARED_OWNER_INVALID")
        if (not math.isfinite(lease_seconds) or not math.isfinite(clock_grace_seconds)
                or lease_seconds <= 0 or clock_grace_seconds < 0):
            raise ValueError("positive lease and nonnegative clock grace required")
        self.lease_seconds = lease_seconds
        self.clock_grace_seconds = clock_grace_seconds

    @staticmethod
    def _empty(channel):
        return {"schema_version": 1, "channel": channel, "generation": 0, "version": 0,
                "owner_id": "", "slug": "", "request_id": "", "phase": "IDLE", "closed_slugs": {},
                "lease_expires_at": 0, "heartbeat_at": 0, "mutation_allowed": False,
                "publisher_started": False, "released": True}

    def _read(self, channel):
        state, revision, now = self.backend.read(channel)
        state = deepcopy(state) if state is not None else self._empty(channel)
        if (state.get("schema_version") != 1 or state.get("channel") != channel
                or type(state.get("generation")) is not int or state["generation"] < 0
                or type(state.get("version")) is not int or state["version"] < 0
                or state.get("phase") not in PHASES
                or any(type(state.get(key)) is not bool for key in ("mutation_allowed", "publisher_started", "released"))
                or any(not isinstance(state.get(key), str) for key in ("slug", "request_id", "owner_id"))
                or not isinstance(state.get("closed_slugs"), dict)
                or type(state.get("lease_expires_at")) not in (int, float)
                or not math.isfinite(state["lease_expires_at"]) or not math.isfinite(now)
                or type(state.get("heartbeat_at")) not in (int, float) or not math.isfinite(state["heartbeat_at"])
                or any(not isinstance(record, dict) for record in state["closed_slugs"].values())):
            raise CoordinationError("SHARED_STATE_INVALID")
        if state["phase"] != "IDLE":
            try:
                safe_slug(state["slug"])
                safe_request(state["request_id"])
                if channel_for(state["slug"]) != channel or not state["owner_id"]:
                    raise ValueError("invalid channel owner")
            except (PipelineError, ValueError) as exc:
                raise CoordinationError("SHARED_STATE_INVALID") from exc
        if ((state["phase"] in SEALED and state["mutation_allowed"])
                or (state["publisher_started"] and state["mutation_allowed"])):
            raise CoordinationError("SHARED_STATE_INVALID")
        return state, revision, now

    @staticmethod
    def _token(state, revision):
        keys = ("channel", "owner_id", "slug", "request_id", "generation", "version", "phase", "lease_expires_at")
        return {**{key: state[key] for key in keys}, "revision": revision}

    def _write(self, state, revision, now, files=None):
        state["version"] += 1
        state["heartbeat_at"] = now
        changes = {_path(path): data for path, data in (files or {}).items()}
        if any(data is not None and not isinstance(data, bytes) for data in changes.values()):
            raise TypeError("mutation files must be bytes or None")
        # Immutable per-slug tombstones replace an ever-growing channel index.
        # The tombstone and fence are committed together; lookup never lists.
        for slug, record in state["closed_slugs"].items():
            changes[f".pipeline/closed/{safe_slug(slug)}.json"] = _json({"slug": slug, **record})
        state["closed_slugs"] = {slug: record for slug, record in state["closed_slugs"].items()
                                 if slug == state["slug"]}
        new_revision, _ = self.backend.compare_and_swap(state["channel"], revision, state, changes)
        return self._token(state, new_revision)

    def _closed(self, slug, state, revision):
        return state["closed_slugs"].get(slug) or self.backend.read_closed(slug, revision)

    @staticmethod
    def _scoped_files(state, files, *, publication=False, terminal=False):
        slug = state["slug"]
        metadata = {f".pipeline/episodes/{slug}.json", f".pipeline/artifacts/{slug}.json"}
        for path in files or {}:
            _path(path)
            if path in metadata:
                continue
            if publication and path == f".publication-attempts/{slug}.json":
                continue
            if not terminal and not publication:
                if path.startswith(f"episodes/{slug}/") or path == f".publish-queue/{slug}.txt":
                    continue
                if any(path.startswith(f"{directory}/{slug}--") and path.endswith(".json")
                       and len(PurePosixPath(path).parts) == 2
                       for directory in (".episode-check", ".duplicate-check", ".duplicate-check-nostalgia")):
                    continue
            raise CoordinationError("SHARED_CONTROL_PATH_FORBIDDEN", "A reservation can write only its own episode's files.")

    def read_files(self, paths: list[str], revision: str) -> dict[str, bytes | None]:
        return self.backend.read_files(paths, revision)

    def _expired(self, state, now):
        return state.get("released") is True or now > state["lease_expires_at"] + self.clock_grace_seconds

    def _checked(self, token, *, mutation=False, permit_expired=False):
        if not isinstance(token, dict) or token.get("channel") not in {"default", "nostalgia"}:
            raise CoordinationError("SHARED_TOKEN_INVALID")
        state, revision, now = self._read(token["channel"])
        identity = ("owner_id", "slug", "request_id", "generation", "version")
        if token.get("owner_id") != self.owner_id or any(state.get(key) != token.get(key) for key in identity):
            raise CoordinationError("SHARED_STALE_FENCE")
        if not permit_expired and (state.get("released") or now >= state["lease_expires_at"]):
            raise CoordinationError("SHARED_LEASE_EXPIRED")
        if mutation and (not state.get("mutation_allowed") or state.get("publisher_started")
                         or state["slug"] in state["closed_slugs"] or state["phase"] in SEALED):
            raise CoordinationError("SHARED_MUTATION_CLOSED")
        return state, revision, now

    def status(self, slug: str) -> dict:
        safe_slug(slug)
        state, revision, now = self._read(channel_for(slug))
        return {**state, "revision": revision, "server_now": now,
                "token": self._token(state, revision), "requested_slug_closed": self._closed(slug, state, revision)}

    reconcile = status

    def acquire(self, slug: str, request_id: str, expected_generation: int | None = None,
                *, initial_phase: str = "AUTHORING") -> dict:
        safe_slug(slug)
        safe_request(request_id)
        if initial_phase not in {"CANDIDATE", "AUTHORING"}:
            raise CoordinationError("SHARED_PHASE_INVALID")
        state, revision, now = self._read(channel_for(slug))
        if self._closed(slug, state, revision):
            raise CoordinationError("SHARED_SLUG_CLOSED")
        if expected_generation is not None and state["generation"] != expected_generation:
            raise CoordinationError("SHARED_STALE_FENCE")
        active = state["phase"] not in {"IDLE", *TERMINAL}
        if active and state["slug"] != slug:
            raise CoordinationError("SHARED_SLOT_OCCUPIED", "Only the active episode may be recovered; expiration does not authorize a different slug.")
        if active and state["request_id"] != request_id:
            raise CoordinationError("SHARED_REQUEST_MISMATCH")
        if active and not self._expired(state, now):
            if state["owner_id"] == self.owner_id:
                return self._token(state, revision)
            raise CoordinationError("SHARED_LEASE_BUSY")
        if state.get("publisher_started") and active:
            raise CoordinationError("SHARED_SLUG_CLOSED")
        state.update(owner_id=self.owner_id, slug=slug, request_id=request_id,
                     generation=state["generation"] + 1, lease_expires_at=now + self.lease_seconds,
                     released=False, mutation_allowed=state["phase"] not in SEALED if active else True,
                     publisher_started=False, phase=state["phase"] if active else initial_phase)
        return self._write(state, revision, now)

    def assert_current(self, token: dict, *, mutation: bool = False) -> dict:
        state, revision, _ = self._checked(token, mutation=mutation)
        return self._token(state, revision)

    def heartbeat(self, token: dict) -> dict:
        state, revision, now = self._checked(token)
        state["lease_expires_at"] = now + self.lease_seconds
        return self._write(state, revision, now)

    def release(self, token: dict) -> dict:
        state, revision, now = self._checked(token)
        state.update(generation=state["generation"] + 1, released=True, lease_expires_at=now)
        return self._write(state, revision, now)

    def handoff(self, token: dict, new_owner_id: str) -> dict:
        if not isinstance(new_owner_id, str) or not new_owner_id or len(new_owner_id) > 200:
            raise CoordinationError("SHARED_OWNER_INVALID")
        state, revision, now = self._checked(token)
        state.update(generation=state["generation"] + 1, owner_id=new_owner_id,
                     lease_expires_at=now + self.lease_seconds, released=False)
        return self._write(state, revision, now)

    def commit_mutation(self, token: dict, files: dict[str, bytes | None], *, phase: str | None = None,
                        request_id: str | None = None) -> dict:
        state, revision, now = self._checked(token, mutation=True)
        self._scoped_files(state, files)
        if phase:
            if phase not in PHASES or phase in {"IDLE", "PUBLISHING", *TERMINAL}:
                raise CoordinationError("SHARED_PHASE_INVALID")
            state["phase"] = phase
            if phase == "QUEUED":
                state["mutation_allowed"] = False
        if request_id is not None and request_id != state["request_id"]:
            state.update(request_id=safe_request(request_id), generation=state["generation"] + 1)
        state["lease_expires_at"] = now + self.lease_seconds
        return self._write(state, revision, now, files)

    def set_phase(self, token: dict, phase: str, files: dict[str, bytes | None] | None = None) -> dict:
        if phase in TERMINAL:
            return self.finish(token, phase, files=files)
        state, revision, _ = self._checked(token)
        if state["phase"] == phase and not files:
            return self._token(state, revision)
        return self.commit_mutation(token, files or {}, phase=phase)

    def change_request(self, token: dict, request_id: str) -> dict:
        return self.commit_mutation(token, {}, request_id=request_id)

    def validate_dispatch(self, slug: str, request_id: str, local_state: dict) -> dict:
        token = local_state.get("shared_token") or local_state.get("reservation_token")
        if not isinstance(token, dict):
            state, revision, _ = self._read(channel_for(safe_slug(slug)))
            if local_state.get("shared_generation") != state["generation"]:
                raise CoordinationError("SHARED_STALE_FENCE")
            token = self._token(state, revision)
        if not isinstance(token, dict) or token.get("slug") != slug or token.get("request_id") != request_id:
            raise CoordinationError("SHARED_STALE_FENCE")
        current = self.assert_current(token)
        if current["phase"] != "QUEUED":
            raise CoordinationError("SHARED_DISPATCH_NOT_READY")
        return current

    def close_for_publication(self, token: dict, payload_fingerprint: str,
                              files: dict[str, bytes | None] | None = None) -> dict:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", payload_fingerprint):
            raise CoordinationError("SHARED_PAYLOAD_FINGERPRINT_REQUIRED")
        state, revision, now = self._checked(token)
        if (state["phase"] != "QUEUED" or state.get("publisher_started")
                or state["slug"] in state["closed_slugs"]):
            raise CoordinationError("SHARED_PUBLICATION_NOT_READY")
        self._scoped_files(state, files, publication=True)
        state.update(generation=state["generation"] + 1, publisher_started=True,
                     mutation_allowed=False, phase="PUBLISHING", payload_fingerprint=payload_fingerprint)
        state["closed_slugs"][state["slug"]] = {
            "request_id": state["request_id"], "generation": state["generation"],
            "closed_at": now, "reason": "PUBLISHING", "payload_fingerprint": payload_fingerprint}
        return self._write(state, revision, now, files)

    def update_publication(self, token: dict, files: dict[str, bytes | None]) -> dict:
        state, revision, now = self._checked(token)
        if state["phase"] != "PUBLISHING" or not state.get("publisher_started"):
            raise CoordinationError("SHARED_PUBLICATION_NOT_STARTED")
        if set(files) != {f".publication-attempts/{state['slug']}.json"} or any(value is None for value in files.values()):
            raise CoordinationError("SHARED_CONTROL_PATH_FORBIDDEN")
        state["lease_expires_at"] = now + self.lease_seconds
        return self._write(state, revision, now, files)

    def resume_publication(self, slug: str, request_id: str, *, expected_generation: int,
                           expected_version: int) -> dict:
        """Renew the same publisher session without reopening any send attempt.

        Distinct jobs of one Actions run share an owner. A gap between jobs can
        exceed a lease, but only that owner observing the exact latest fence can
        renew it. Recovery racing this CAS wins exclusively and stays terminal.
        """
        state, revision, now = self._read(channel_for(safe_slug(slug)))
        if (state["slug"] != slug or state["request_id"] != safe_request(request_id)
                or state["owner_id"] != self.owner_id or state["generation"] != expected_generation
                or state["version"] != expected_version):
            raise CoordinationError("SHARED_STALE_FENCE")
        if (state["phase"] != "PUBLISHING" or not state["publisher_started"]
                or state["mutation_allowed"] or not self._closed(slug, state, revision)):
            raise CoordinationError("SHARED_PUBLICATION_NOT_STARTED")
        state.update(generation=state["generation"] + 1, lease_expires_at=now + self.lease_seconds, released=False)
        return self._write(state, revision, now)

    def finish(self, token: dict, outcome: str, files: dict[str, bytes | None] | None = None) -> dict:
        if outcome not in TERMINAL:
            raise CoordinationError("SHARED_PHASE_INVALID")
        state, revision, now = self._read(token.get("channel", ""))
        if (state["phase"] == outcome and state["slug"] == token.get("slug")
                and state["request_id"] == token.get("request_id") and state["owner_id"] == self.owner_id
                and state["generation"] == token.get("generation", -2) + 1
                and state["version"] == token.get("version", -2) + 1):
            return self._token(state, revision)  # Previous CAS succeeded but its response was lost.
        state, revision, now = self._checked(token, permit_expired=True)
        self._scoped_files(state, files, publication=state.get("publisher_started", False), terminal=True)
        if outcome in {"PUBLISHED", "PUBLISH_UNCERTAIN"} and not state.get("publisher_started"):
            raise CoordinationError("SHARED_PUBLICATION_NOT_STARTED")
        state.update(generation=state["generation"] + 1, mutation_allowed=False,
                     phase=outcome, released=True, lease_expires_at=now)
        state["closed_slugs"].setdefault(state["slug"], {
            "request_id": state["request_id"], "generation": state["generation"], "closed_at": now,
            "reason": outcome})
        state["closed_slugs"][state["slug"]]["outcome"] = outcome
        return self._write(state, revision, now, files)

    def abandon_expired(self, slug: str, request_id: str, outcome: str = "DISPATCH_UNCERTAIN",
                        expected_generation: int | None = None) -> dict:
        if outcome not in {"DISPATCH_UNCERTAIN", "CANCELLED"}:
            raise CoordinationError("SHARED_PHASE_INVALID")
        state, revision, now = self._read(channel_for(safe_slug(slug)))
        closed = self._closed(slug, state, revision)
        if (closed and closed.get("request_id") == request_id
                and (closed.get("outcome") or closed.get("reason")) == outcome):
            return {**self._token(state, revision), "requested_slug_closed": closed}
        if state["slug"] != slug or state["request_id"] != safe_request(request_id):
            raise CoordinationError("SHARED_STALE_FENCE")
        if expected_generation is not None and state["generation"] != expected_generation:
            raise CoordinationError("SHARED_STALE_FENCE")
        if not self._expired(state, now):
            raise CoordinationError("SHARED_LEASE_BUSY")
        if state.get("publisher_started"):
            raise CoordinationError("SHARED_PUBLICATION_ALREADY_STARTED")
        state.update(generation=state["generation"] + 1, owner_id=self.owner_id,
                     mutation_allowed=False, phase=outcome, released=True, lease_expires_at=now)
        state["closed_slugs"][slug] = {"request_id": request_id, "generation": state["generation"],
                                       "closed_at": now, "reason": outcome}
        return self._write(state, revision, now)

    terminate_uncertain = abandon_expired

    def recover_publication_expired(self, slug: str, request_id: str, *, outcome: str = "PUBLISH_UNCERTAIN",
                                    files: dict[str, bytes | None] | None = None,
                                    expected_version: int | None = None) -> dict:
        """Release a dead publisher's slot without ever reopening its slug.

        Only conservative uncertainty is inferred here. A caller with positive
        platform evidence can finish its own current token as PUBLISHED.
        """
        if outcome != "PUBLISH_UNCERTAIN":
            raise CoordinationError("SHARED_RECOVERY_OUTCOME_INVALID")
        state, revision, now = self._read(channel_for(safe_slug(slug)))
        if state["slug"] != slug or state["request_id"] != safe_request(request_id):
            raise CoordinationError("SHARED_STALE_FENCE")
        if state["phase"] == outcome and slug in state["closed_slugs"]:
            return self._token(state, revision)
        if expected_version is not None and state["version"] != expected_version:
            raise CoordinationError("SHARED_STALE_FENCE")
        if not state.get("publisher_started") or slug not in state["closed_slugs"]:
            raise CoordinationError("SHARED_PUBLICATION_NOT_STARTED")
        if not self._expired(state, now):
            raise CoordinationError("SHARED_LEASE_BUSY")
        self._scoped_files(state, files, publication=True, terminal=True)
        state.update(generation=state["generation"] + 1, owner_id=self.owner_id,
                     mutation_allowed=False, phase=outcome, released=True, lease_expires_at=now)
        state["closed_slugs"][slug]["outcome"] = outcome
        return self._write(state, revision, now, files)

    def update_closed_publication(self, slug: str, request_id: str, files: dict[str, bytes | None],
                                  expected_revision: str) -> dict:
        """Persist additional receipt evidence without changing any live fence.

        The receipt observation and write must use the same immutable revision.
        A newer episode may own this channel: its generation/version/lease must
        remain byte-for-byte unchanged by a historical receipt update.
        """
        state, revision, _ = self._read(channel_for(safe_slug(slug)))
        if revision != expected_revision:
            raise CASConflict()
        closed = self._closed(slug, state, revision)
        if (not closed or closed.get("request_id") != safe_request(request_id)
                or (closed.get("outcome") or closed.get("reason")) not in {"PUBLISHED", "PUBLISH_UNCERTAIN"}):
            raise CoordinationError("SHARED_PUBLICATION_NOT_FINISHED")
        if set(files) != {f".publication-attempts/{slug}.json"} or any(not isinstance(value, bytes) for value in files.values()):
            raise CoordinationError("SHARED_CONTROL_PATH_FORBIDDEN")
        new_revision, _ = self.backend.compare_and_swap(state["channel"], revision, state, files)
        return self._token(state, new_revision)


_OVERRIDES: ContextVar[dict[str, SharedCoordinator]] = ContextVar("shared_coordinators", default={})


def coordinator_for(root: Path) -> SharedCoordinator:
    return override_for(root) or SharedCoordinator(Path(root))


def override_for(root: Path) -> SharedCoordinator | None:
    return _OVERRIDES.get().get(str(Path(root).resolve()))


@contextmanager
def using_coordinator(root: Path, coordinator: SharedCoordinator):
    """Explicit dependency injection for isolated workspaces and offline tests."""
    token = _OVERRIDES.set({**_OVERRIDES.get(), str(Path(root).resolve()): coordinator})
    try:
        yield coordinator
    finally:
        _OVERRIDES.reset(token)
