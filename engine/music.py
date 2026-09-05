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
MUSIC_FALLBACK_CATALOG = MUSIC_ROOT / "fallback_social_extra.json"
SUPPORTED_MUSIC_SUFFIXES = SUPPORTED_AUDIO_SUFFIXES

_SAFE_CONTENT_ID_STATUSES = frozenset(
    {
        "explicit_no_content_id",
        "not_registered_on_source_page_when_curated",
        "creator_states_no_content_id_registration",
    }
)
_REGISTERED_CONTENT_ID_STATUSES = frozenset(
    {
        "registered",
        "content_id_registered",
    }
)


def _load_music_profiles(project_root: Path, music_root: Path) -> dict[str, list[object]]:
    catalog_paths = [project_root / MUSIC_CATALOG]
    fallback_path = project_root / MUSIC_FALLBACK_CATALOG
    if fallback_path.is_file():
        catalog_paths.append(fallback_path)

    merged: dict[str, list[object]] = {}
    for catalog_path in catalog_paths:
        resolved = catalog_path.resolve()
        try:
            resolved.relative_to(music_root)
        except ValueError as exc:
            raise RuntimeError(
                f"Catalogo de background music fora da biblioteca: {catalog_path}."
            ) from exc

        data = load_json(resolved)
        validate_schema(data, resolved)
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            raise RuntimeError(f"{catalog_path.as_posix()} precisa conter um objeto em 'profiles'.")

        for profile, raw_files in profiles.items():
            if not isinstance(profile, str) or not profile.strip():
                raise RuntimeError(f"Profile invalido em {catalog_path.as_posix()}.")
            if not isinstance(raw_files, list) or not raw_files:
                raise RuntimeError(
                    f"Profile de background music {profile!r} em "
                    f"{catalog_path.as_posix()} precisa listar ao menos um arquivo."
                )
            merged.setdefault(profile, []).extend(raw_files)
    return merged


def _content_id_risk(raw_entry: object) -> int:
    """Prefer safer curated tracks; keep registered tracks as deep fallback."""
    if not isinstance(raw_entry, dict):
        return 1
    raw_status = raw_entry.get("content_id_status")
    if raw_status is None:
        return 1
    if not isinstance(raw_status, str):
        raise RuntimeError("content_id_status precisa ser texto quando informado.")
    status = raw_status.strip().casefold()
    if status in _SAFE_CONTENT_ID_STATUSES:
        return 0
    if status in _REGISTERED_CONTENT_ID_STATUSES:
        return 2
    return 1


def _rotate_candidates(
    candidates: list[AudioCatalogEntry],
    profile: str,
    episode_slug: str,
    risk: int,
) -> list[AudioCatalogEntry]:
    candidates.sort(key=lambda entry: entry.relative_file.casefold())
    digest = hashlib.sha256(
        f"{profile}\0{episode_slug}\0{risk}".encode("utf-8")
    ).digest()
    start_index = int.from_bytes(digest[:8], "big") % len(candidates)
    return candidates[start_index:] + candidates[:start_index]


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

    profiles = _load_music_profiles(project_root, music_root)
    raw_files = profiles.get(spec.profile)
    if raw_files is None:
        raise RuntimeError(
            f"Profile de background music inexistente: {spec.profile!r}. "
            f"Configure-o em {MUSIC_CATALOG.as_posix()} ou "
            f"{MUSIC_FALLBACK_CATALOG.as_posix()}."
        )

    candidates_by_risk: dict[int, list[AudioCatalogEntry]] = {0: [], 1: [], 2: []}
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
        candidates_by_risk[_content_id_risk(raw_entry)].append(entry)

    ordered_candidates: list[AudioCatalogEntry] = []
    for risk in (0, 1, 2):
        group = candidates_by_risk[risk]
        if group:
            ordered_candidates.extend(
                _rotate_candidates(group, spec.profile, episode_slug, risk)
            )

    cache_dir = media_cache_directory(project_root, cache_root, "music")
    failures: list[str] = []

    for attempt, selected_entry in enumerate(ordered_candidates, start=1):
        try:
            selected, downloaded = materialize_audio_catalog_entry(
                selected_entry,
                cache_dir,
                f"profile {spec.profile!r}",
                "background music",
            )
            if downloaded:
                try:
                    probe_audio_duration(selected)
                except RuntimeError as exc:
                    selected.unlink(missing_ok=True)
                    raise RuntimeError(f"audio remoto invalido: {exc}") from exc
            if attempt > 1:
                print(
                    f"[musica] profile={spec.profile} recuperado com alternativa "
                    f"{selected_entry.relative_file} apos {attempt - 1} falha(s)."
                )
            return ResolvedBackgroundMusic(
                profile=spec.profile,
                path=selected,
                volume=spec.volume,
            )
        except RuntimeError as exc:
            failures.append(f"{selected_entry.relative_file}: {exc}")
            if attempt < len(ordered_candidates):
                print(
                    f"[musica] faixa {selected_entry.relative_file} falhou; "
                    "tentando outra do mesmo profile."
                )

    details = "; ".join(failures)
    raise RuntimeError(
        f"Nenhuma faixa do profile de background music {spec.profile!r} "
        f"pode ser resolvida. {details}"
    )
