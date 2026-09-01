from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
from typing import Literal, Protocol
from urllib.parse import unquote, urlparse

import requests

from .audio_library import (
    MAX_EXTERNAL_MUSIC_BYTES,
    MAX_EXTERNAL_SFX_BYTES,
    OPENVERSE_AUDIO_DOWNLOAD_HOSTS,
    SUPPORTED_AUDIO_SUFFIXES,
    parse_audio_catalog_entry,
)
from .ffmpeg import probe_audio_duration
from .media_cache import download_to_cache, media_cache_directory
from .music import MUSIC_CATALOG, MUSIC_ROOT
from .sfx import SFX_CATALOG, SFX_ROOT
from .utils import load_json, validate_schema


AudioKind = Literal["music", "sfx"]

OPENVERSE_AUDIO_ENDPOINT = "https://api.openverse.org/v1/audio/"
APPLE_MUSIC_SEARCH_ENDPOINT = "https://itunes.apple.com/search"
SEARCH_HEADERS = {
    "User-Agent": (
        "MusicShortFactory/8.1 "
        "(+https://github.com/aleseixas/music-short-factory; editorial-audio-search)"
    ),
    "Accept": "application/json",
}
MAX_SEARCH_RESULTS = 20
# Acquisition is intentionally conservative. Other open licenses remain visible
# to the agent, but require a manual rights review before they enter a catalog.
DIRECT_USE_LICENSES = frozenset({"cc0", "pdm", "by"})
OPENVERSE_DOWNLOAD_HOSTS = {
    "freesound": frozenset({"cdn.freesound.org"}),
    "wikimedia": frozenset({"upload.wikimedia.org"}),
    "wikimedia_audio": frozenset({"upload.wikimedia.org"}),
}


class AudioSearchError(RuntimeError):
    """A controlled, credential-free error from an external audio provider."""


@dataclass(frozen=True)
class LocalAudioChoice:
    kind: AudioKind
    catalog_key: str
    files: tuple[str, ...]
    remote_variants: int
    catalog_path: str

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.catalog_key,
            "kind": self.kind,
            "source": "local_catalog",
            "catalog_key": self.catalog_key,
            "catalog_path": self.catalog_path,
            "files": list(self.files),
            "remote_variants": self.remote_variants,
        }


@dataclass(frozen=True)
class AudioSearchResult:
    provider_id: str
    name: str
    kind: AudioKind
    source: str
    source_page_url: str
    creator: str
    license: str
    license_url: str
    attribution: str
    duration_seconds: float | None = None
    file_format: str | None = None
    tags: tuple[str, ...] = ()
    download_url: str | None = field(default=None, repr=False)
    allowed_download_hosts: tuple[str, ...] = field(default=(), repr=False)
    download_note: str | None = None

    @property
    def suggested_file(self) -> str | None:
        if not self.download_url or not self.file_format:
            return None
        source = _safe_path_component(self.source, "source")
        identifier = _safe_path_component(self.provider_id, "audio")
        title = _safe_path_component(self.name, "track")[:48]
        return f"external/openverse/{source}-{identifier}-{title}.{self.file_format}"

    @property
    def catalog_entry(self) -> dict[str, str] | None:
        suggested_file = self.suggested_file
        if suggested_file is None or self.download_url is None:
            return None
        return {"file": suggested_file, "url": self.download_url}

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "source_page_url": self.source_page_url,
            "creator": self.creator,
            "license": self.license,
            "license_url": self.license_url,
            "attribution": self.attribution,
            "duration_seconds": self.duration_seconds,
            "file_format": self.file_format,
            "tags": list(self.tags),
            "technically_downloadable": self.catalog_entry is not None,
            "rights_verified": False,
            "download_note": self.download_note,
            "candidate_catalog_entry": self.catalog_entry,
        }


@dataclass(frozen=True)
class AudioSearchReport:
    query: str
    kind: AudioKind
    local_fallback: tuple[LocalAudioChoice, ...]
    external_results: tuple[AudioSearchResult, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "kind": self.kind,
            "local_fallback": [choice.as_dict() for choice in self.local_fallback],
            "external_results": [result.as_dict() for result in self.external_results],
            "warnings": list(self.warnings),
            "notice": (
                "Resultados externos sao dados para revisao editorial; rights_verified "
                "permanece false. Verifique fonte, autoria e licenca antes de usar."
            ),
        }


