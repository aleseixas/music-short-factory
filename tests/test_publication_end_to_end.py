"""Offline publication flow through the real CLI and a shared CAS authority."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from engine.coordination_runtime import save_token
from engine.pipeline_state import PipelineStore, atomic_json, read_json
from engine.shared_coordination import SharedCoordinator, using_coordinator
from publish import main as publish_main
from publishing.attempts import AttemptStore, PublicationLocked
from publishing.credentials import CredentialStore
from publishing.youtube import YouTubePublisher
from tests.test_publication_snapshot_recovery import make_bundle, queued
from tests.test_shared_coordination import ServerBackend, authority


pytestmark = pytest.mark.distributed_coordination
SOURCE_SHA = "a" * 40
REQUEST_PATH = ".episode-check/demo--request_1.json"
VIDEO_BYTES = b"approved video bytes"
COVER_BYTES = b"approved cover bytes"


class FakeGitHubPreflight:
    """Only exact read-only requests for the source run and request are served."""

    def __init__(self):
        self.calls: list[str] = []

    def request(self, method, url, **kwargs):
        assert method == "GET"
        self.calls.append(url)
        if url.endswith("/actions/runs/123"):
            payload = {
                "id": 123,
                "name": "Episode media preflight",
                "status": "completed",
                "conclusion": "success",
                "event": "push",
                "head_branch": "main",
                "path": ".github/workflows/episode-media-preflight.yml",
                "head_sha": SOURCE_SHA,
            }
        elif url.endswith("/contents/" + REQUEST_PATH):
            assert kwargs["params"] == {"ref": SOURCE_SHA}
            receipt = {
                "slug": "demo",
                "request_id": "request_1",
                "local_preflight_passed": True,
                "workflow": "episode-media-preflight.yml",
            }
            payload = {"content": base64.b64encode(json.dumps(receipt).encode()).decode()}
        else:
            raise AssertionError(f"unexpected GitHub request: {url}")
        return SimpleNamespace(status_code=200, json=lambda: payload)


class FakeYouTubeAPI:
    def __init__(self, root: Path):
        self.root = root
        self.calls: list[tuple[str, str]] = []
        self.uploaded: list[bytes] = []
        self.thumbnails: list[bytes] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if method == "POST" and url == YouTubePublisher.UPLOAD_URL:
            # Change the working bundle after the claim; the real adapter must
            # still read the private snapshot when it uploads the video.
            (self.root / ".publish-ready/output/demo.mp4").write_bytes(b"changed after claim")
            headers = {"Location": "https://upload.invalid/offline-session"}
            payload = {}
        elif method == "PUT" and url == "https://upload.invalid/offline-session":
            media = kwargs["data"]
            assert not Path(media.name).is_relative_to(self.root)
            self.uploaded.append(media.read())
            headers = {}
            payload = {"id": "offline-video-123"}
        elif method == "POST" and url == YouTubePublisher.THUMBNAIL_URL:
            thumbnail = kwargs["data"]
            assert not Path(thumbnail.name).is_relative_to(self.root)
            self.thumbnails.append(thumbnail.read())
            headers = {}
            payload = {}
        else:
            raise AssertionError(f"unexpected YouTube request: {method} {url}")
        return SimpleNamespace(status_code=200, headers=headers, json=lambda: payload)


def _queued_approved_bundle(root: Path):
    make_bundle(root)
    queued(root)
    pipeline = PipelineStore(root)
    state = read_json(pipeline.episode_path("demo"))
    state.update(
        commit_sha=SOURCE_SHA,
        run_id="123",
        request_path=REQUEST_PATH,
        workflow=".github/workflows/episode-media-preflight.yml",
        local_preflight_passed=True,
        media_preflight_passed=True,
        queue_created=True,
    )
    atomic_json(pipeline.episode_path("demo"), state)
    queue = root / ".publish-queue/demo.txt"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_bytes(b"demo\n123\nrequest_1\n")
    manifest_bytes = (root / ".publish-ready/manifest.json").read_bytes()
    return {
        "slug": "demo",
        "bundle_approved": True,
        "files": {".publish-ready/manifest.json": hashlib.sha256(manifest_bytes).hexdigest()},
    }


def test_cli_publishes_approved_snapshot_and_new_clone_reconciles_without_resend(tmp_path):
    publisher_root = tmp_path / "publisher"
    publisher_root.mkdir()
    artifact = _queued_approved_bundle(publisher_root)
    fake_api = FakeGitHubPreflight()
    fake_platform = FakeYouTubeAPI(publisher_root)
    env = {
        "GITHUB_ACTIONS": "false",
        "GITHUB_REPOSITORY": "owner/repo",
        "GH_TOKEN": "offline-test-token",
        "PIPELINE_REQUEST_ID": "request_1",
        "SOURCE_RUN_ID": "123",
    }

    with authority() as (url, data, _lock):
        coordinator = SharedCoordinator(
            publisher_root, ServerBackend(url), owner_id="publisher", lease_seconds=10,
        )
        token = coordinator.acquire("demo", "request_1")
        token = coordinator.commit_mutation(
            token,
            {
                ".pipeline/artifacts/demo.json": (json.dumps(artifact) + "\n").encode(),
                ".publish-queue/demo.txt": (publisher_root / ".publish-queue/demo.txt").read_bytes(),
            },
            phase="QUEUED",
        )
        save_token(publisher_root, token)

        with (
            patch.dict(os.environ, env),
            using_coordinator(publisher_root, coordinator),
            patch("publish.CredentialStore.load", return_value=CredentialStore.from_mapping({
                "YOUTUBE_ACCESS_TOKEN": "offline-test-token",
            })),
            patch("publish.create_publisher", side_effect=lambda platform, credentials:
                  YouTubePublisher(credentials=credentials, session=fake_platform)),
            patch("publish.AttemptStore", side_effect=lambda root: AttemptStore(root, fake_api)),
        ):
            assert publish_main([
                "demo", "--platform", "youtube", "--live",
                "--project-root", str(publisher_root),
            ]) == 0

        assert fake_platform.uploaded == [VIDEO_BYTES]
        assert fake_platform.thumbnails == [COVER_BYTES]
        assert fake_platform.calls == [
            ("POST", YouTubePublisher.UPLOAD_URL),
            ("PUT", "https://upload.invalid/offline-session"),
            ("POST", YouTubePublisher.THUMBNAIL_URL),
        ]
        assert fake_api.calls == [
            "https://api.github.com/repos/owner/repo/actions/runs/123",
            "https://api.github.com/repos/owner/repo/contents/" + REQUEST_PATH,
        ]
        marker = json.loads(data["files"][".publication-attempts/demo.json"])
        assert marker["stage"] == "PUBLISHED"
        assert marker["platforms"]["youtube"]["status"] == "confirmed_sent"
        assert data["states"]["default"]["publisher_started"] is True
        assert data["states"]["default"]["mutation_allowed"] is False
        assert data["states"]["default"]["released"] is True

        fresh_root = tmp_path / "fresh-clone"
        fresh_root.mkdir()
        fresh_coordinator = SharedCoordinator(
            fresh_root, ServerBackend(url), owner_id="fresh-clone", lease_seconds=10,
        )
        observer = Mock(side_effect=AssertionError("confirmed receipt needs no platform query"))
        with patch.dict(os.environ, env), using_coordinator(fresh_root, fresh_coordinator):
            attempts = AttemptStore(fresh_root, fake_api)
            recovered = attempts.reconcile("demo", observer)
            assert recovered["stage"] == "PUBLISHED"
            assert recovered["platforms"]["youtube"]["external_id"] == "offline-video-123"
            observer.assert_not_called()
            with pytest.raises(PublicationLocked, match="ALREADY_ATTEMPTED"):
                attempts.claim("demo", "youtube", "local:second", payload_fingerprint=marker["payload_fingerprint"])
        assert fake_platform.uploaded == [VIDEO_BYTES]
