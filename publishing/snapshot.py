"""Private copies of the approved bytes; live adapters never open mutable output."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Iterator

from .base import PublishContext, PublishingError
from .metadata import load_post


@dataclass(frozen=True)
class PublicationSnapshot:
    directory: Path
    slug: str
    fingerprint: str
    manifest: dict

    def context(self, platform: str) -> PublishContext:
        # Load the copied post, never post.json in the authoring checkout.
        post = load_post(self.directory / "episodes" / self.slug / "post.json", warning_platforms=(platform,))
        metadata = post.for_platform(platform)
        if any(str(metadata.get(key, "")).strip() for key in ("video_url", "cover_url")):
            raise PublishingError("PUBLICATION_EXTERNAL_BYTES_UNVERIFIED: live media must come from the approved snapshot")
        return PublishContext(self.slug, self.directory / "output" / f"{self.slug}.mp4",
                              self.directory / "output" / f"{self.slug}_cover.jpg", metadata)


def approved_manifest(root: Path, slug: str, source_run_id: str, request_id: str,
                      *, check_authority: bool = True) -> tuple[dict, str]:
    from scripts.publish_ready_bundle import (
        _manifest_file_map, _require_regular_file, _validate_manifest_header, _validate_slug,
    )
    slug = _validate_slug(slug)
    manifest_path = root / ".publish-ready" / "manifest.json"
    _require_regular_file(manifest_path, "manifest.json")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8-sig"))
    _validate_manifest_header(manifest, slug, source_run_id)
    if manifest.get("source", {}).get("request_id") != request_id:
        raise PublishingError("PUBLICATION_SNAPSHOT_REQUEST_MISMATCH")
    files = _manifest_file_map(manifest, slug)
    if check_authority:
        from engine.coordination_runtime import coordinator_for
        coordinator = coordinator_for(root)
        if coordinator is None:
            raise PublishingError("PUBLICATION_SHARED_AUTHORITY_REQUIRED")
        authority = coordinator.status(slug)
        if (authority.get("slug") != slug or authority.get("request_id") != request_id
                or authority.get("phase") not in {"QUEUED", "PUBLISHING"}):
            raise PublishingError("PUBLICATION_SNAPSHOT_AUTHORITY_MISMATCH")
        path = f".pipeline/artifacts/{slug}.json"
        committed = coordinator.read_files([path], authority["revision"])[path]
        artifact = json.loads(committed) if committed is not None else {}
        expected = artifact.get("files", {}).get(".publish-ready/manifest.json")
        if artifact.get("bundle_approved") is not True or expected != hashlib.sha256(raw).hexdigest():
            raise PublishingError("PUBLICATION_SNAPSHOT_NOT_APPROVED: manifest is not committed by the media gate")
    fingerprint = hashlib.sha256(json.dumps({
        "slug": slug, "request_id": request_id, "source_run_id": source_run_id,
        "files": {role: {"sha256": item["sha256"], "size_bytes": item["size_bytes"]}
                  for role, item in sorted(files.items())},
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return manifest, fingerprint


@contextmanager
def approved_snapshot(root: Path, slug: str, source_run_id: str, request_id: str) -> Iterator[PublicationSnapshot]:
    from scripts.publish_ready_bundle import _manifest_file_map, _require_regular_file, _sha256
    manifest, fingerprint = approved_manifest(root, slug, source_run_id, request_id, check_authority=False)
    source = root / ".publish-ready"
    files = _manifest_file_map(manifest, slug)
    # Outside the checkout, with an unpredictable name: project mutators cannot
    # address these paths. They are independent copies, never hard links.
    directory = Path(tempfile.mkdtemp(prefix="publication-snapshot-"))
    os.chmod(directory, 0o700)
    try:
        for role, entry in files.items():
            original = source / entry["path"]
            _require_regular_file(original, role)
            destination = directory / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            with original.open("rb") as incoming, destination.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            # Validate the copy, not the file previously inspected. Concurrent
            # source writes either leave an approved copy or fail this gate.
            if destination.stat().st_size != int(entry["size_bytes"]) or _sha256(destination) != entry["sha256"]:
                raise PublishingError(f"PUBLICATION_SNAPSHOT_BYTES_CHANGED: {role}")
            os.chmod(destination, stat.S_IREAD)
        yield PublicationSnapshot(directory, slug, fingerprint, manifest)
    finally:
        # Only our exact private directory is cleaned up. Make readonly copies
        # removable on Windows; caller-controlled paths are never removed here.
        for path in directory.rglob("*"):
            if path.is_file():
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(directory)
