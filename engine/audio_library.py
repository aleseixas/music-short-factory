from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

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
OPENVERSE_EXTERNAL_PREFIX = "external/openverse/"
MANUAL_EXTERNAL_PREFIX = "external/manual/"
OPENVERSE_AUDIO_DOWNLOAD_HOSTS = frozenset(
    {
        "cdn.freesound.org",
        "upload.wikimedia.org",
    }
)
MYINSTANTS_HOSTS = frozenset(
    {
        "myinstants.com",
        "www.myinstants.com",
    }
)
MYINSTANTS_API_URL = "https://myinstants-api.vercel.app/detail"
MYINSTANTS_DIRECT_OVERRIDES = {
    "cinematic-bass-drop": (
        "https://www.myinstants.com/media/sounds/"
        "169335__vibeenterprise__cinematic-deep-bass-hit.mp3"
    ),
}
MAX_EXTERNAL_MUSIC_BYTES = 100 * 1024 * 1024
MAX_EXTERNAL_SFX_BYTES = 25 * 1024 * 1024
MYINSTANTS_PAGE_TIMEOUT_SECONDS = (10, 30)


@dataclass(frozen=True)
class AudioCatalogEntry:
    local_path: Path
    relative_file: str
    url: str | None


class _MyInstantsAudioLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.audio_href: str | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.audio_href is not None:
            return
        values = dict(attrs)
        for attribute in ("href", "src"):
            raw_value = values.get(attribute)
            if not isinstance(raw_value, str):
                continue
            value = raw_value.strip()
            path = unquote(urlparse(value).path)
            if "/media/sounds/" in path and Path(path).suffix.lower() == ".mp3":
                self.audio_href = value
                return


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

    download_url = entry.url
    download_options: dict[str, object] = {}
    relative_file = entry.relative_file.casefold()
    if relative_file.startswith(OPENVERSE_EXTERNAL_PREFIX):
        host = (urlparse(download_url).hostname or "").casefold()
        if host not in OPENVERSE_AUDIO_DOWNLOAD_HOSTS:
            raise RuntimeError(
                f"Host externo nao aprovado para {kind} em {label}."
            )
        download_options = {
            "allowed_hosts": {host},
            "require_https": True,
            "max_bytes": (
                MAX_EXTERNAL_SFX_BYTES
                if kind.casefold() == "sfx"
                else MAX_EXTERNAL_MUSIC_BYTES
            ),
        }
    elif relative_file.startswith(MANUAL_EXTERNAL_PREFIX):
        download_url = _resolve_manual_audio_url(download_url, label)
        download_options = {
            "require_https": True,
            "max_bytes": (
                MAX_EXTERNAL_SFX_BYTES
                if kind.casefold() == "sfx"
                else MAX_EXTERNAL_MUSIC_BYTES
            ),
        }

    return (
        download_to_cache(
            download_url,
            cache_dir,
            entry.relative_file,
            f"{kind} {entry.relative_file!r}",
            **download_options,
        ),
        True,
    )


def _resolve_manual_audio_url(url: str, label: str) -> str:
    value = url.strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() == "https"
        and host in MYINSTANTS_HOSTS
        and "/instant/" in parsed.path.casefold()
    ):
        return _resolve_myinstants_audio_url(value, label)
    return value


def _myinstants_instant_id(page_url: str, label: str) -> str:
    path_parts = [part for part in urlparse(page_url).path.split("/") if part]
    try:
        instant_index = next(
            index for index, part in enumerate(path_parts) if part.casefold() == "instant"
        )
    except StopIteration as exc:
        raise RuntimeError(f"URL MyInstants sem id em {label}.") from exc
    if instant_index + 1 >= len(path_parts):
        raise RuntimeError(f"URL MyInstants sem id em {label}.")
    instant_id = unquote(path_parts[instant_index + 1]).strip()
    if not instant_id:
        raise RuntimeError(f"URL MyInstants sem id em {label}.")
    return instant_id


def _validate_myinstants_mp3_url(value: str, label: str) -> str:
    audio_url = value.strip()
    parsed_audio = urlparse(audio_url)
    if (
        parsed_audio.scheme.casefold() != "https"
        or (parsed_audio.hostname or "").casefold() not in MYINSTANTS_HOSTS
        or Path(unquote(parsed_audio.path)).suffix.lower() != ".mp3"
        or "/media/sounds/" not in unquote(parsed_audio.path)
    ):
        raise RuntimeError(f"Link MP3 MyInstants invalido em {label}.")
    return audio_url


def _resolve_myinstants_via_api(page_url: str, label: str) -> str:
    instant_id = _myinstants_instant_id(page_url, label)
    try:
        response = requests.get(
            MYINSTANTS_API_URL,
            params={"id": instant_id},
            headers={
                "User-Agent": "MusicShortFactory/8.2 (curated-sfx-catalog)",
                "Accept": "application/json",
            },
            timeout=MYINSTANTS_PAGE_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Falha na API MyInstants em {label}: {type(exc).__name__}."
        ) from exc

    try:
        status = int(response.status_code)
        if status >= 400:
            raise RuntimeError(f"Falha na API MyInstants em {label}: HTTP {status}.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Resposta invalida da API MyInstants em {label}.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Resposta invalida da API MyInstants em {label}.")
        data = payload.get("data")
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            raise RuntimeError(f"API MyInstants sem dados em {label}.")
        mp3 = data.get("mp3")
        if not isinstance(mp3, str) or not mp3.strip():
            raise RuntimeError(f"API MyInstants sem MP3 em {label}.")
        return _validate_myinstants_mp3_url(mp3, label)
    finally:
        response.close()


def _resolve_myinstants_audio_url(page_url: str, label: str) -> str:
    instant_id = _myinstants_instant_id(page_url, label)
    direct_override = MYINSTANTS_DIRECT_OVERRIDES.get(instant_id.casefold())
    if direct_override:
        return _validate_myinstants_mp3_url(direct_override, label)

    api_error: RuntimeError | None = None
    try:
        return _resolve_myinstants_via_api(page_url, label)
    except RuntimeError as exc:
        api_error = exc

    try:
        response = requests.get(
            page_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=MYINSTANTS_PAGE_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        detail = f"; fallback anterior: {api_error}" if api_error else ""
        raise RuntimeError(
            f"Falha ao resolver pagina MyInstants em {label}: {type(exc).__name__}{detail}."
        ) from exc

    try:
        status = int(response.status_code)
        if status >= 400:
            detail = f"; API: {api_error}" if api_error else ""
            raise RuntimeError(
                f"Falha ao resolver pagina MyInstants em {label}: HTTP {status}{detail}."
            )
        final_url = str(getattr(response, "url", page_url) or page_url).strip()
        final = urlparse(final_url)
        if (
            final.scheme.casefold() != "https"
            or (final.hostname or "").casefold() not in MYINSTANTS_HOSTS
        ):
            raise RuntimeError(f"Redirecionamento MyInstants invalido em {label}.")

        parser = _MyInstantsAudioLinkParser()
        parser.feed(response.text)
        if not parser.audio_href:
            raise RuntimeError(f"Pagina MyInstants sem link MP3 direto em {label}.")
        return _validate_myinstants_mp3_url(
            urljoin(final_url, parser.audio_href),
            label,
        )
    finally:
        response.close()


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