"""Integration of authoritative coordination; local ledgers are caches, never grants."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path

_STAGING = ContextVar("pipeline_private_staging", default=frozenset())


def in_private_staging(root):
    return str(Path(root).resolve()) in _STAGING.get()


@contextmanager
def private_staging(root):
    token = _STAGING.set(_STAGING.get() | {str(Path(root).resolve())})
    try:
        yield
    finally:
        _STAGING.reset(token)


def _factory(root):
    from engine.shared_coordination import coordinator_for
    return coordinator_for(Path(root))


def coordinator_for(root):
    if in_private_staging(root):
        return None
    return _factory(Path(root).resolve())


def token_path(root, slug):
    from engine.pipeline_state import safe_slug
    return Path(root) / ".pipeline" / "leases" / (safe_slug(slug) + ".json")


def _artifact_path(slug):
    from engine.pipeline_state import safe_slug
    return f".pipeline/artifacts/{safe_slug(slug)}.json"


def _episode_record_path(slug):
    from engine.pipeline_state import safe_slug
    return f".pipeline/episodes/{safe_slug(slug)}.json"


def _cache_base_path(root, slug):
    from engine.pipeline_state import safe_slug
    return Path(root) / ".pipeline/cache-applied" / (safe_slug(slug) + ".json")


def _artifact_signature(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def mark_cache_applied(root, slug, artifact_bytes):
    """Acknowledge an authored cache only after every committed byte is copied."""
    from engine.pipeline_state import atomic_json
    atomic_json(_cache_base_path(root, slug), {"artifact_sha256": _artifact_signature(artifact_bytes)})


def authoritative_mutation_base(root, slug, coordinator, token):
    """Reject a checkout whose authored cache missed a remote CAS commit.

    A local draft can differ from the remote files after a completed cache copy;
    its marker still identifies the exact remote base on which it was edited.
    """
    from engine.pipeline_state import PipelineError, read_json
    names = [_artifact_path(slug), _episode_record_path(slug)]
    remote = coordinator.read_files(names, token["revision"])
    artifact = remote[names[0]]
    signature = _artifact_signature(artifact)
    marker = read_json(_cache_base_path(root, slug))
    if marker and marker.get("applying"):
        raise PipelineError("STALE_LOCAL_CACHE", "Interrupted authoritative download; reconcile this episode before mutation.", recoverable=True)
    if artifact is not None and (marker or {}).get("artifact_sha256") != signature:
        if marker is not None:
            raise PipelineError("STALE_LOCAL_CACHE", "Authoritative authored files changed; reconcile this episode before mutation.", recoverable=True)
        # First use of an older checkout: establish the base only if every
        # locally present authored file matches the authoritative manifest.
        try:
            manifest = json.loads(artifact)
            expected = manifest["files"]
            if not isinstance(expected, dict):
                raise ValueError("invalid artifact files")
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineError("SHARED_ARTIFACT_INVALID", "Reconcile the authoritative artifact manifest.") from exc
        prefix = f"episodes/{slug}/"
        authored = {name: digest for name, digest in expected.items()
                    if name.startswith(prefix) and len(Path(name).parts) == 3
                    and Path(name).suffix in {".json", ".txt"}}
        local_dir = Path(root) / "episodes" / slug
        local_names = {path.relative_to(root).as_posix() for path in local_dir.iterdir()
                       if path.is_file() and path.suffix in {".json", ".txt"}} if local_dir.is_dir() else set()
        if set(authored) != local_names or any(
                hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() != digest
                for name, digest in authored.items()):
            raise PipelineError("STALE_LOCAL_CACHE", "Local authored files do not match the last known remote base; reconcile before mutation.", recoverable=True)
        mark_cache_applied(root, slug, artifact)
    return remote[names[1]], artifact


def save_token(root, token):
    from engine.pipeline_state import atomic_json, read_json
    if token:
        atomic_json(token_path(root, token["slug"]), token)
        record_path = Path(root) / ".pipeline/episodes" / (token["slug"] + ".json")
        record = read_json(record_path)
        if record and record.get("request_id") == token["request_id"]:
            record.update(shared_generation=token["generation"], shared_version=token["version"],
                          shared_owner_id=token["owner_id"])
            atomic_json(record_path, record)


def acquire_token(root, slug, request_id=None, *, initial_phase="AUTHORING"):
    from engine.pipeline_state import PipelineError, read_json
    coordinator = coordinator_for(root)
    if coordinator is None:  # Only a trusted private staging context or an injected test factory.
        return None, None
    cached = read_json(token_path(root, slug))
    remote = coordinator.status(slug)
    remote_token = remote.get("token")
    workflow_request = os.environ.get("PIPELINE_REQUEST_ID") if os.environ.get("GITHUB_ACTIONS") == "true" else None
    if workflow_request and remote.get("slug") == slug and workflow_request != (remote_token or {}).get("request_id"):
        raise PipelineError("WORKFLOW_REQUEST_STALE", "This workflow belongs to an older request; it cannot acquire the current generation.")
    local = read_json(Path(root) / ".pipeline/episodes" / (slug + ".json"))
    if (not cached and local and local.get("shared_generation") is not None
            and remote.get("slug") == slug and local["shared_generation"] != remote["generation"]):
        raise PipelineError("STALE_COORDINATION_GENERATION", "Reconcile this checkout before acquiring a newer reservation.", recoverable=True)
    if cached and remote_token and (cached["generation"] != remote_token["generation"]
                                   or cached["version"] != remote_token["version"]):
        raise PipelineError("STALE_COORDINATION_GENERATION", "Reconcile the authoritative reservation before continuing.", recoverable=True)
    identity = request_id or (remote_token or {}).get("request_id") or (cached or {}).get("request_id")
    if not identity:
        raise PipelineError("SHARED_RESERVATION_REQUIRED", "Start an explicit request before mutating this episode.")
    token = coordinator.acquire(slug, identity, initial_phase=initial_phase)
    save_token(root, token)
    return coordinator, token


def current_token(root, slug):
    from engine.pipeline_state import PipelineError, read_json
    coordinator = coordinator_for(root)
    if coordinator is None:
        return None, None
    token = read_json(token_path(root, slug))
    if not token:
        raise PipelineError("SHARED_RESERVATION_REQUIRED", "Acquire/reconcile the exact shared request first.")
    coordinator.assert_current(token)
    return coordinator, token


def release_token(root, coordinator, token):
    if coordinator is not None and token is not None:
        released = coordinator.release(token)
        save_token(root, released)
        return released
    return token


def reconcile_token(root, slug):
    coordinator = coordinator_for(root)
    if coordinator is None:
        return None
    status = coordinator.status(slug)
    token = status.get("token")
    # Reconciling records evidence only; acquire still checks owner and lease.
    if token:
        save_token(root, token)
    return status


def persist_state(root, value, index):
    """Store the ledger and shared phase in the same authoritative Git transaction."""
    from engine.pipeline_state import read_json
    coordinator = coordinator_for(root)
    if coordinator is None:
        return
    cached = read_json(token_path(root, value["slug"]))
    if value["stage"] in {"PUBLISHING", "PUBLISHED", "PUBLISH_UNCERTAIN", "DISPATCH_UNCERTAIN", "CANCELLED"}:
        return  # AttemptStore owns this authority, including crash recovery.
    coordinator, cached = acquire_token(root, value["slug"], (cached or {}).get("request_id") or value["request_id"])
    stage = value["stage"]
    if stage in {"PUBLISHING", "PUBLISHED", "PUBLISH_UNCERTAIN"}:
        # Attempts owns the irreversible transition. The local ledger merely
        # reflects that separately persisted evidence.
        return
    if cached["request_id"] != value["request_id"]:
        cached = coordinator.change_request(cached, value["request_id"])
    value.update(shared_generation=cached["generation"], shared_owner_id=cached["owner_id"])
    files = {".pipeline/episodes/" + value["slug"] + ".json": (json.dumps(value, sort_keys=True) + "\n").encode()}
    cached = coordinator.set_phase(cached, stage, files=files)
    save_token(root, cached)


def release_episode(root, slug):
    from engine.pipeline_state import read_json
    coordinator = coordinator_for(root)
    if coordinator is None:
        return
    token = read_json(token_path(root, slug))
    if token:
        try:
            release_token(root, coordinator, token)
        except Exception:
            authority = coordinator.status(slug)
            if authority.get("owner_id") == coordinator.owner_id and authority.get("released"):
                save_token(root, authority["token"])
            else:
                raise

def sync_authority(root, slug, *, download=False):
    """Reconcile exact shared evidence; never infer authority from a local index."""
    from engine.pipeline_state import PipelineStore, atomic_json, channel_for, read_json
    coordinator = coordinator_for(root)
    if coordinator is None:
        return None
    authority = coordinator.status(slug)
    closed = authority.get("requested_slug_closed")
    token = authority.get("token")
    if authority.get("slug") == slug and authority["phase"] == "IDLE":
        return authority
    if authority.get("slug") != slug and not closed:
        return authority
    store = PipelineStore(root)
    artifact = None
    payload = None
    authoritative_record = {}
    metadata = []
    recovered = {}
    exact_recovered = {}
    if hasattr(coordinator.backend, "read_files") and authority.get("slug") == slug:
        # Recover the exact remote ledger; arbitrary stale authored bytes are
        # never silently uploaded during reconciliation.
        name = ".pipeline/episodes/" + slug + ".json"
        artifact = coordinator.backend.read_files([_artifact_path(slug)], authority["revision"])[_artifact_path(slug)]
        metadata = ["episodes/" + slug + "/" + filename for filename in (
            "story.json", "assets.json", "timeline.json", "visual_candidates.json",
            "post.json", "sources.txt", "visual_resolution_report.json", "visual_usage.json")]
        episode_directory = Path(root) / "episodes" / slug
        if download and episode_directory.is_dir():
            metadata = sorted(set(metadata) | {path.relative_to(root).as_posix()
                for path in episode_directory.iterdir() if path.is_file() and path.suffix in {".json", ".txt"}})
        if download and artifact is not None:
            try:
                files = json.loads(artifact)["files"]
                if not isinstance(files, dict):
                    raise ValueError("invalid artifact map")
            except (KeyError, TypeError, ValueError) as exc:
                from engine.shared_coordination import CoordinationError
                raise CoordinationError("SHARED_ARTIFACT_INVALID", "Invalid authoritative artifact map.") from exc
            metadata = sorted(set(metadata) | {filename for filename in files
                if filename.startswith(f"episodes/{slug}/") and len(Path(filename).parts) == 3
                and Path(filename).suffix in {".json", ".txt"}})
        if not download:
            metadata = []  # Status refreshes evidence, never authored local drafts.
        recovered = coordinator.backend.read_files([name, *metadata], authority["revision"])
        payload = recovered.get(name)
        if payload:
            authoritative_record = json.loads(payload)
            request_path = authoritative_record.get("request_path", "")
            exact_paths = [f".publish-queue/{slug}.txt"]
            if (isinstance(request_path, str) and request_path.startswith(f".episode-check/{slug}--")
                    and len(Path(request_path).parts) == 2):
                exact_paths.append(request_path)
            exact_recovered = coordinator.backend.read_files(exact_paths, authority["revision"])
    from engine.process_lock import process_lock
    with process_lock(store.directory / ".lock"):
        if download and authority.get("slug") == slug:
            # A crash partway through the download must not leave a previous
            # applied marker authorizing a mixed local snapshot.
            atomic_json(_cache_base_path(root, slug), {"artifact_sha256": None, "applying": True})
        for filename in metadata:
            content = recovered.get(filename)
            destination = Path(root) / filename
            if destination.is_file() and destination.read_bytes() != content:
                previous = destination.read_bytes()
                backup = Path(root) / ".pipeline/conflicts" / slug / (hashlib.sha256(previous).hexdigest() + "-" + destination.name)
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(previous)
            if content is None:
                destination.unlink(missing_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        for filename, content in exact_recovered.items():
            if content is not None:
                destination = Path(root) / filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        # Remote reads can be slow. Merge their evidence into the latest local
        # ledger only while holding the same lock as other state writers.
        value = read_json(store.episode_path(slug)) or {
            "schema_version": 1, "slug": slug, "request_id": authority.get("request_id"),
            "channel": channel_for(slug),
        }
        if authority.get("slug") == slug:
            value.update(stage=authority["phase"], request_id=authority["request_id"],
                         shared_generation=authority["generation"], shared_owner_id=authority["owner_id"])
            if authority.get("publisher_started"):
                value.update(publisher_started=True, ever_published_or_attempted=True)
        if closed:
            value.update(stage=closed.get("outcome") or closed.get("reason") or "PUBLISH_UNCERTAIN",
                         request_id=closed["request_id"], closed_generation=closed["generation"])
        if payload:
            for key in ("commit_sha", "run_id", "request_path", "workflow", "fingerprint",
                        "local_preflight_passed", "media_preflight_passed", "queue_created"):
                if key in authoritative_record:
                    value[key] = authoritative_record[key]
        value = store._decorate(value)
        index = read_json(store.index_path) or store._empty_index()
        index.get("conflicts", {}).pop(channel_for(slug), None)
        if authority.get("slug"):
            index["active"][channel_for(slug)] = None if authority["phase"] in {
                "PUBLISHED", "PUBLISH_UNCERTAIN", "DISPATCH_UNCERTAIN", "REJECTED", "CANCELLED"
            } else authority["slug"]
        # An interrupted local journal is a cache too. Replaying it after this
        # reconciliation would resurrect stale facts over the shared snapshot.
        journal = store.directory / "transaction.json"
        if journal.is_file():
            raw = journal.read_bytes()
            backup = store.directory / "conflicts" / (hashlib.sha256(raw).hexdigest() + "-transaction.json")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(raw)
            journal.unlink()
        atomic_json(store.episode_path(slug), value)
        atomic_json(store.index_path, index)
        if download and authority.get("slug") == slug:
            mark_cache_applied(root, slug, artifact)
        if authority.get("slug") == slug and token:
            save_token(root, token)
    return authority


def sync_channel(root, channel):
    """Read the bounded shared channel before consulting a possibly stale index."""
    from engine.pipeline_state import PipelineStore, atomic_json, read_json
    coordinator = coordinator_for(root)
    if coordinator is None:
        return None
    authority = coordinator.status("nostalgia_channel_probe" if channel == "nostalgia" else "channel_probe")
    if authority.get("slug"):
        sync_authority(root, authority["slug"])
    else:
        store = PipelineStore(root)
        from engine.process_lock import process_lock
        with process_lock(store.directory / ".lock"):
            index = read_json(store.index_path) or store._empty_index()
            index["active"][channel] = None
            index.get("conflicts", {}).pop(channel, None)
            atomic_json(store.index_path, index)
    return authority
