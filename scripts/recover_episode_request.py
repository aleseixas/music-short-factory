"""Apply one pre-publication recovery repair through fenced CAS, then prepare a new media request."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.coordination_runtime import sync_authority
from engine.pipeline_runtime import prepare_request
from engine.pipeline_state import PipelineError, PipelineStore, safe_request, safe_slug

REQUIRED_FILES = {
    "story.json",
    "timeline.json",
    "assets.json",
    "visual_candidates.json",
    "sources.txt",
    "post.json",
}


def _validated_files(payload: dict) -> dict:
    files = payload.get("files")
    if not isinstance(files, dict):
        raise PipelineError("RECOVERY_REQUEST_INVALID", "files must be an object")
    if set(files) != REQUIRED_FILES:
        missing = sorted(REQUIRED_FILES - set(files))
        extra = sorted(set(files) - REQUIRED_FILES)
        raise PipelineError(
            "RECOVERY_REQUEST_INVALID",
            f"required recovery files mismatch; missing={missing}; extra={extra}",
        )
    return files


def recover_and_prepare(
    root: Path,
    slug: str,
    previous_request_id: str,
    request_id: str,
    payload: dict,
) -> dict:
    """Replace only authored metadata for the same pre-publication slug and prepare it."""
    slug = safe_slug(slug)
    previous_request_id = safe_request(previous_request_id)
    request_id = safe_request(request_id)
    if request_id == previous_request_id:
        raise PipelineError(
            "RECOVERY_REQUEST_ID_REUSED",
            "A material recovery validation must use a fresh media request_id.",
        )

    cycle = payload.get("repair_cycle")
    if not isinstance(cycle, int) or isinstance(cycle, bool) or cycle not in {1, 2, 3}:
        raise PipelineError(
            "RECOVERY_REQUEST_INVALID",
            "repair_cycle must be an integer from 1 to 3.",
        )

    files = _validated_files(payload)
    store = PipelineStore(root)
    state = store.status(slug)

    if state.get("request_id") != previous_request_id:
        raise PipelineError(
            "RECOVERY_REQUEST_STALE",
            "The recovery request no longer matches the authoritative request_id.",
        )
    if state.get("stage") != "VALIDATION_FAILED":
        raise PipelineError(
            "RECOVERY_PREPUBLICATION_REQUIRED",
            f"Recovery repair requires VALIDATION_FAILED, got {state.get('stage')}.",
        )
    if state.get("publisher_started") or state.get("ever_published_or_attempted"):
        raise PipelineError(
            "RECOVERY_PUBLISHER_STARTED",
            "Publisher evidence exists; recovery mutation is permanently forbidden.",
        )
    if state.get("recovery_mutation_allowed") is False or state.get("republication_allowed") is False:
        raise PipelineError(
            "RECOVERY_MUTATION_FORBIDDEN",
            "Authoritative state forbids pre-publication recovery mutation.",
        )

    store.assert_mutation_allowed(slug)

    if (root / ".publish-queue" / f"{slug}.txt").exists():
        raise PipelineError("RECOVERY_QUEUE_EXISTS", "A queued slug cannot be repaired.")
    if (root / ".publication-attempts" / f"{slug}.json").exists():
        raise PipelineError(
            "RECOVERY_PUBLICATION_ATTEMPT_EXISTS",
            "A slug with a publication-attempt marker cannot be repaired.",
        )

    episode_dir = root / "episodes" / slug
    episode_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(REQUIRED_FILES):
        value = files[name]
        target = episode_dir / name
        if name.endswith(".json"):
            if not isinstance(value, (dict, list)):
                raise PipelineError(
                    "RECOVERY_REQUEST_INVALID",
                    f"{name} must contain JSON object/array content.",
                )
            target.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        else:
            if not isinstance(value, str):
                raise PipelineError(
                    "RECOVERY_REQUEST_INVALID",
                    f"{name} must be text.",
                )
            target.write_text(value.rstrip() + "\n", encoding="utf-8")

    result = prepare_request(root, slug, request_id)
    if isinstance(result, dict):
        result["repair_cycle"] = cycle
        result["previous_request_id"] = previous_request_id
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_file", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    payload = json.loads(args.request_file.read_text(encoding="utf-8"))
    slug = safe_slug(str(payload.get("slug", "")).strip())
    previous_request_id = safe_request(str(payload.get("previous_request_id", "")).strip())
    request_id = safe_request(str(payload.get("request_id", "")).strip())

    root = args.root.resolve()
    sync_authority(root, slug, download=True)
    result = recover_and_prepare(
        root,
        slug,
        previous_request_id,
        request_id,
        payload,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("result") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