@dataclass(frozen=True)
class AcquiredAudio:
    path: Path
    duration_seconds: float
    catalog_entry: dict[str, str]
    source_page_url: str
    attribution: str
    license: str
    license_url: str

    def as_dict(self, project_root: Path) -> dict[str, object]:
        root = project_root.resolve()
        try:
            display_path = self.path.resolve().relative_to(root).as_posix()
        except ValueError:
            display_path = self.path.name
        return {
            "cache_path": display_path,
            "duration_seconds": self.duration_seconds,
            "catalog_entry": self.catalog_entry,
            "source_page_url": self.source_page_url,
            "attribution": self.attribution,
            "license": self.license,
            "license_url": self.license_url,
        }


class ExternalAudioProvider(Protocol):
    name: str

    def search(
        self,
        query: str,
        kind: AudioKind,
        limit: int,
    ) -> tuple[AudioSearchResult, ...]: ...


class OpenverseAudioProvider:
    """Search openly licensed audio without adding network calls to rendering."""

    name = "openverse"

    def search(
        self,
        query: str,
        kind: AudioKind,
        limit: int,
    ) -> tuple[AudioSearchResult, ...]:
        params: dict[str, object] = {
            "q": query,
            "page_size": limit,
            "mature": "false",
            # Avoid NC/ND results by default. License metadata still needs review.
            "license_type": "commercial,modification",
        }
        if kind == "music":
            params["category"] = "music"

        response = None
        try:
            response = requests.get(
                OPENVERSE_AUDIO_ENDPOINT,
                params=params,
                headers=SEARCH_HEADERS,
                timeout=(5, 20),
            )
            status = int(response.status_code)
            if status >= 400:
                raise AudioSearchError(
                    f"Busca Openverse indisponivel (HTTP {status}); usando catalogo local."
                )
            try:
                payload = response.json()
            except (TypeError, ValueError) as exc:
                raise AudioSearchError(
                    "Busca Openverse retornou dados invalidos; usando catalogo local."
                ) from exc
        except AudioSearchError:
            raise
        except requests.RequestException as exc:
            raise AudioSearchError(
                f"Busca Openverse indisponivel ({type(exc).__name__}); "
                "usando catalogo local."
            ) from exc
        finally:
            if response is not None:
                response.close()

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise AudioSearchError(
                "Busca Openverse retornou estrutura invalida; usando catalogo local."
            )

        normalized: list[AudioSearchResult] = []
        seen: set[tuple[str, str]] = set()
        for raw in payload["results"]:
            if not isinstance(raw, dict):
                continue
            result = _parse_openverse_result(raw, kind)
            if result is None:
                continue
            key = (result.source.casefold(), result.provider_id.casefold())
            if key in seen:
                continue
            seen.add(key)
            normalized.append(result)
            if len(normalized) >= limit:
                break
        return tuple(normalized)


