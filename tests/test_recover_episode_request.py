import json
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.pipeline_state import PipelineError
from scripts.recover_episode_request import REQUIRED_FILES, recover_and_prepare


class FakeStore:
    def __init__(self, state):
        self.state = state
        self.assertions = 0

    def status(self, slug):
        return dict(self.state)

    def assert_mutation_allowed(self, slug):
        self.assertions += 1


def payload(slug="demo", previous="old_request", request_id="new_request"):
    files = {
        "story.json": {"schema_version": 1, "title": "Repaired"},
        "timeline.json": {"schema_version": 1, "shots": []},
        "assets.json": {"schema_version": 1, "assets": []},
        "visual_candidates.json": {"schema_version": 1, "slots": []},
        "sources.txt": "source",
        "post.json": {"schema_version": 1},
    }
    assert set(files) == REQUIRED_FILES
    return {
        "schema_version": 1,
        "slug": slug,
        "previous_request_id": previous,
        "request_id": request_id,
        "repair_cycle": 1,
        "files": files,
    }


def base_state():
    return {
        "request_id": "old_request",
        "stage": "VALIDATION_FAILED",
        "publisher_started": False,
        "ever_published_or_attempted": False,
        "recovery_mutation_allowed": True,
        "republication_allowed": True,
    }


def test_recovery_request_replaces_only_authored_metadata_and_calls_prepare(tmp_path):
    store = FakeStore(base_state())
    data = payload()
    with patch("scripts.recover_episode_request.PipelineStore", return_value=store), \
            patch(
                "scripts.recover_episode_request.prepare_request",
                return_value={"next_action": "wait_for_correlated_run", "commit_sha": "a" * 40},
            ) as prepare:
        result = recover_and_prepare.__wrapped__(
            tmp_path,
            "demo",
            "old_request",
            "new_request",
            data,
        )

    assert result["repair_cycle"] == 1
    assert result["previous_request_id"] == "old_request"
    assert store.assertions == 1
    prepare.assert_called_once_with(tmp_path, "demo", "new_request")
    episode = tmp_path / "episodes" / "demo"
    assert {p.name for p in episode.iterdir()} == REQUIRED_FILES
    assert json.loads((episode / "story.json").read_text(encoding="utf-8"))["title"] == "Repaired"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"request_id": "someone_else"}, "RECOVERY_REQUEST_STALE"),
        ({"stage": "QUEUED"}, "RECOVERY_PREPUBLICATION_REQUIRED"),
        ({"publisher_started": True}, "RECOVERY_PUBLISHER_STARTED"),
        ({"ever_published_or_attempted": True}, "RECOVERY_PUBLISHER_STARTED"),
        ({"recovery_mutation_allowed": False}, "RECOVERY_MUTATION_FORBIDDEN"),
        ({"republication_allowed": False}, "RECOVERY_MUTATION_FORBIDDEN"),
    ],
)
def test_recovery_request_rejects_unsafe_authority_states(tmp_path, change, code):
    state = base_state()
    state.update(change)
    store = FakeStore(state)
    with patch("scripts.recover_episode_request.PipelineStore", return_value=store), \
            pytest.raises(PipelineError, match=code):
        recover_and_prepare.__wrapped__(
            tmp_path,
            "demo",
            "old_request",
            "new_request",
            payload(),
        )


def test_recovery_request_requires_fresh_request_and_three_cycle_limit(tmp_path):
    store = FakeStore(base_state())
    with patch("scripts.recover_episode_request.PipelineStore", return_value=store), \
            pytest.raises(PipelineError, match="RECOVERY_REQUEST_ID_REUSED"):
        recover_and_prepare.__wrapped__(
            tmp_path,
            "demo",
            "old_request",
            "old_request",
            payload(request_id="old_request"),
        )

    invalid = payload()
    invalid["repair_cycle"] = 4
    with patch("scripts.recover_episode_request.PipelineStore", return_value=store), \
            pytest.raises(PipelineError, match="RECOVERY_REQUEST_INVALID"):
        recover_and_prepare.__wrapped__(
            tmp_path,
            "demo",
            "old_request",
            "new_request",
            invalid,
        )


def test_recovery_request_blocks_queue_or_publication_attempt_markers(tmp_path):
    for marker, code in (
        (tmp_path / ".publish-queue" / "demo.txt", "RECOVERY_QUEUE_EXISTS"),
        (tmp_path / ".publication-attempts" / "demo.json", "RECOVERY_PUBLICATION_ATTEMPT_EXISTS"),
    ):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("marker", encoding="utf-8")
        store = FakeStore(base_state())
        with patch("scripts.recover_episode_request.PipelineStore", return_value=store), \
                pytest.raises(PipelineError, match=code):
            recover_and_prepare.__wrapped__(
                tmp_path,
                "demo",
                "old_request",
                "new_request",
                payload(),
            )
        marker.unlink()
