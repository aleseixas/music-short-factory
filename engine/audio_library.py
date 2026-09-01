from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .media_cache import download_to_cache


SUPPORTED_AUDIO_SUFFIXES = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
}


@dataclass(frozen=True)
class AudioCatalogEntry:
    local_path: Path
    relative_file: str
    url: str | None


def parse_audio_catalog_entry(
    library_root: Path,
    raw_entry: object,
    label: str,
    kind: str,
) -> AudioCatalogEntry:
    if isinstance(raw_entry, str):
        raw_file = raw_entry
        url = None
    elif isinstance(raw_entry, dict):
        raw_file = raw_entry.get("file")
        raw_url = raw_entry.get("url", "")
        if raw_url is not None and not isinstance(raw_url, str):
            raise RuntimeError(f"URL invalida em {label}.")
        url = str(raw_url or "").strip() or None
    else:
        raise RuntimeError(f"Entrada invalida em {label}; use texto ou objeto com file/url.")

    if not isinstance(raw_file, str) or not raw_file.strip():
        raise RuntimeError(f"Caminho invalido em {label}.")

    value = raw_file.strip()
    relative = Path(value)
    if (
        relative.is_absolute()
        or bool(relative.drive)
        or ".." in relative.parts
        or "://" in value
    ):
        raise RuntimeError(
            f"Caminho de {kind} precisa ser local e relativo em {label}: "
            f"{value!r}."
        )

    candidate = (library_root / relative).resolve()
    try:
        normalized = candidate.relative_to(library_root).as_posix()
    except ValueError as exc:
        raise RuntimeError(
            f"Caminho de {kind} fora da biblioteca em {label}: {value!r}."
        ) from exc

    if candidate.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_AUDIO_SUFFIXES))
        raise RuntimeError(
            f"Formato de {kind} nao suportado em {label}: "
            f"{candidate.suffix or '<sem extensao>'}. Use: {supported}."
        )
    return AudioCatalogEntry(candidate, normalized, url)


def materialize_audio_catalog_entry(
    entry: AudioCatalogEntry,
    cache_dir: Path,
    label: str,
    kind: str,
) -> tuple[Path, bool]:
    if entry.local_path.is_file():
        return entry.local_path, False
    if not entry.url:
        raise RuntimeError(
            f"Arquivo local de {kind} nao encontrado e sem URL em {label}: "
            f"{entry.relative_file}."
        )
    return (
        download_to_cache(
            entry.url,
            cache_dir,
            entry.relative_file,
            f"{kind} {entry.relative_file!r}",
        ),
        True,
    )


def resolve_local_audio_path(
    library_root: Path,
    raw_file: object,
    label: str,
    kind: str,
) -> tuple[Path, str]:
    """Compatibility wrapper for callers that still require a local string path."""
    entry = parse_audio_catalog_entry(library_root, raw_file, label, kind)
    if entry.url is not None:
        raise RuntimeError(f"{label} usa URL, mas este fluxo aceita somente arquivo local.")
    return entry.local_path, entry.relative_file
