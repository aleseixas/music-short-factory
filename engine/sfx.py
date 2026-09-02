from __future__ import annotations

import hashlib
import math
from pathlib import Path

from .audio_library import (
    AudioCatalogEntry,
    materialize_audio_catalog_entry,
    parse_audio_catalog_entry,
)
from .ffmpeg import probe_audio_duration, probe_duration
from .media_cache import media_cache_directory
from .models import ResolvedSfxCue, SfxCue
from .utils import load_json, validate_schema


SFX_ROOT = Path("assets") / "audio" / "sfx"
SFX_CATALOG = SFX_ROOT / "catalog.json"
FULL_PLAYBACK_TYPE_PREFIXES = ("meme_br_",)


def resolve_sfx_cues(
    project_root: Path,
    cues: tuple[SfxCue, ...],
    episode_slug: str,
    cache_root: Path | None = None,
) -> tuple[ResolvedSfxCue, ...]:
    if not cues:
        return ()

    project_root = project_root.resolve()
    sfx_root = (project_root / SFX_ROOT).resolve()
    catalog_path = (project_root / SFX_CATALOG).resolve()
    try:
        sfx_root.relative_to(project_root)
        catalog_path.relative_to(sfx_root)
    except ValueError as exc:
        raise RuntimeError("Biblioteca local de SFX fora do projeto.") from exc

    if not catalog_path.is_file():
        raise RuntimeError(f"Catalogo de SFX ausente: {SFX_CATALOG.as_posix()}.")
    data = load_json(catalog_path)
    validate_schema(data, catalog_path)
    types = data.get("types")
    if not isinstance(types, dict):
        raise RuntimeError(
            f"{SFX_CATALOG.as_posix()} precisa conter um objeto em 'types'."
        )

    sfx_cache_dir = media_cache_directory(project_root, cache_root, "sfx")
    candidates_by_type: dict[str, tuple[AudioCatalogEntry, ...]] = {}
    resolved_by_file: dict[str, tuple[Path, bool]] = {}
    durations_by_path: dict[Path, float] = {}
    occurrences: dict[str, int] = {}
    resolved: list[ResolvedSfxCue] = []
    for cue_index, cue in enumerate(cues, start=1):
        _validate_trim_values(cue, cue_index)
        candidates = candidates_by_type.get(cue.type)
        if candidates is None:
            candidates = _resolve_type_candidates(sfx_root, types, cue.type)
            candidates_by_type[cue.type] = candidates

        occurrence = occurrences.get(cue.type, 0)
        occurrences[cue.type] = occurrence + 1
        digest = hashlib.sha256(
            f"{episode_slug}\0{cue.type}".encode("utf-8")
        ).digest()
        offset = int.from_bytes(digest[:8], "big") % len(candidates)
        selected_entry = candidates[(offset + occurrence) % len(candidates)]
        selected_data = resolved_by_file.get(selected_entry.relative_file)
        if selected_data is None:
            selected_data = materialize_audio_catalog_entry(
                selected_entry,
                sfx_cache_dir,
                f"type {cue.type!r}",
                "SFX",
            )
            resolved_by_file[selected_entry.relative_file] = selected_data
        selected, downloaded = selected_data
        if downloaded or cue.source_start_seconds != 0 or cue.duration_seconds is not None:
            source_duration = durations_by_path.get(selected)
            if source_duration is None:
                try:
                    source_duration = (
                        probe_audio_duration(selected)
                        if downloaded
                        else probe_duration(selected)
                    )
                except RuntimeError as exc:
                    if downloaded:
                        selected.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Nao foi possivel validar a duracao do SFX da cue "
                        f"{cue_index} ({cue.type!r}): {exc}"
                    ) from exc
                durations_by_path[selected] = source_duration
            _validate_trim_window(cue, cue_index, selected, source_duration)
        resolved.append(
            ResolvedSfxCue(
                index=cue_index,
                time_seconds=cue.time_seconds,
                type=cue.type,
                path=selected,
                volume=cue.volume,
                source_start_seconds=cue.source_start_seconds,
                duration_seconds=cue.duration_seconds,
            )
        )
    return tuple(resolved)


def _validate_trim_values(cue: SfxCue, cue_index: int) -> None:
    label = f"sfx_cues[{cue_index}]"
    if (
        isinstance(cue.source_start_seconds, bool)
        or not isinstance(cue.source_start_seconds, (int, float))
        or not math.isfinite(cue.source_start_seconds)
        or cue.source_start_seconds < 0
    ):
        raise RuntimeError(
            f"{label}.source_start_seconds precisa ser maior ou igual a zero."
        )
    if cue.duration_seconds is not None and (
        isinstance(cue.duration_seconds, bool)
        or not isinstance(cue.duration_seconds, (int, float))
        or not math.isfinite(cue.duration_seconds)
        or cue.duration_seconds <= 0
    ):
        raise RuntimeError(f"{label}.duration_seconds precisa ser maior que zero.")

    normalized_type = cue.type.casefold()
    if normalized_type.startswith(FULL_PLAYBACK_TYPE_PREFIXES) and (
        cue.source_start_seconds != 0 or cue.duration_seconds is not None
    ):
        raise RuntimeError(
            f"{label} ({cue.type!r}) precisa tocar o meme completo: "
            "use source_start_seconds=0 e omita duration_seconds."
        )


def _validate_trim_window(
    cue: SfxCue,
    cue_index: int,
    path: Path,
    source_duration: float,
) -> None:
    label = f"sfx_cues[{cue_index}]"
    tolerance = 1e-6
    if cue.source_start_seconds >= source_duration - tolerance:
        raise RuntimeError(
            f"Recorte invalido em {label} ({cue.type!r}, {path.name}): "
            f"source_start_seconds={cue.source_start_seconds:.3f}s fica no ou "
            f"apos o fim do arquivo ({source_duration:.3f}s)."
        )
    if cue.duration_seconds is None:
        return
    requested_end = cue.source_start_seconds + cue.duration_seconds
    if requested_end > source_duration + tolerance:
        raise RuntimeError(
            f"Recorte invalido em {label} ({cue.type!r}, {path.name}): "
            f"fim solicitado={requested_end:.3f}s ultrapassa a duracao real "
            f"do arquivo ({source_duration:.3f}s)."
        )


def _resolve_type_candidates(
    sfx_root: Path,
    types: dict[object, object],
    sfx_type: str,
) -> tuple[AudioCatalogEntry, ...]:
    raw_files = types.get(sfx_type)
    if raw_files is None:
        raise RuntimeError(
            f"Type de SFX inexistente: {sfx_type!r}. "
            f"Configure-o em {SFX_CATALOG.as_posix()}."
        )
    if not isinstance(raw_files, list) or not raw_files:
        raise RuntimeError(
            f"Type de SFX {sfx_type!r} precisa listar ao menos um arquivo."
        )

    candidates: list[AudioCatalogEntry] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(raw_files, start=1):
        label = f"types.{sfx_type}[{index}]"
        entry = parse_audio_catalog_entry(
            sfx_root,
            raw_entry,
            label,
            "SFX",
        )
        key = entry.relative_file.casefold()
        if key in seen:
            raise RuntimeError(
                f"Arquivo duplicado no type de SFX {sfx_type!r}: "
                f"{entry.relative_file!r}."
            )
        seen.add(key)
        if not entry.local_path.is_file() and not entry.url:
            raise RuntimeError(
                f"Arquivo local do type de SFX {sfx_type!r} nao encontrado: "
                f"{entry.relative_file}."
            )
        candidates.append(entry)

    candidates.sort(key=lambda entry: entry.relative_file.casefold())
    return tuple(candidates)
