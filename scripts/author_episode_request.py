"""Author one reserved episode through the existing fenced CAS, then prepare media request."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.mutation_transaction import fenced_mutation
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

@fenced_mutation(root_arg="root", slug_arg="slug")
def author_and_prepare(root: Path, slug: str, request_id: str, payload: dict) -> dict:
    slug = safe_slug(slug)
    request_id = safe_request(request_id)
    files = payload.get("files")
    if not isinstance(files, dict):
        raise PipelineError("AUTHOR_REQUEST_INVALID", "files must be an object")
    if set(files) != REQUIRED_FILES:
        missing = sorted(REQUIRED_FILES - set(files))
        extra = sorted(set(files) - REQUIRED_FILES)
        raise PipelineError("AUTHOR_REQUEST_INVALID", f"required authored files mismatch; missing={missing}; extra={extra}")

    store = PipelineStore(root)
    state = store.status(slug)
    if state.get("request_id") != request_id:
        raise PipelineError("REQUEST_CONFLICT", "Author request does not own the reserved request_id.")
    if state.get("stage") == "UNIQUE":
        store.transition(slug, "AUTHORING", request_id=request_id)
    elif state.get("stage") not in {"AUTHORING", "VALIDATION_FAILED"}:
        raise PipelineError("AUTHORING_REQUIRED", f"Cannot author while {state.get('stage')}.")

    episode_dir = root / "episodes" / slug
    episode_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(REQUIRED_FILES):
        value = files[name]
        target = episode_dir / name
        if name.endswith(".json"):
            if not isinstance(value, (dict, list)):
                raise PipelineError("AUTHOR_REQUEST_INVALID", f"{name} must contain JSON object/array content")
            target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            if not isinstance(value, str):
                raise PipelineError("AUTHOR_REQUEST_INVALID", f"{name} must be text")
            target.write_text(value.rstrip() + "\n", encoding="utf-8")

    return prepare_request(root, slug, request_id)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_file", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    payload = json.loads(args.request_file.read_text(encoding="utf-8"))
    slug = safe_slug(str(payload.get("slug", "")).strip())
    request_id = safe_request(str(payload.get("request_id", "")).strip())
    result = author_and_prepare(args.root.resolve(), slug, request_id, payload)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("result") == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
