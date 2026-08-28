from __future__ import annotations

import json
import math
from pathlib import Path
import re

from .assets import load_asset_catalog
from .models import Episode, ScriptSegment, Story
from .timeline import load_shots
from .utils import load_json, safe_child, validate_schema, validate_slug


REQUIRED_EPISODE_FILES = ("story.json", "timeline.json", "assets.json", "sources.txt")


def load_story(path: Path) -> Story:
    data = load_json(path)
    validate_schema(data, path)
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise RuntimeError("story.json precisa ter uma lista nao vazia em 'segments'.")

    segments: list[ScriptSegment] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Segmento {index} do roteiro e invalido.")
        segment_id = str(raw.get("id", "")).strip()
        text = str(raw.get("text", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", segment_id) or segment_id in seen:
            raise RuntimeError(f"ID de segmento ausente ou duplicado: {segment_id!r}")
        if not text:
            raise RuntimeError(f"O segmento {segment_id!r} nao tem texto.")
        seen.add(segment_id)
        segments.append(ScriptSegment(id=segment_id, text=text))

    title = str(data.get("title", "")).strip()
    slug = validate_slug(str(data.get("slug", "")), "slug")
    try:
        target = float(data["target_duration_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "story.json precisa definir target_duration_seconds como numero positivo."
        ) from exc
    if not title:
        raise RuntimeError("story.json precisa definir 'title'.")
    if not math.isfinite(target) or target <= 0:
        raise RuntimeError("target_duration_seconds precisa ser positivo e finito.")

    return Story(
        title=title,
        slug=slug,
        target_duration_seconds=target,
        segments=tuple(segments),
    )


def load_episode(project_root: Path, episodes_dir: str, name: str) -> Episode:
    project_root = project_root.resolve()
    episode_name = validate_slug(name)
    episodes_root = (project_root / episodes_dir).resolve()
    try:
        episodes_root.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError(f"Pasta de episodios fora do projeto: {episodes_root}") from exc
    directory = safe_child(episodes_root, episode_name)
    if not directory.is_dir():
        raise RuntimeError(
            f"Episodio {episode_name!r} nao encontrado em {directory}. "
            f"Crie-o com: python new_episode.py {episode_name}"
        )
    missing = [
        file_name
        for file_name in REQUIRED_EPISODE_FILES
        if not (directory / file_name).is_file()
    ]
    if missing:
        raise RuntimeError(
            f"Episodio {episode_name!r} incompleto; arquivos ausentes: {', '.join(missing)}."
        )

    story = load_story(directory / "story.json")
    if story.slug != episode_name:
        raise RuntimeError(
            f"O slug de story.json ({story.slug!r}) precisa ser igual a pasta do episodio "
            f"({episode_name!r})."
        )
    assets = load_asset_catalog(directory / "assets.json")
    shots = load_shots(directory / "timeline.json", story, assets)
    return Episode(episode_name, directory, story, assets, shots)


def create_episode(project_root: Path, episodes_dir: str, name: str) -> Path:
    project_root = project_root.resolve()
    episode_name = validate_slug(name)
    episodes_root = (project_root / episodes_dir).resolve()
    try:
        episodes_root.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError(f"Pasta de episodios fora do projeto: {episodes_root}") from exc
    episodes_root.mkdir(parents=True, exist_ok=True)
    destination = safe_child(episodes_root, episode_name)
    if destination.exists():
        raise RuntimeError(f"O episodio {episode_name!r} ja existe em {destination}.")

    assets_dir = destination / "assets"
    assets_dir.mkdir(parents=True)
    story = {
        "schema_version": 1,
        "title": "Novo video musical",
        "slug": episode_name,
        "target_duration_seconds": 75,
        "segments": [{"id": "hook", "text": "Substitua pelo texto da narracao."}],
    }
    timeline = {
        "schema_version": 1,
        "shots": [
            {
                "id": "shot_hook",
                "segment": "hook",
                "asset": "main_image",
                "motion": "push_in",
                "transition_out": "cut",
                "highlight": {
                    "text": "TEXTO DE DESTAQUE",
                    "start_seconds": 0.2,
                    "duration_seconds": 1.6,
                },
            }
        ],
    }
    assets = {
        "schema_version": 1,
        "assets": [
            {
                "id": "main_image",
                "file": "main_image.jpg",
                "url": "",
                "credit": "",
                "license": "",
                "focus": {"x": 0.5, "y": 0.5},
            }
        ],
    }
    _write_json(destination / "story.json", story)
    _write_json(destination / "timeline.json", timeline)
    _write_json(destination / "assets.json", assets)
    (destination / "sources.txt").write_text(
        "Liste aqui as fontes do roteiro, das imagens e das licencas.\n",
        encoding="utf-8",
    )
    return destination


def _write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