class AppleMusicMetadataProvider:
    """Discover recognizable commercial songs as metadata only, never as audio."""

    name = "apple-music-metadata"

    def search(
        self,
        query: str,
        kind: AudioKind,
        limit: int,
    ) -> tuple[AudioSearchResult, ...]:
        if kind != "music":
            return ()
        response = None
        try:
            response = requests.get(
                APPLE_MUSIC_SEARCH_ENDPOINT,
                params={
                    "term": query,
                    "media": "music",
                    "entity": "song",
                    "limit": limit,
                    "country": "BR",
                },
                headers=SEARCH_HEADERS,
                timeout=(5, 20),
            )
            status = int(response.status_code)
            if status >= 400:
                raise AudioSearchError(
                    f"Busca Apple Music indisponivel (HTTP {status}); "
                    "usando outras fontes e catalogo local."
                )
            try:
                payload = response.json()
            except (TypeError, ValueError) as exc:
                raise AudioSearchError(
                    "Busca Apple Music retornou dados invalidos; usando catalogo local."
                ) from exc
        except AudioSearchError:
            raise
        except requests.RequestException as exc:
            raise AudioSearchError(
                f"Busca Apple Music indisponivel ({type(exc).__name__}); "
                "usando outras fontes e catalogo local."
            ) from exc
        finally:
            if response is not None:
                response.close()

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise AudioSearchError(
                "Busca Apple Music retornou estrutura invalida; usando catalogo local."
            )

        results: list[AudioSearchResult] = []
        seen: set[str] = set()
        for raw in payload["results"]:
            if not isinstance(raw, dict):
                continue
            provider_id = _clean_text(raw.get("trackId"), 100)
            name = _clean_text(raw.get("trackName"), 200)
            source_page_url = _safe_http_url(raw.get("trackViewUrl"), require_https=True)
            if not provider_id or not name or not source_page_url or provider_id in seen:
                continue
            seen.add(provider_id)
            creator = _clean_text(raw.get("artistName"), 160)
            genre = _clean_text(raw.get("primaryGenreName"), 80)
            duration_seconds = _milliseconds_to_seconds(raw.get("trackTimeMillis"))
            results.append(
                AudioSearchResult(
                    provider_id=provider_id,
                    name=name,
                    kind="music",
                    source="apple_music_metadata",
                    source_page_url=source_page_url,
                    creator=creator,
                    license="commercial_metadata_only",
                    license_url="",
                    attribution=(
                        f"Metadata: {name} - {creator or 'artista nao informado'}"
                    ),
                    duration_seconds=duration_seconds,
                    tags=(genre,) if genre else (),
                    download_note=(
                        "somente descoberta; obtenha direitos separadamente e nao "
                        "baixe/cacheie o preview"
                    ),
                )
            )
            if len(results) >= limit:
                break
        return tuple(results)


def search_audio(
    project_root: Path,
    query: str,
    kind: AudioKind,
    *,
    include_external: bool = False,
    limit: int = 8,
    provider: ExternalAudioProvider | None = None,
) -> AudioSearchReport:
    """Return local choices and optional external data without blocking authorship."""
    if kind not in {"music", "sfx"}:
        raise RuntimeError(f"Tipo de busca de audio invalido: {kind!r}.")

    warnings: list[str] = []
    try:
        local = list_local_audio_choices(project_root, kind, query)
    except Exception as exc:  # Search assistance must never block episode authorship.
        local = ()
        warnings.append(
            f"Nao foi possivel listar o catalogo local ({type(exc).__name__})."
        )

    normalized_query = _clean_text(query, 200)
    valid_limit = (
        not isinstance(limit, bool)
        and isinstance(limit, int)
        and 1 <= limit <= MAX_SEARCH_RESULTS
    )
    if not normalized_query:
        warnings.append("Consulta externa vazia; usando somente o catalogo local.")
        include_external = False
    if not valid_limit:
        warnings.append(
            f"Limite externo invalido; use um valor entre 1 e {MAX_SEARCH_RESULTS}."
        )
        include_external = False

    external: tuple[AudioSearchResult, ...] = ()
    if include_external:
        selected_providers: tuple[ExternalAudioProvider, ...] = (
            (provider,)
            if provider is not None
            else (
                (OpenverseAudioProvider(), AppleMusicMetadataProvider())
                if kind == "music"
                else (OpenverseAudioProvider(),)
            )
        )
        per_provider_limit = (
            limit
            if len(selected_providers) == 1
            else max(1, math.ceil(limit / len(selected_providers)))
        )
        collected: list[AudioSearchResult] = []
        seen_external: set[tuple[str, str]] = set()
        for selected_provider in selected_providers:
            try:
                raw_results = selected_provider.search(
                    normalized_query,
                    kind,
                    per_provider_limit,
                )
                for result in raw_results:
                    if not isinstance(result, AudioSearchResult) or result.kind != kind:
                        continue
                    key = (result.source.casefold(), result.provider_id.casefold())
                    if key in seen_external:
                        continue
                    seen_external.add(key)
                    collected.append(result)
            except AudioSearchError as exc:
                warnings.append(str(exc))
            except Exception as exc:  # Never expose provider URLs, payloads or credentials.
                provider_name = _safe_path_component(
                    _clean_text(getattr(selected_provider, "name", "externo"), 40),
                    "externa",
                )
                warnings.append(
                    f"Busca {provider_name or 'externa'} indisponivel "
                    f"({type(exc).__name__}); usando outras fontes e catalogo local."
                )
        external = tuple(collected[:limit])

    return AudioSearchReport(
        query=normalized_query,
        kind=kind,
        local_fallback=local,
        external_results=external,
        warnings=tuple(warnings),
    )


