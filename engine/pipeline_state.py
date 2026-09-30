"""Small durable continuity ledger. Publication evidence is monotonic and fail-closed.

Normal reads use an active pointer and exact paths. Recovery uses Git's index,
never a directory listing or the possibly truncated GitHub Contents API.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any

_SAFE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,159}\Z")
_SLUG = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*\Z")
TERMINAL = {"PUBLISHED", "PUBLISH_UNCERTAIN", "REJECTED", "DISPATCH_UNCERTAIN", "CANCELLED"}
ATTEMPTED = {"PUBLISHING", "PUBLISHED", "PUBLISH_UNCERTAIN"}


class PipelineError(RuntimeError):
    def __init__(self, code: str, detail: str, *, recoverable: bool = False):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.recoverable = code, detail, recoverable

    def record(self, *, slug: str = "", request_id: str = "", stage: str = "CONTINUITY", commit_sha: str = "") -> dict:
        error = dict(error_code=self.code, error_class="local preflight", recoverable=self.recoverable,
                     stage=stage, slug=slug, request_id=request_id, target=slug, detail=self.detail,
                     commit_sha=commit_sha)
        return {**error, "errors": [error]}


def safe_slug(value: str) -> str:
    if not _SLUG.fullmatch(value):
        raise PipelineError("INVALID_SLUG", "Use a lowercase episode slug, never a path.")
    return value


def safe_request(value: str) -> str:
    if not _SAFE.fullmatch(value):
        raise PipelineError("INVALID_REQUEST_ID", "request_id must be a nonempty safe identifier.")
    return value


def channel_for(slug: str) -> str:
    return "nostalgia" if slug.startswith("nostalgia_") else "default"


def read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class PipelineStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.directory = self.root / ".pipeline"
        self.index_path = self.directory / "state.json"
        self._legacy_attempts: set[str] | None = None
        self.contract = read_json(self.root / "config/pipeline-contract.json")
        if self.contract is None:
            self.contract = read_json(Path(__file__).resolve().parents[1] / "config/pipeline-contract.json")
        if self.contract is None:
            raise PipelineError("CONTRACT_MISSING", "config/pipeline-contract.json is required.")

    def episode_path(self, slug: str) -> Path:
        return self.directory / "episodes" / f"{safe_slug(slug)}.json"

    @contextmanager
    def lock(self):
        from engine.process_lock import process_lock
        try:
            with process_lock(self.directory / ".lock"):
                pending = self._pending_transaction()
                if pending:
                    atomic_json(self.episode_path(pending["episode"]["slug"]), pending["episode"])
                    atomic_json(self.index_path, pending["index"])
                    (self.directory / "transaction.json").unlink()
                yield
        except TimeoutError as exc:
            raise PipelineError("STATE_BUSY", "An active writer holds the state lock; retry after it finishes.", recoverable=True) from exc

    def _pending_transaction(self):
        path = self.directory / "transaction.json"
        if not path.exists():
            return None
        value = read_json(path)
        episode = (value or {}).get("episode", {})
        index = (value or {}).get("index", {})
        if (not isinstance(episode, dict) or not isinstance(index, dict)
                or not self._valid(episode, str(episode.get("slug", "")))
                or not isinstance(index.get("active"), dict)
                or set(index["active"]) != {"default", "nostalgia"}):
            raise PipelineError("STATE_TRANSACTION_INVALID", "Reconcile the exact interrupted transaction; never assume an empty slot.")
        safe_slug(episode["slug"])
        return value

    def _empty_index(self) -> dict:
        return {"schema_version": 1, "active": {"default": None, "nostalgia": None}, "reconciled": True}

    def _valid(self, value: dict | None, slug: str) -> bool:
        return bool(value and value.get("schema_version") == 1 and value.get("slug") == slug
                    and value.get("stage") in self.contract["stages"] and value.get("request_id"))

    def _facts(self, slug: str, value: dict | None = None) -> dict:
        """Read exact immutable evidence even when the ledger was lost/corrupted."""
        value = value or {}
        attempted = bool(value.get("publisher_started") or value.get("ever_published_or_attempted")
                         or value.get("stage") in ATTEMPTED)
        marker = self.root / ".publication-attempts" / f"{slug}.json"
        marker_stage = ""
        if marker.exists():  # Corruption can never unlock a possible publication.
            attempted = True
            data = read_json(marker)
            marker_stage = str((data or {}).get("stage", "PUBLISHING" if data else "PUBLISH_UNCERTAIN"))
            if marker_stage not in ATTEMPTED:
                marker_stage = "PUBLISH_UNCERTAIN"
        queue = self.root / ".publish-queue" / f"{slug}.txt"
        if queue.exists() and not value.get("queue_created"):
            # Pre-ledger queues do not prove that no publisher already ran.
            attempted = True
        if not attempted and not self._valid(value, slug) and slug in self._legacy_publication_requests():
            attempted = True
        return {"attempted": attempted, "queue": queue.exists() or bool(value.get("queue_created")), "marker_stage": marker_stage}

    def _legacy_publication_requests(self) -> set[str]:
        if self._legacy_attempts is not None:
            return self._legacy_attempts
        found: set[str] = set()
        if (self.root / ".git").exists():
            result = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".publish-only/*.json", ".publish-retry/*.txt"], cwd=self.root, capture_output=True, check=False)
            if result.returncode:
                raise PipelineError("INDEX_UNAVAILABLE", "Cannot verify legacy publication attempts.", recoverable=True)
            for name in result.stdout.decode("utf-8").split("\0"):
                if not name:
                    continue
                path = self.root / name
                try:
                    if path.suffix == ".json":
                        data = read_json(path) or {}
                        candidate = str(data.get("episode") or data.get("slug") or "")
                    else:
                        candidate = path.read_text(encoding="utf-8-sig").splitlines()[0].strip()
                    if _SLUG.fullmatch(candidate):
                        found.add(candidate)
                except (OSError, IndexError):
                    raise PipelineError("LEGACY_ATTEMPT_UNREADABLE", f"Cannot verify {name}; recover its exact contents before publication.")
        self._legacy_attempts = found
        return found

    def _load_episode(self, slug: str) -> dict:
        path = self.episode_path(slug)
        pending = self._pending_transaction()
        raw = pending["episode"] if pending and pending["episode"]["slug"] == slug else read_json(path)
        valid = self._valid(raw, slug)
        facts = self._facts(slug, raw)
        if valid:
            value = dict(raw)
            request_path = value.get("request_path")
            if (value["stage"] == "MEDIA_PREFLIGHT" and isinstance(request_path, str)
                    and request_path.startswith(".episode-check/") and ".." not in Path(request_path).parts
                    and not (self.root / request_path).is_file()):
                value.update(stage="VALIDATION_FAILED", local_preflight_passed=False,
                             errors=[{"error_code": "REQUEST_FILE_MISSING", "detail": "Re-run the local gate to create the missing request."}])
            if value["stage"] == "MEDIA_PREFLIGHT" and isinstance(request_path, str) and request_path.startswith(".episode-check/"):
                receipt = read_json(self.root / request_path)
                if not receipt or receipt.get("slug") != slug or receipt.get("request_id") != value["request_id"]:
                    value.update(stage="VALIDATION_FAILED", local_preflight_passed=False,
                                 errors=[{"error_code": "REQUEST_FILE_INVALID", "detail": "Revalidate with a new request ID; preserve already committed requests."}])
        else:
            value = dict(schema_version=1, slug=slug, request_id=f"legacy-{slug}",
                         channel=channel_for(slug), stage="AUTHORING", reconciled=True)
            if path.exists():
                value["reconciliation_required"] = True
        if facts["attempted"]:
            value["publisher_started"] = True
            value["ever_published_or_attempted"] = True
            if value["stage"] in {"PUBLISHED", "PUBLISH_UNCERTAIN"}:
                pass  # An observed terminal outcome must never be reverted by a stale claim.
            elif facts["marker_stage"]:
                value["stage"] = facts["marker_stage"]
            elif value["stage"] not in ATTEMPTED:
                value["stage"] = "PUBLISH_UNCERTAIN"
        value["queue_created"] = facts["queue"]
        return self._decorate(value)

    def _decorate(self, value: dict) -> dict:
        value = dict(value)
        stage = value["stage"]
        attempted = bool(value.get("publisher_started") or value.get("ever_published_or_attempted") or stage in ATTEMPTED)
        sealed = attempted or bool(value.get("closed_generation")) or stage == "QUEUED"
        value.update(active=stage not in TERMINAL, publisher_started=attempted,
                     ever_published_or_attempted=attempted,
                     EVER_PUBLISHED_OR_ATTEMPTED="YES" if attempted else "NO",
                     REPUBLICATION_ALLOWED="NO" if attempted or value.get("closed_generation") else "YES",
                     mutation_allowed=not sealed, recovery_mutation_allowed=not sealed,
                     republication_allowed=not attempted and not bool(value.get("closed_generation")), can_create_new_episode=stage in TERMINAL,
                     authoring_finished=bool(value.get("authoring_finished") or stage in {"LOCAL_VALIDATION", "LOCAL_REPAIR", "MEDIA_PREFLIGHT", "READY_TO_QUEUE", "QUEUED"} | ATTEMPTED),
                     media_preflight_passed=bool(value.get("media_preflight_passed") or stage in {"READY_TO_QUEUE", "QUEUED"}),
                     queue_created=bool(value.get("queue_created") or stage == "QUEUED"))
        value["next_action"] = {
            "CANDIDATE": "check_duplicate", "UNIQUE": "author", "AUTHORING": "author",
            "LOCAL_VALIDATION": "validate_locally", "LOCAL_REPAIR": "repair_batch",
            "VALIDATION_FAILED": "repair_then_validate", "MEDIA_PREFLIGHT": "wait_for_correlated_run",
            "READY_TO_QUEUE": "create_queue", "QUEUED": "wait_for_publisher",
            "PUBLISHING": "observe_only", "PUBLISHED": "create_new_episode",
            "PUBLISH_UNCERTAIN": "observe_only_never_republish", "REJECTED": "create_new_episode",
            "DISPATCH_UNCERTAIN": "create_new_episode_never_retry_slug", "CANCELLED": "create_new_episode",
        }[stage]
        return value

    def _indexed_paths(self):
        """Git index is complete, NUL-delimited and streamed, including large repos."""
        if not (self.root / ".git").exists():
            return
        command = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".pipeline/episodes/*.json",
                   "episodes/*/story.json", ".episode-check/*.json", ".publication-attempts/*.json"]
        with subprocess.Popen(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            pending = b""
            while chunk := process.stdout.read(65536):
                parts = (pending + chunk).split(b"\0")
                pending = parts.pop()
                for part in parts:
                    yield part.decode("utf-8")
            if process.wait() != 0 and (self.root / ".git").exists():
                raise PipelineError("INDEX_UNAVAILABLE", "Could not reconcile the Git index; no directory listing was inferred complete.", recoverable=True)

    def _reconcile_index(self) -> dict:
        index = self._empty_index()
        candidates: set[str] = set()
        recent_by_channel = {channel: self._legacy_recent_paths(channel) for channel in ("default", "nostalgia")}
        for filename in self._indexed_paths():
            path = Path(filename)
            if filename.startswith("episodes/"):
                slug = path.parts[1]
            elif filename.startswith(".episode-check/"):
                data = read_json(self.root / path) or {}
                slug = str(data.get("slug") or "")
            else:
                slug = path.stem
            if _SLUG.fullmatch(slug):
                recent = recent_by_channel[channel_for(slug)]
                if (filename.startswith((".pipeline/", ".publication-attempts/")) or
                        recent is None or filename in recent or self._valid(read_json(self.episode_path(slug)), slug)):
                    candidates.add(slug)
        conflicts = {}
        for slug in sorted(candidates):
            value = self._load_episode(slug)
            if not value["active"]:
                continue
            channel = channel_for(slug)
            if index["active"][channel] is None:
                index["active"][channel] = slug
            else:
                conflicts.setdefault(channel, [index["active"][channel]]).append(slug)
        if conflicts:
            index["conflict_counts"] = {channel: len(slugs) for channel, slugs in conflicts.items()}
            index["conflicts"] = {channel: slugs[:20] for channel, slugs in conflicts.items()}
        return index

    def _legacy_recent_paths(self, channel: str) -> set[str] | None:
        """Migrate the old queue boundary once; archived samples never become active."""
        if not (self.root / ".git").exists():
            return None
        queue_paths = ([".publish-queue/nostalgia_*.txt"] if channel == "nostalgia" else
                       [".publish-queue/*.txt", ":(exclude).publish-queue/nostalgia_*.txt"])
        boundary = subprocess.run(["git", "log", "-1", "--format=%H", "--diff-filter=A", "--", *queue_paths], cwd=self.root, capture_output=True, text=True, check=False)
        if boundary.returncode:
            raise PipelineError("HISTORY_UNAVAILABLE", "A complete Git history is needed for one-time continuity reconciliation.", recoverable=True)
        sha = boundary.stdout.strip()
        if not sha:
            return None
        patterns = ["episodes/*/story.json", ".episode-check/*.json"]
        history = subprocess.run(["git", "log", f"{sha}..HEAD", "--format=", "--name-only", "--diff-filter=AM", "--", *patterns], cwd=self.root, capture_output=True, text=True, check=True)
        local = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", *patterns], cwd=self.root, capture_output=True, text=True, check=True)
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", *patterns], cwd=self.root, capture_output=True, text=True, check=True)
        return {line for output in (history.stdout, local.stdout, untracked.stdout) for line in output.splitlines() if line}

    def _index(self) -> dict:
        pending = self._pending_transaction()
        if pending:
            return pending["index"]
        value = read_json(self.index_path)
        if not (value and value.get("schema_version") == 1 and isinstance(value.get("active"), dict)
                and all(key in value["active"] for key in ("default", "nostalgia"))):
            return self._reconcile_index()
        # A dangling pointer requires indexed reconciliation, never silent reset.
        for slug in value["active"].values():
            if slug and (not isinstance(slug, str) or not _SLUG.fullmatch(slug)
                         or not self._valid(read_json(self.episode_path(slug)), slug)):
                return self._reconcile_index()
        return value

    def status(self, slug: str | None = None, channel: str = "default") -> dict:
        if slug:
            return self._load_episode(safe_slug(slug))
        if channel not in ("default", "nostalgia"):
            raise PipelineError("INVALID_CHANNEL", channel)
        index = self._index()
        conflicts = index.get("conflicts", {}).get(channel, [])
        active = index["active"].get(channel)
        if active:
            value = self._load_episode(active)
        else:
            value = dict(active=False, slug=None, request_id=None, stage="IDLE", channel=channel,
                         authoring_finished=False, media_preflight_passed=False, queue_created=False,
                         publisher_started=False, mutation_allowed=True, recovery_mutation_allowed=True,
                         republication_allowed=True, can_create_new_episode=True, next_action="create_new_episode")
        if conflicts:
            value.update(reconciliation_required=True, conflicts=conflicts, can_create_new_episode=False,
                         next_action="reconcile_specific_requests")
        return value

    def reconcile(self, slug: str | None = None, observe_run=None) -> dict:
        if slug:
            from engine.coordination_runtime import sync_authority
            if sync_authority(self.root, slug, download=True) is not None:
                return self.status(slug)
        with self.lock():
            if slug:
                value = self._load_episode(safe_slug(slug))
                marker = read_json(self.root / ".publication-attempts" / f"{slug}.json") or {}
                if value.get("reconciliation_required") and not value["publisher_started"]:
                    from engine.pipeline_runtime import episode_fingerprint
                    raw = read_json(self.episode_path(slug)) or {}
                    paths = {name for name in self._indexed_paths() if name.startswith(".episode-check/")}
                    known_path = raw.get("request_path", "")
                    if isinstance(known_path, str) and known_path.startswith(".episode-check/") and ".." not in Path(known_path).parts:
                        paths.add(known_path)
                    fingerprint = episode_fingerprint(self.root, slug)
                    receipts = []
                    for name in paths:
                        request = read_json(self.root / name) or {}
                        if (request.get("slug") == slug and request.get("local_preflight_passed") is True
                                and request.get("episode_fingerprint") == fingerprint and request.get("request_id")):
                            receipts.append((name, request))
                    if len(receipts) == 1:
                        path, request = receipts[0]
                        value.update(stage="MEDIA_PREFLIGHT", request_id=safe_request(request["request_id"]),
                                     request_path=path, local_preflight_passed=True, fingerprint=fingerprint,
                                     authoring_finished=True, reconciliation_required=False)
                if observe_run and value["stage"] == "PUBLISHING" and marker.get("run_id"):
                    observed = observe_run(str(marker["run_id"]))
                    if str(observed.get("id")) != str(marker["run_id"]):
                        raise PipelineError("RUN_ID_MISMATCH", "Reconciliation received another publisher run.")
                    if (observed.get("status") == "completed" or
                            str(observed.get("run_attempt", marker.get("run_attempt"))) != str(marker.get("run_attempt"))):
                        # Without explicit final delivery proof, even a successful run is uncertain.
                        value["stage"] = "PUBLISH_UNCERTAIN"
                        value["observed_run_conclusion"] = observed.get("conclusion")
                self._save(value)
                return value
            index = self._reconcile_index()
            for candidate in index["active"].values():
                if candidate:
                    atomic_json(self.episode_path(candidate), self._load_episode(candidate))
            atomic_json(self.index_path, index)
        return self.status()

    def _save(self, value: dict) -> dict:
        value = self._decorate(value)
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
        index = self._index()
        channel = channel_for(value["slug"])
        current = index["active"].get(channel)
        if value["active"]:
            if current and current != value["slug"] and self._load_episode(current)["active"]:
                raise PipelineError("ACTIVE_EPISODE_EXISTS", f"Resume {current} before {value['slug']}.")
            index["active"][channel] = value["slug"]
        elif current == value["slug"]:
            index["active"][channel] = None
        from engine.coordination_runtime import persist_state
        persist_state(self.root, value, index)
        journal = self.directory / "transaction.json"
        atomic_json(journal, {"episode": value, "index": index})
        atomic_json(self.episode_path(value["slug"]), value)
        atomic_json(self.index_path, index)
        journal.unlink()
        return value

    def start(self, slug: str, request_id: str) -> dict:
        safe_slug(slug)
        safe_request(request_id)
        from engine.coordination_runtime import acquire_token, coordinator_for, sync_channel
        coordinator = coordinator_for(self.root)
        if coordinator is not None:
            coordinator.recover_orphan_candidate(channel_for(slug))
        sync_channel(self.root, channel_for(slug))
        acquire_token(self.root, slug, request_id, initial_phase="CANDIDATE")
        with self.lock():
            self.assert_mutation_allowed(slug)
            status = self.status(channel=channel_for(slug))
            if status.get("reconciliation_required"):
                raise PipelineError("RECONCILIATION_REQUIRED", "Multiple real unfinished episodes require specific reconciliation.")
            if status["active"] and status["slug"] != slug:
                raise PipelineError("ACTIVE_EPISODE_EXISTS", f"Resume {status['slug']}.")
            previous = read_json(self.episode_path(slug))
            if self._valid(previous, slug):
                if previous["request_id"] != request_id:
                    raise PipelineError("REQUEST_CONFLICT", "Resume the existing request_id for this episode.")
                if coordinator is not None:
                    authority = coordinator.status(slug)
                    if (authority.get("slug") == slug and authority.get("request_id") == request_id
                            and authority.get("candidate_claim_pending") is True):
                        # sync_channel materializes a local placeholder even
                        # before the first durable episode CAS. Completing
                        # start must commit the ledger and clear that flag.
                        return self._save(dict(schema_version=1, slug=slug, request_id=request_id,
                                               channel=channel_for(slug), stage="CANDIDATE", queue_created=False))
                return self._load_episode(slug)
            return self._save(dict(schema_version=1, slug=slug, request_id=request_id,
                                   channel=channel_for(slug), stage="CANDIDATE", queue_created=False))

    def assert_mutation_allowed(self, slug: str, *, check_remote: bool = True) -> None:
        from engine.coordination_runtime import in_private_staging
        if in_private_staging(self.root):
            return  # Disposable workspace; final authority is the fenced CAS.
        state = self._load_episode(safe_slug(slug))
        if not state["mutation_allowed"]:
            raise PipelineError("PUBLICATION_ALREADY_ATTEMPTED", f"{slug}: publication or a possible attempt permanently forbids mutation and republication.")
        if state.get("reconciliation_required"):
            raise PipelineError("STATE_INCONSISTENT", f"Reconcile the exact state for {slug} before mutation.")
        if check_remote:
            from engine.coordination_runtime import coordinator_for, token_path
            coordinator = coordinator_for(self.root)
            if coordinator is not None:
                shared = coordinator.status(slug)
                if shared.get("requested_slug_closed") or shared.get("publisher_started"):
                    raise PipelineError("PUBLICATION_ALREADY_ATTEMPTED", "Shared authority permanently closed this slug.")
                cached = read_json(token_path(self.root, slug))
                if cached:
                    coordinator.assert_current(cached, mutation=True)
                elif shared.get("slug") != slug or not shared.get("mutation_allowed"):
                    raise PipelineError("SHARED_RESERVATION_REQUIRED", "Acquire the authoritative reservation before mutation.")
        if check_remote and ((self.root / ".git").exists() or os.environ.get("GITHUB_ACTIONS") == "true"):
            from publishing.attempts import AttemptStore, PublicationLocked
            attempts = AttemptStore(self.root)
            if attempts.configuration_error:
                raise PipelineError("PUBLICATION_SAFETY_UNVERIFIED", attempts.configuration_error)
            if attempts.remote or attempts.in_actions:
                try:
                    attempts.assert_remote_unstarted(slug)
                except PublicationLocked as exc:
                    raise PipelineError("PUBLICATION_SAFETY_UNVERIFIED", str(exc)) from exc


    def transition(self, slug: str, stage: str, request_id: str | None = None, **evidence: Any) -> dict:
        safe_slug(slug)
        with self.lock():
            value = self._load_episode(slug)
            if request_id and request_id != value["request_id"]:
                # A legacy episode gets its first request identity exactly once.
                if (value["request_id"] != f"legacy-{slug}" and not
                        (stage == "LOCAL_VALIDATION" and value["stage"] in {"AUTHORING", "VALIDATION_FAILED", "LOCAL_REPAIR"})):
                    raise PipelineError("REQUEST_CONFLICT", "State belongs to a different request_id.")
                value["request_id"] = safe_request(request_id)
                value["local_preflight_passed"] = False
            before = value["stage"]
            if stage not in self.contract["stages"]:
                raise PipelineError("INVALID_STAGE", stage)
            if value["publisher_started"] and stage not in ATTEMPTED:
                raise PipelineError("PUBLICATION_ALREADY_ATTEMPTED", "Publication evidence cannot be cleared by a transition.")
            if stage != before and stage not in self.contract["transitions"][before]:
                raise PipelineError("INVALID_TRANSITION", f"{before} -> {stage}")
            if stage == "MEDIA_PREFLIGHT" and not (evidence.get("local_preflight_passed") or value.get("local_preflight_passed")):
                raise PipelineError("LOCAL_PASS_REQUIRED", "A local PASS is required before creating .episode-check.")
            for key in ("commit_sha", "run_id", "request_path", "workflow", "local_preflight_passed", "authoring_finished", "media_preflight_passed", "queue_created", "fingerprint", "errors"):
                if key in evidence:
                    value[key] = evidence[key]
            value["stage"] = stage
            return self._save(value)

    def mark_publisher_started(self, slug: str, request_id: str = "", commit_sha: str = "", run_id: str = "") -> dict:
        with self.lock():
            value = self._load_episode(safe_slug(slug))
            if value["stage"] == "PUBLISHED":
                raise PipelineError("PUBLICATION_ALREADY_ATTEMPTED", slug)
            value.update(stage="PUBLISHING", publisher_started=True, ever_published_or_attempted=True,
                         commit_sha=commit_sha or value.get("commit_sha", ""), run_id=run_id)
            if request_id:
                value["request_id"] = safe_request(request_id)
            return self._save(value)

    def mark_published(self, slug: str) -> dict:
        return self.transition(slug, "PUBLISHED")
