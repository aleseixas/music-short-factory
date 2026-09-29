"""Independent diagnostics using the production episode parsers.

Partial models are diagnostic-only: callers must retain the original load error,
so no partial model can turn an invalid episode into a successful preflight.
"""
from __future__ import annotations

from pathlib import Path

from .assets import load_asset_catalog
from .episode import load_story
from .models import Episode, Story
from .timeline import load_timeline, _parse_background_music
from .utils import load_json, safe_child, validate_schema, validate_slug


def collect_partial_episode(root: Path, episodes_dir: str, slug: str):
    directory = safe_child(root / episodes_dir, validate_slug(slug))
    directory.relative_to(root.resolve())
    failures: list[tuple[str, Exception]] = []
    documents = {}
    for name in ("story.json", "assets.json", "timeline.json"):
        try:
            document = load_json(directory / name)
            validate_schema(document, directory / name)
            documents[name] = document
        except Exception as exc:
            failures.append((name, exc))

    story = Story("diagnostic-only", slug, ())
    if "story.json" in documents:
        try:
            story = load_story(directory / "story.json")
            if story.slug != slug:
                raise RuntimeError("O slug de story.json precisa ser igual a pasta do episodio.")
        except Exception as exc:
            failures.append(("story.json", exc))

    assets = {}
    assets_doc = documents.get("assets.json", {})
    try:
        assets = load_asset_catalog(directory / "assets.json", data=assets_doc)
    except Exception as exc:
        failures.append(("assets.json", exc))
        for index, raw in enumerate(assets_doc.get("assets", []) if isinstance(assets_doc.get("assets"), list) else []):
            try:
                parsed = load_asset_catalog(directory / "assets.json", data={"schema_version": 1, "assets": [raw]})
                for asset_id, asset in parsed.items():
                    if asset_id in assets:
                        raise RuntimeError(f"ID de asset ausente ou duplicado: {asset_id!r}")
                    assets[asset_id] = asset
            except Exception as entry_exc:
                failures.append((f"asset-entry:{index}", entry_exc))

    timeline_doc = documents.get("timeline.json", {})
    shots = []
    background = None
    try:
        timeline = load_timeline(directory / "timeline.json", story, assets, data=timeline_doc)
        shots = list(timeline.shots)
        background = timeline.background_music
    except Exception as exc:
        failures.append(("timeline.json", exc))
        raw_shots = timeline_doc.get("shots", [])
        for index, raw in enumerate(raw_shots if isinstance(raw_shots, list) else []):
            try:
                partial = load_timeline(
                    directory / "timeline.json", story, assets,
                    data={"schema_version": 1, "shots": [raw]}, diagnostic_partial=True,
                )
                shots.extend(partial.shots)
            except Exception as entry_exc:
                scope = f"shot:{raw.get('id', index)}" if isinstance(raw, dict) else f"shot-entry:{index}"
                failures.append((scope, entry_exc))
        try:
            background = _parse_background_music(timeline_doc.get("background_music"))
        except Exception as entry_exc:
            failures.append(("background-music-schema", entry_exc))

    return Episode(name=slug, directory=directory, story=story, assets=assets,
                   shots=tuple(shots), background_music=background), failures