def list_local_audio_choices(
    project_root: Path,
    kind: AudioKind,
    query: str = "",
) -> tuple[LocalAudioChoice, ...]:
    project_root = project_root.resolve()
    if kind == "music":
        root = (project_root / MUSIC_ROOT).resolve()
        catalog_path = (project_root / MUSIC_CATALOG).resolve()
        collection_name = "profiles"
    elif kind == "sfx":
        root = (project_root / SFX_ROOT).resolve()
        catalog_path = (project_root / SFX_CATALOG).resolve()
        collection_name = "types"
    else:
        raise RuntimeError(f"Tipo de catalogo de audio invalido: {kind!r}.")

    data = load_json(catalog_path)
    validate_schema(data, catalog_path)
    collection = data.get(collection_name)
    if not isinstance(collection, dict):
        raise RuntimeError(
            f"{catalog_path.name} precisa conter um objeto em {collection_name!r}."
        )

    choices: list[LocalAudioChoice] = []
    for raw_key, raw_entries in collection.items():
        key = str(raw_key).strip()
        if not key or not isinstance(raw_entries, list) or not raw_entries:
            continue
        files: list[str] = []
        remote_variants = 0
        for index, raw_entry in enumerate(raw_entries, start=1):
            entry = parse_audio_catalog_entry(
                root,
                raw_entry,
                f"{collection_name}.{key}[{index}]",
                "audio",
            )
            files.append(entry.relative_file)
            remote_variants += int(entry.url is not None)
        choices.append(
            LocalAudioChoice(
                kind=kind,
                catalog_key=key,
                files=tuple(files),
                remote_variants=remote_variants,
                catalog_path=catalog_path.relative_to(project_root).as_posix(),
            )
        )

    terms = tuple(part for part in _search_terms(query) if part)

    def rank(choice: LocalAudioChoice) -> tuple[int, str]:
        haystack = " ".join((choice.catalog_key, *choice.files)).casefold()
        matches = sum(term in haystack for term in terms)
        return (-matches, choice.catalog_key.casefold())

    return tuple(sorted(choices, key=rank))


def acquire_audio_result(
    project_root: Path,
    result: AudioSearchResult,
    cache_root: Path | None = None,
) -> AcquiredAudio:
    """Explicitly cache and validate one provider-approved result."""
    catalog_entry = result.catalog_entry
    if catalog_entry is None or result.download_url is None:
        raise AudioSearchError(
            "O resultado selecionado nao oferece download direto aprovado."
        )
    host = _validate_acquirable_result(result)
    allowed_hosts = frozenset({host})

    project_root = project_root.resolve()
    cache_dir = media_cache_directory(project_root, cache_root, result.kind)
    max_bytes = (
        MAX_EXTERNAL_MUSIC_BYTES
        if result.kind == "music"
        else MAX_EXTERNAL_SFX_BYTES
    )
    try:
        path = download_to_cache(
            result.download_url,
            cache_dir,
            catalog_entry["file"],
            f"resultado externo {result.provider_id!r}",
            allowed_hosts=allowed_hosts,
            require_https=True,
            max_bytes=max_bytes,
        )
        try:
            duration = probe_audio_duration(path)
        except RuntimeError:
            path.unlink(missing_ok=True)
            raise
    except RuntimeError as exc:
        raise AudioSearchError(
            "Nao foi possivel obter e validar o resultado externo selecionado."
        ) from exc

    return AcquiredAudio(
        path=path,
        duration_seconds=duration,
        catalog_entry=catalog_entry,
        source_page_url=result.source_page_url,
        attribution=result.attribution,
        license=result.license,
        license_url=result.license_url,
    )


