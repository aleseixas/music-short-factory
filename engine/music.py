from __future__ import annotations

import hashlib
from pathlib import Path

from .audio_library import (
    AudioCatalogEntry,
    SUPPORTED_AUDIO_SUFFIXES,
    materialize_audio_catalog_entry,
    parse_audio_catalog_entry,
)
from .ffmpeg import probe_audio_duration
from .media_cache import media_cache_directory
from .models import BackgroundMusicSpec, ResolvedBackgroundMusic
from .utils import load_json, validate_schema


MUSIC_ROOT = Path("assets") / "audio" / "music"
MUSIC_CATALOG = MUSIC_ROOT / "catalog.json"
SUPPORTED_MUSIC_SUFFIXES = SUPPORTED_AUDIO_SUFFIXES


def resolve_background_music(
    project_root: Path,
    spec: BackgroundMusicSpec | None,
    episode_slug: str,
    cache_root: Path | None = None,
) -> ResolvedBackgroundMusic | None:
    if spec is None:
        return None

    project_root = project_root.resolve()
    music_root = (project_root / MUSIC_ROOT).resolve()
    catalog_path = (project_root / MUSIC_CATALOG).resolve()
    try:
        music_root.relative_to(project_root)
        catalog_path.relative_to(music_root)
    except ValueError as exc:
        raise RuntimeError("Biblioteca local de background music fora do projeto.") from exc

    if not catalog_path.is_file():
        raise RuntimeError(
            f"Catalogo de background music ausente: {MUSIC_CATALOG.as_posix()}."
        )
    data = load_json(catalog_path)
    validate_schema(data, catalog_path)
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise RuntimeError(
            f"{MUSIC_CATALOG.as_posix()} precisa conter um objeto em 'profiles'."
        )

    raw_files = profiles.get(spec.profile)
    if raw_files is None:
        raise RuntimeError(
            f"Profile de background music inexistente: {spec.profile!r}. "
            f"Configure-o em {MUSIC_CATALOG.as_posix()}."
        )
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeError(
            f"Profile de background music {spec.profile!r} precisa listar "
            "ao menos um arquivo."
        )

    candidates: list[AudioCatalogEntry] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(raw_files, start=1):
        label = f"profiles.{spec.profile}[{index}]"
        entry = parse_audio_catalog_entry(
            music_root,
            raw_entry,
            label,
            "background music",
        )
        key = entry.relative_file.casefold()
        if key in seen:
            raise RuntimeError(
                f"Arquivo duplicado no profile de background music {spec.profile!r}: "
                f"{entry.relative_file!r}."
            )
        seen.add(key)
        if not entry.local_path.is_file() and not entry.url:
            raise RuntimeError(
                f"Arquivo local do profile {spec.profile!r} nao encontrado: "
                f"{entry.relative_file}."
            )
        candidates.append(entry)

    candidates.sort(key=lambda entry: entry.relative_file.casefold())
    digest = hashlib.sha256(
        f"{spec.profile}\0{episode_slug}".encode("utf-8")
    ).digest()
    selected_entry = candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
    selected, downloaded = materialize_audio_catalog_entry(
        selected_entry,
        media_cache_directory(project_root, cache_root, "music"),
        f"profile {spec.profile!r}",
        "background music",
    )
    if downloaded:
        try:
            probe_audio_duration(selected)
        except RuntimeError as exc:
            selected.unlink(missing_ok=True)
            raise RuntimeError(
                f"Background music remota invalida no profile {spec.profile!r} "
                f"({selected_entry.relative_file}): {exc}"
            ) from exc
    return ResolvedBackgroundMusic(
        profile=spec.profile,
        path=selected,
        volume=spec.volume,
    )
