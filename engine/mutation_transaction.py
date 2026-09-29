"""Prepare mutations privately and commit their authority with a generation-fenced CAS.

Writes in the disposable workspace are never publishable. Git is the authoritative
commit point; files copied back afterward are only a local cache. Publishers use
their separately verified private snapshot, never this mutable cache.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import fields, is_dataclass, replace
import functools
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import tempfile
import threading

from engine.coordination_runtime import (
    acquire_token, authoritative_mutation_base, coordinator_for,
    in_private_staging, mark_cache_applied, private_staging, release_token,
    save_token,
)


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _selected(root, slug):
    """Bounded to this episode and its outputs; no historical directory scan."""
    result = {}
    for suffix in (".json", ".log"):
        diagnostic = root / ".pipeline/diagnostics" / (slug + suffix)
        if diagnostic.is_file():
            result[diagnostic.relative_to(root).as_posix()] = _hash(diagnostic)
    for prefix in ("episodes/" + slug, "work/" + slug):
        directory = root / prefix
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_symlink():
                    raise RuntimeError("MUTATION_SYMLINK_FORBIDDEN")
                if path.is_file():
                    result[path.relative_to(root).as_posix()] = _hash(path)
    for prefix in ("output", ".episode-check", ".publish-queue"):
        directory = root / prefix
        if directory.is_dir():
            for path in directory.glob(slug + "*"):
                if path.is_file():
                    if path.is_symlink():
                        raise RuntimeError("MUTATION_SYMLINK_FORBIDDEN")
                    result[path.relative_to(root).as_posix()] = _hash(path)
    if (root / ".publish-ready").is_dir():
        for path in (root / ".publish-ready").rglob("*"):
            if path.is_symlink():
                raise RuntimeError("MUTATION_SYMLINK_FORBIDDEN")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = _hash(path)
    return result


def _copy_inputs(root, staging, slug):
    # These are copies, never hard links: render/repair can write only privately.
    for name in ("config", "assets", "templates", ".github", ".publish-ready"):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, staging / name,
                            ignore=shutil.ignore_patterns("*.lock", "owner.json", "leases", "operations"))
    for name in ("episodes/" + slug, "work/" + slug):
        if (root / name).is_dir():
            shutil.copytree(root / name, staging / name)
    for name in (".pipeline/state.json", ".pipeline/episodes/" + slug + ".json",
                 ".publication-attempts/" + slug + ".json"):
        if (root / name).is_file():
            (staging / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, staging / name)
    # Repetition validators still need authored history. These read-only copies
    # are for media policy, never for continuity/slot ownership.
    if (root / "episodes").is_dir():
        for episode in (root / "episodes").iterdir():
            if episode.is_dir() and episode.name != slug:
                for source in episode.iterdir():
                    if source.is_file() and source.suffix in {".json", ".txt"}:
                        target = staging / "episodes" / episode.name / source.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
    for name in ("output", ".episode-check", ".publish-queue"):
        directory = root / name
        if directory.is_dir():
            for source in directory.glob(slug + "*"):
                if source.is_file():
                    (staging / name).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, staging / name / source.name)


def _remap(value, staging, root):
    if isinstance(value, str) and Path(value).is_absolute():
        try:
            return str(root / Path(value).relative_to(staging))
        except ValueError:
            return value
    if isinstance(value, Path):
        try:
            return root / value.relative_to(staging)
        except ValueError:
            return value
    if is_dataclass(value) and not isinstance(value, type):
        return replace(value, **{field.name: _remap(getattr(value, field.name), staging, root) for field in fields(value)})
    if isinstance(value, list):
        return [_remap(item, staging, root) for item in value]
    if isinstance(value, tuple):
        return tuple(_remap(item, staging, root) for item in value)
    if isinstance(value, dict):
        return {key: _remap(item, staging, root) for key, item in value.items()}
    return value


def _stage_argument(value, root, staging):
    mapped = _remap(value, root, staging)
    # Diagnostic files may live in ci-diagnostics or a batch temporary path,
    # outside the regular authoring inputs copied by _copy_inputs.
    if isinstance(value, Path) and value.is_file() and isinstance(mapped, Path) and mapped != value:
        if not mapped.exists():
            mapped.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(value, mapped)
    return mapped


class Mutation:
    def __init__(self, root, slug, *, approve_bundle=False):
        self.root, self.slug = Path(root).resolve(), slug
        self.approve_bundle = approve_bundle
        configuration = self.root / "config/config.json"
        if configuration.is_file():
            paths = json.loads(configuration.read_text(encoding="utf-8")).get("paths", {})
            if any(paths.get(name + "_dir", name) != name for name in ("episodes", "work", "output")):
                from engine.pipeline_state import PipelineError
                raise PipelineError("UNSUPPORTED_COORDINATED_PATHS", "Shared mutations require episodes/, work/ and output/.")
        self.coordinator, self.token = acquire_token(self.root, slug)
        self.stop = threading.Event()
        self.heartbeat_error = None

    def heartbeat(self):
        interval = max(1, getattr(self.coordinator, "lease_seconds", 120) / 3)
        while not self.stop.wait(interval):
            try:
                self.token = self.coordinator.heartbeat(self.token)
                save_token(self.root, self.token)
            except Exception as exc:
                self.heartbeat_error = exc
                return

    @contextmanager
    def workspace(self):
        from engine.pipeline_state import PipelineError
        with tempfile.TemporaryDirectory(prefix="pipeline-mutation-") as directory:
            staging = Path(directory)
            thread = threading.Thread(target=self.heartbeat, daemon=True)
            thread.start()
            try:
                # Hashing/copying a large bundle is part of the lease-protected
                # preparation. The authoritative ledger is staged from the same
                # revision as the artifact base, never from a stale local cache.
                remote_record, _ = authoritative_mutation_base(self.root, self.slug, self.coordinator, self.token)
                before = _selected(self.root, self.slug)
                _copy_inputs(self.root, staging, self.slug)
                if remote_record is not None:
                    name = staging / ".pipeline/episodes" / (self.slug + ".json")
                    name.parent.mkdir(parents=True, exist_ok=True)
                    name.write_bytes(remote_record)
                with private_staging(staging):
                    yield staging
                if before != _selected(self.root, self.slug):
                    raise PipelineError("LOCAL_MUTATION_CONFLICT", "Local input changed while preparing the private mutation.")
                after = _selected(staging, self.slug)
                changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
                # Only authored metadata belongs in Git. Large rendered bytes
                # remain artifacts and are bound by hashes in the CAS transaction.
                files = {}
                for name in set(changed) | {name for name in after if name.startswith("episodes/" + self.slug + "/")}:
                    path = Path(name)
                    if (name.startswith("episodes/" + self.slug + "/") and len(path.parts) == 3 and path.suffix in {".json", ".txt"}) or name.startswith((".episode-check/", ".publish-queue/")):
                        files[name] = (staging / name).read_bytes() if name in after else None
                files[".pipeline/artifacts/" + self.slug + ".json"] = json.dumps(
                    {"slug": self.slug, "generation": self.token["generation"], "files": after,
                     "bundle_approved": self.approve_bundle and ".publish-ready/manifest.json" in after},
                    sort_keys=True).encode()
                # The compare-and-swap serializes this final write with publisher
                # start and takeover. A last-minute GET alone would not suffice.
                from engine.pipeline_state import read_json, atomic_json
                state_name = ".pipeline/episodes/" + self.slug + ".json"
                prepared_state = read_json(staging / state_name)
                phase = prepared_state.get("stage") if prepared_state else None
                request_id = prepared_state.get("request_id") if prepared_state else None
                if prepared_state:
                    prepared_state.update(shared_generation=self.token["generation"] + int(request_id != self.token["request_id"]),
                                          shared_owner_id=self.token["owner_id"])
                    files[state_name] = (json.dumps(prepared_state, sort_keys=True) + "\n").encode()
                self.stop.set()
                thread.join(timeout=35)
                if thread.is_alive() or self.heartbeat_error:
                    raise PipelineError("MUTATION_LEASE_LOST", "No private changes were committed.", recoverable=True)
                # Reserve a fresh lease for the final remote blob/tree/commit
                # preparation. CAS still rejects any publisher or takeover race.
                self.token = self.coordinator.heartbeat(self.token)
                save_token(self.root, self.token)
                self.token = self.coordinator.commit_mutation(self.token, files, phase=phase, request_id=request_id)
                self.committed_revision = self.token["revision"]
                save_token(self.root, self.token)
                self.coordinator.assert_current(self.token)
                if prepared_state:
                    from engine.pipeline_state import PipelineStore, channel_for
                    from engine.process_lock import process_lock
                    store = PipelineStore(self.root)
                    with process_lock(store.directory / ".lock"):
                        atomic_json(self.root / state_name, prepared_state)
                        index = read_json(store.index_path) or store._empty_index()
                        channel = channel_for(self.slug)
                        index["active"][channel] = self.slug
                        index.get("conflicts", {}).pop(channel, None)
                        atomic_json(store.index_path, index)
                for name in changed:
                    destination = self.root / name
                    if name not in after:
                        destination.unlink(missing_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        descriptor, temporary = tempfile.mkstemp(prefix=".cache-", dir=destination.parent)
                        import os
                        os.close(descriptor)
                        try:
                            shutil.copyfile(staging / name, temporary)
                            Path(temporary).replace(destination)
                        finally:
                            Path(temporary).unlink(missing_ok=True)
                mark_cache_applied(self.root, self.slug, files[".pipeline/artifacts/" + self.slug + ".json"])
            finally:
                self.stop.set()
                thread.join(timeout=35)
                try:
                    release_token(self.root, self.coordinator, self.token)
                except Exception:
                    pass  # A lost generation must never be reopened during cleanup.


def fenced_mutation(root_arg="project_root", slug_arg="slug", *, approve_bundle=False):
    def decorate(function):
        signature = inspect.signature(function)
        def arguments(args, kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            root = Path(bound.arguments[root_arg]).resolve()
            slug = bound.arguments[slug_arg]
            if "bundle_dir" in bound.arguments:
                bundle = Path(bound.arguments["bundle_dir"])
                target = (root / bundle).resolve()
                if coordinator_for(root) is not None and target != root / ".publish-ready":
                    from engine.pipeline_state import PipelineError
                    raise PipelineError("UNSUPPORTED_COORDINATED_BUNDLE_PATH", "Use the private .publish-ready bundle path.")
            return bound, root, slug
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def asynchronous(*args, **kwargs):
                bound, root, slug = arguments(args, kwargs)
                if in_private_staging(root) or coordinator_for(root) is None:
                    return await function(*args, **kwargs)
                transaction = Mutation(root, slug, approve_bundle=approve_bundle)
                with transaction.workspace() as staging:
                    for name, value in list(bound.arguments.items()):
                        bound.arguments[name] = _stage_argument(value, root, staging)
                    bound.arguments[root_arg] = staging
                    result = await function(*bound.args, **bound.kwargs)
                return _remap(result, staging, root)
            return asynchronous
        @functools.wraps(function)
        def synchronous(*args, **kwargs):
            bound, root, slug = arguments(args, kwargs)
            if in_private_staging(root) or coordinator_for(root) is None:
                return function(*args, **kwargs)
            transaction = Mutation(root, slug, approve_bundle=approve_bundle)
            with transaction.workspace() as staging:
                for name, value in list(bound.arguments.items()):
                    bound.arguments[name] = _stage_argument(value, root, staging)
                bound.arguments[root_arg] = staging
                result = function(*bound.args, **bound.kwargs)
                if isinstance(result, int) and not isinstance(result, bool) and result != 0:
                    raise RuntimeError("MUTATION_ABORTED: preparation did not succeed")
            result = _remap(result, staging, root)
            if isinstance(result, dict) and result.get("next_action") == "commit_and_push_request":
                result.update(commit_sha=transaction.committed_revision, next_action="wait_for_correlated_run")
            return result
        return synchronous
    return decorate