def main(
    argv: Sequence[str] | None = None,
    default_project_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pesquisa opcoes locais e, com opt-in, audio externo para autoria editorial."
        )
    )
    parser.add_argument("query", help="Consulta editorial (ex.: cinematic impact).")
    parser.add_argument("--kind", required=True, choices=("music", "sfx"))
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument(
        "--external",
        action="store_true",
        help="Habilita Openverse e, para musica, Apple Music apenas como metadados.",
    )
    parser.add_argument(
        "--download",
        type=int,
        metavar="N",
        help="Baixa e valida explicitamente o resultado externo N (base 1).",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root or Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    report = search_audio(
        args.project_root,
        args.query,
        args.kind,
        include_external=args.external,
        limit=args.limit,
    )
    output = report.as_dict()
    warnings = list(output["warnings"])
    if args.download is not None:
        selected_index = args.download - 1
        if not args.external:
            warnings.append("--download requer --external; nenhum arquivo foi baixado.")
        elif selected_index < 0 or selected_index >= len(report.external_results):
            warnings.append("Indice de resultado externo invalido; nenhum arquivo foi baixado.")
        else:
            try:
                acquired = acquire_audio_result(
                    args.project_root,
                    report.external_results[selected_index],
                )
                output["downloaded"] = acquired.as_dict(args.project_root)
            except AudioSearchError as exc:
                warnings.append(str(exc))
    output["warnings"] = warnings
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _parse_openverse_result(
    raw: dict[object, object],
    kind: AudioKind,
) -> AudioSearchResult | None:
    provider_id = _clean_text(raw.get("id"), 100)
    name = _clean_text(raw.get("title"), 200)
    source = _clean_text(raw.get("source") or raw.get("provider"), 60).casefold()
    if not provider_id or not name or not source:
        return None

    category = _clean_text(raw.get("category"), 40).casefold()
    if kind == "sfx" and category not in {"", "sound_effect"}:
        return None

    source_page_url = _safe_http_url(
        raw.get("foreign_landing_url") or raw.get("detail_url")
    )
    license_code = _clean_text(raw.get("license"), 40).casefold()
    license_url = _safe_http_url(raw.get("license_url"))
    creator = _clean_text(raw.get("creator"), 160)
    raw_attribution = _clean_text(raw.get("attribution"), 500)
    attribution = raw_attribution
    if not attribution:
        attribution = _clean_text(
            f"{name} - {creator or 'autor nao informado'} - {license_code or 'licenca nao informada'}",
            500,
        )

    duration_seconds = _openverse_duration_seconds(raw.get("duration"))
    tags = _openverse_tags(raw.get("tags"))
    download_url, file_format, allowed_hosts, download_note = _openverse_download(
        raw,
        source,
        license_code,
        bool(
            _is_https_url(source_page_url)
            and _is_https_url(license_url)
            and _license_url_matches(license_code, license_url)
        ),
        bool(creator),
        bool(raw_attribution),
    )
    return AudioSearchResult(
        provider_id=provider_id,
        name=name,
        kind=kind,
        source=source,
        source_page_url=source_page_url,
        creator=creator,
        license=license_code,
        license_url=license_url,
        attribution=attribution,
        duration_seconds=duration_seconds,
        file_format=file_format,
        tags=tags,
        download_url=download_url,
        allowed_download_hosts=allowed_hosts,
        download_note=download_note,
    )


def _openverse_download(
    raw: dict[object, object],
    source: str,
    license_code: str,
    has_rights_metadata: bool,
    has_creator: bool,
    has_attribution: bool,
) -> tuple[str | None, str | None, tuple[str, ...], str | None]:
    raw_url = _safe_http_url(raw.get("url"), require_https=True)
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").casefold()
    suffix = Path(unquote(parsed.path)).suffix.lower()
    allowed_hosts = OPENVERSE_DOWNLOAD_HOSTS.get(source, frozenset())

    if license_code not in DIRECT_USE_LICENSES:
        return None, _format_from_suffix(suffix), (), "licenca exige revisao manual"
    if not has_rights_metadata:
        return None, _format_from_suffix(suffix), (), "fonte/licenca incompleta"
    if license_code == "by" and (not has_creator or not has_attribution):
        return None, _format_from_suffix(suffix), (), "atribuicao CC BY incompleta"
    if not raw_url or suffix not in SUPPORTED_AUDIO_SUFFIXES:
        return None, _format_from_suffix(suffix), (), "URL nao e um arquivo de audio direto suportado"
    if host not in allowed_hosts:
        return None, _format_from_suffix(suffix), (), "host de download nao aprovado"
    return raw_url, suffix.lstrip("."), (host,), None


def _openverse_duration_seconds(raw_value: object) -> float | None:
    return _milliseconds_to_seconds(raw_value)


def _milliseconds_to_seconds(raw_value: object) -> float | None:
    if isinstance(raw_value, bool):
        return None
    try:
        milliseconds = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(milliseconds) or milliseconds <= 0:
        return None
    return round(milliseconds / 1000.0, 3)


def _openverse_tags(raw_tags: object) -> tuple[str, ...]:
    if not isinstance(raw_tags, list):
        return ()
    tags: list[str] = []
    seen: set[str] = set()
    for raw in raw_tags:
        value = raw.get("name") if isinstance(raw, dict) else raw
        tag = _clean_text(value, 50)
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= 8:
            break
    return tuple(tags)


def _safe_http_url(raw_value: object, *, require_https: bool = False) -> str:
    value = _clean_text(raw_value, 2048)
    if not value:
        return ""
    parsed = urlparse(value)
    schemes = {"https"} if require_https else {"http", "https"}
    if (
        parsed.scheme not in schemes
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return value


def _validate_acquirable_result(result: AudioSearchResult) -> str:
    if result.kind not in {"music", "sfx"}:
        raise AudioSearchError("Tipo do resultado externo e invalido.")
    if result.license not in DIRECT_USE_LICENSES:
        raise AudioSearchError("A licenca do resultado exige revisao manual.")
    if not _is_https_url(result.source_page_url) or not _is_https_url(result.license_url):
        raise AudioSearchError("Fonte ou licenca HTTPS ausente no resultado externo.")
    if not _license_url_matches(result.license, result.license_url):
        raise AudioSearchError("A URL da licenca nao corresponde ao resultado externo.")
    if result.license == "by" and (not result.creator.strip() or not result.attribution.strip()):
        raise AudioSearchError("A atribuicao CC BY do resultado esta incompleta.")
    if result.download_url is None or not _is_https_url(result.download_url):
        raise AudioSearchError("O resultado nao possui URL HTTPS direta aprovada.")

    parsed = urlparse(result.download_url)
    host = (parsed.hostname or "").casefold()
    suffix = Path(unquote(parsed.path)).suffix.lower()
    source_hosts = OPENVERSE_DOWNLOAD_HOSTS.get(result.source.casefold(), frozenset())
    if (
        host not in OPENVERSE_AUDIO_DOWNLOAD_HOSTS
        or host not in source_hosts
        or suffix not in SUPPORTED_AUDIO_SUFFIXES
        or result.file_format != suffix.lstrip(".")
    ):
        raise AudioSearchError("Formato ou host do resultado externo nao e aprovado.")
    return host


def _is_https_url(value: str) -> bool:
    return bool(_safe_http_url(value, require_https=True))


def _license_url_matches(license_code: str, license_url: str) -> bool:
    parsed = urlparse(license_url)
    if parsed.scheme != "https" or (parsed.hostname or "").casefold() != "creativecommons.org":
        return False
    path = parsed.path.casefold().rstrip("/") + "/"
    expected_prefixes = {
        "cc0": "/publicdomain/zero/",
        "pdm": "/publicdomain/mark/",
        "by": "/licenses/by/",
    }
    expected = expected_prefixes.get(license_code)
    return expected is not None and path.startswith(expected)


def _clean_text(raw_value: object, max_length: int) -> str:
    if raw_value is None:
        return ""
    value = str(raw_value)
    value = " ".join(value.replace("\x00", " ").split())
    return value[:max_length].strip()


def _safe_path_component(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or fallback


def _search_terms(value: str) -> tuple[str, ...]:
    return tuple(
        term
        for term in re.split(r"[^a-z0-9]+", value.casefold())
        if term
    )


def _format_from_suffix(suffix: str) -> str | None:
    return suffix.lstrip(".") if suffix in SUPPORTED_AUDIO_SUFFIXES else None
