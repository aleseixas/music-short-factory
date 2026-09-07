from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
import html
import json
import math
from pathlib import Path
import re
from typing import Literal, Protocol
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError
import requests

from .ffmpeg import probe_video_stream, run_ffmpeg_capture
from .media_cache import download_to_cache, media_cache_directory
from .models import (
    DEFAULT_VIDEO_SPEED,
    MAX_FREEZE_DURATION_SECONDS,
    MAX_VIDEO_SPEED,
    MIN_FREEZE_DURATION_SECONDS,
    MIN_VIDEO_SPEED,
    VIDEO_ASSET_EXTENSIONS,
    FreezeFrameSpec,
)
from .timeline import resolve_freeze_frame


VisualKind = Literal["image", "video", "any"]

OPENVERSE_IMAGES_ENDPOINT = "https://api.openverse.org/v1/images/"
WIKIMEDIA_COMMONS_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
VISUAL_SEARCH_HEADERS = {
    "User-Agent": (
        "MusicShortFactory/9.0 "
        "(+https://github.com/aleseixas/music-short-factory; editorial-visual-search)"
    ),
    "Accept": "application/json",
}
SUPPORTED_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
SUPPORTED_VISUAL_SUFFIXES = SUPPORTED_IMAGE_SUFFIXES | VIDEO_ASSET_EXTENSIONS
MAX_VISUAL_RESULTS = 30
MAX_VISUAL_QUERIES = 8
MAX_EXTERNAL_IMAGE_BYTES = 50 * 1024 * 1024
MAX_EXTERNAL_VIDEO_BYTES = 500 * 1024 * 1024
TARGET_WIDTH = 720
TARGET_HEIGHT = 1280
MOTION_SAMPLE_FPS = 2
MOTION_WINDOW_SECONDS = 3.0
STATIC_SAMPLE_THRESHOLD = 1.0
STATIC_FRAME_RATIO = 0.80
OPENVERSE_IMAGE_HOSTS = {
    "flickr": frozenset({"live.staticflickr.com"}),
    "wikimedia": frozenset({"upload.wikimedia.org"}),
    "wikimedia_commons": frozenset({"upload.wikimedia.org"}),
}


class VisualSearchError(RuntimeError):
    """Controlled, credential-free error from visual discovery or inspection."""


@dataclass(frozen=True)
class VisualSearchResult:
    provider_id: str
    name: str
    kind: Literal["image", "video"]
    source: str
    source_page_url: str
    creator: str
    license: str
    license_url: str
    attribution: str
    search_provider: str = ""
    description: str = ""
    width: int | None = None
    height: int | None = None
    file_size_bytes: int | None = None
    duration_seconds: float | None = None
    fps: float | None = None
    file_format: str | None = None
    mime_type: str | None = None
    thumbnail_url: str | None = None
    tags: tuple[str, ...] = ()
    matched_queries: tuple[str, ...] = ()
    provider_rank: int | None = None
    download_url: str | None = field(default=None, repr=False)
    allowed_download_hosts: tuple[str, ...] = field(default=(), repr=False)
    download_note: str | None = None

    @property
    def aspect_ratio(self) -> float | None:
        if not self.width or not self.height:
            return None
        return round(self.width / self.height, 6)

    @property
    def suggested_file(self) -> str | None:
        if not self.download_url or not self.file_format:
            return None
        source = _safe_path_component(self.source, "source")[:24]
        identifier = _safe_path_component(self.provider_id, "asset")[:32]
        title = _safe_path_component(Path(self.name).stem, "visual")[:42]
        return f"{source}-{identifier}-{title}.{self.file_format}"

    @property
    def candidate_asset_entry(self) -> dict[str, object] | None:
        suggested_file = self.suggested_file
        if suggested_file is None or self.download_url is None:
            return None
        identifier = _safe_path_component(
            f"{self.source}-{self.provider_id}", "visual_asset"
        )[:72]
        source_label = {
            "wikimedia": "Wikimedia Commons",
            "wikimedia_commons": "Wikimedia Commons",
            "flickr": "Flickr",
        }.get(self.source, self.source)
        credit = (
            f"{self.creator} / {source_label}"
            if self.creator and source_label
            else self.attribution or self.creator or self.source
        )
        return {
            "id": identifier,
            "file": suggested_file,
            "url": self.download_url,
            "credit": credit,
            "license": self.license,
            "focus": {"x": 0.5, "y": 0.5},
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "search_provider": self.search_provider,
            "provider_id": self.provider_id,
            "source_page_url": self.source_page_url,
            "creator": self.creator,
            "license": self.license,
            "license_url": self.license_url,
            "attribution": self.attribution,
            "description": self.description,
            "thumbnail_url": self.thumbnail_url,
            "tags": list(self.tags),
            "matched_queries": list(self.matched_queries),
            "provider_rank": self.provider_rank,
            "technical_metadata": {
                "width": self.width,
                "height": self.height,
                "aspect_ratio": self.aspect_ratio,
                "file_size_bytes": self.file_size_bytes,
                "duration_seconds": self.duration_seconds,
                "fps": self.fps,
                "file_format": self.file_format,
                "mime_type": self.mime_type,
            },
            "technically_downloadable": self.candidate_asset_entry is not None,
            "rights_verified": False,
            "download_note": self.download_note,
            "visual_score": None,
            "inspection_required_for_visual_score": True,
            "candidate_asset_entry": self.candidate_asset_entry,
        }


@dataclass(frozen=True)
class VisualSearchReport:
    queries: tuple[str, ...]
    kind: VisualKind
    results: tuple[VisualSearchResult, ...]
    warnings: tuple[str, ...] = ()
    fallback_guidance: tuple[str, ...] = (
        "Tente outras consultas ou outro provider.",
        "Reutilize um asset valido ja disponivel no episodio.",
        "Use uma imagem relevante quando nenhum video adequado estiver disponivel.",
    )

    @property
    def external_results(self) -> tuple[VisualSearchResult, ...]:
        return self.results

    def as_dict(self) -> dict[str, object]:
        return {
            "queries": list(self.queries),
            "kind": self.kind,
            "results": [result.as_dict() for result in self.results],
            "warnings": list(self.warnings),
            "fallback_guidance": list(self.fallback_guidance),
            "notice": (
                "Conteudo remoto e apenas dado para decisao editorial. "
                "visual_score e tecnico, nao mede relevancia semantica, e nao deve "
                "ser salvo em assets.json ou timeline.json."
            ),
        }


@dataclass(frozen=True)
class MotionAnalysis:
    opening_motion_score: float
    motion_score: float
    static_sample_ratio: float
    is_practically_static: bool
    sample_count: int
    sampled_windows: tuple[tuple[float, float], ...]

    @property
    def practically_static(self) -> bool:
        return self.is_practically_static

    def as_dict(self) -> dict[str, object]:
        return {
            "opening_motion_score": self.opening_motion_score,
            "motion_score": self.motion_score,
            "static_sample_ratio": self.static_sample_ratio,
            "practically_static": self.is_practically_static,
            "is_practically_static": self.is_practically_static,
            "sample_count": self.sample_count,
            "sampled_windows": [
                {"start_seconds": start, "duration_seconds": duration}
                for start, duration in self.sampled_windows
            ],
        }


@dataclass(frozen=True)
class TrimAssessment:
    safe_for_shot: bool | None
    source_start_seconds: float
    source_end_seconds: float
    available_seconds: float
    required_seconds: float | None
    margin_seconds: float | None
    reason: str
    speed: float | None = DEFAULT_VIDEO_SPEED
    required_output_seconds: float | None = None
    moving_output_seconds: float | None = None
    output_fps: int | None = None
    freeze_start_frame: int | None = None
    freeze_duration_frames: int | None = None

    @property
    def freeze_frame(self) -> dict[str, object] | None:
        if (
            self.output_fps is None
            or self.freeze_start_frame is None
            or self.freeze_duration_frames is None
        ):
            return None
        return {
            "start_frame": self.freeze_start_frame,
            "duration_frames": self.freeze_duration_frames,
            "start_seconds": round(self.freeze_start_frame / self.output_fps, 6),
            "duration_seconds": round(
                self.freeze_duration_frames / self.output_fps,
                6,
            ),
            "source_frames_saved": self.freeze_duration_frames - 1,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "safe": self.safe_for_shot,
            "trim_safe": self.safe_for_shot,
            "safe_for_shot": self.safe_for_shot,
            "source_start_seconds": self.source_start_seconds,
            "source_end_seconds": self.source_end_seconds,
            "available_seconds": self.available_seconds,
            "required_seconds": self.required_seconds,
            "required_output_seconds": self.required_output_seconds,
            "moving_output_seconds": self.moving_output_seconds,
            "margin_seconds": self.margin_seconds,
            "speed": self.speed,
            "output_fps": self.output_fps,
            "freeze_frame": self.freeze_frame,
            "reason": self.reason,
            "loop_required": self.reason == "insufficient_duration_no_loop",
        }


@dataclass(frozen=True)
class VisualInspection:
    result: VisualSearchResult
    path: Path
    width: int
    height: int
    aspect_ratio: float
    duration_seconds: float | None
    fps: float | None
    motion: MotionAnalysis | None
    trim: TrimAssessment | None
    visual_score: float
    score_breakdown: dict[str, float]
    warnings: tuple[str, ...] = ()

    @property
    def opening_motion_score(self) -> float | None:
        return self.motion.opening_motion_score if self.motion else None

    @property
    def motion_score(self) -> float | None:
        return self.motion.motion_score if self.motion else None

    @property
    def is_practically_static(self) -> bool | None:
        return self.motion.is_practically_static if self.motion else None

    @property
    def trim_safe(self) -> bool | None:
        return self.trim.safe_for_shot if self.trim else None

    def as_dict(self, project_root: Path | None = None) -> dict[str, object]:
        display_path = self.path.name
        if project_root is not None:
            try:
                display_path = self.path.resolve().relative_to(
                    project_root.resolve()
                ).as_posix()
            except ValueError:
                pass
        return {
            "candidate": self.result.as_dict(),
            "cache_path": display_path,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "duration_seconds": self.duration_seconds,
            "fps": self.fps,
            "opening_motion_score": self.opening_motion_score,
            "motion_score": self.motion_score,
            "is_practically_static": self.is_practically_static,
            "practically_static": self.is_practically_static,
            "technical_metadata": {
                "width": self.width,
                "height": self.height,
                "aspect_ratio": self.aspect_ratio,
                "duration_seconds": self.duration_seconds,
                "fps": self.fps,
            },
            "motion": self.motion.as_dict() if self.motion else None,
            "trim": self.trim.as_dict() if self.trim else None,
            "visual_score": self.visual_score,
            "score_breakdown": self.score_breakdown,
            "score_components": self.score_breakdown,
            "warnings": list(self.warnings),
            "candidate_asset_entry": self.result.candidate_asset_entry,
        }


class ExternalVisualProvider(Protocol):
    name: str

    def search(
        self,
        query: str,
        kind: VisualKind,
        limit: int,
    ) -> tuple[VisualSearchResult, ...]: ...


class OpenverseImageProvider:
    """Search Openverse image metadata; rendering remains completely offline."""

    name = "openverse-images"

    def search(
        self,
        query: str,
        kind: VisualKind = "image",
        limit: int = 8,
    ) -> tuple[VisualSearchResult, ...]:
        if kind == "video":
            return ()
        payload = _request_json(
            OPENVERSE_IMAGES_ENDPOINT,
            {
                "q": query,
                "page_size": limit,
                "mature": "false",
                "license_type": "commercial,modification",
            },
            "Openverse Images",
        )
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise VisualSearchError(
                "Busca Openverse Images retornou estrutura invalida."
            )

        results: list[VisualSearchResult] = []
        for rank, raw in enumerate(raw_results, start=1):
            if not isinstance(raw, dict):
                continue
            parsed = _parse_openverse_image(raw, rank)
            if parsed is not None:
                results.append(parsed)
            if len(results) >= limit:
                break
        return tuple(results)


class WikimediaCommonsProvider:
    """Search images and supported videos through the official MediaWiki API."""

    name = "wikimedia-commons"

    def search(
        self,
        query: str,
        kind: VisualKind = "any",
        limit: int = 8,
    ) -> tuple[VisualSearchResult, ...]:
        kinds: tuple[Literal["image", "video"], ...]
        if kind == "any":
            # Video comes first so the authoring agent sees moving candidates before
            # falling back to a still image for the same query.
            kinds = ("video", "image")
        elif kind in {"image", "video"}:
            kinds = (kind,)
        else:
            raise VisualSearchError("Tipo de busca visual invalido.")

        results: list[VisualSearchResult] = []
        search_errors: list[VisualSearchError] = []
        for selected_kind in kinds:
            try:
                payload = _request_json(
                    WIKIMEDIA_COMMONS_ENDPOINT,
                    {
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": (
                            f"{query} filetype:video"
                            if selected_kind == "video"
                            else f"{query} filetype:bitmap"
                        ),
                        "gsrnamespace": 6,
                        "gsrlimit": limit,
                        "prop": "imageinfo",
                        "iiprop": "url|size|mime|mediatype|extmetadata",
                        "iiurlwidth": 480,
                        "format": "json",
                        "formatversion": 2,
                        "origin": "*",
                    },
                    "Wikimedia Commons",
                )
            except VisualSearchError as exc:
                search_errors.append(exc)
                continue
            pages = _wikimedia_pages(payload)
            for rank, raw in enumerate(pages, start=1):
                parsed = _parse_wikimedia_result(raw, selected_kind, rank)
                if parsed is not None:
                    results.append(parsed)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        if not results and search_errors:
            raise search_errors[0]
        return tuple(results[:limit])


def search_visual(
    project_root: Path,
    queries: str | Sequence[str],
    kind: VisualKind = "any",
    *,
    include_external: bool = False,
    limit: int = 12,
    providers: Sequence[ExternalVisualProvider] | None = None,
) -> VisualSearchReport:
    """Search visual candidates without ever making the renderer access the web."""
    del project_root  # Reserved for future local-catalog discovery; keeps API stable.
    if kind not in {"image", "video", "any"}:
        raise RuntimeError(f"Tipo de busca visual invalido: {kind!r}.")

    normalized_queries = _normalize_queries(queries)
    warnings: list[str] = []
    if not normalized_queries:
        warnings.append("Nenhuma consulta visual valida foi informada.")
        include_external = False
    if len(normalized_queries) > MAX_VISUAL_QUERIES:
        warnings.append(
            f"Somente as primeiras {MAX_VISUAL_QUERIES} consultas foram usadas."
        )
        normalized_queries = normalized_queries[:MAX_VISUAL_QUERIES]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_VISUAL_RESULTS:
        warnings.append(
            f"Limite visual invalido; use um valor entre 1 e {MAX_VISUAL_RESULTS}."
        )
        include_external = False
        limit = 12

    selected_providers: tuple[ExternalVisualProvider, ...] = tuple(
        providers
        if providers is not None
        else (WikimediaCommonsProvider(), OpenverseImageProvider())
    )
    collected: list[VisualSearchResult] = []
    result_index: dict[str, int] = {}
    query_buckets: list[list[int]] = []
    if include_external:
        per_search_limit = max(
            3,
            math.ceil(limit / max(1, len(normalized_queries))),
        )
        for query in normalized_queries:
            query_bucket: list[int] = []
            query_buckets.append(query_bucket)
            for provider in selected_providers:
                try:
                    provider_results = provider.search(query, kind, per_search_limit)
                    for result in provider_results:
                        if not isinstance(result, VisualSearchResult):
                            continue
                        if kind != "any" and result.kind != kind:
                            continue
                        key = _dedupe_key(result)
                        if key in result_index:
                            existing_index = result_index[key]
                            if existing_index not in query_bucket:
                                query_bucket.append(existing_index)
                            existing = collected[existing_index]
                            if query not in existing.matched_queries:
                                collected[existing_index] = replace(
                                    existing,
                                    matched_queries=(*existing.matched_queries, query),
                                )
                            continue
                        result_index[key] = len(collected)
                        query_bucket.append(len(collected))
                        collected.append(
                            replace(result, matched_queries=(query,))
                        )
                except VisualSearchError:
                    provider_name = _safe_path_component(
                        _clean_text(getattr(provider, "name", "externo"), 50),
                        "externo",
                    )
                    warnings.append(
                        f"Busca visual {provider_name} indisponivel; "
                        "continuando com outras opcoes."
                    )
                except Exception as exc:
                    provider_name = _safe_path_component(
                        _clean_text(getattr(provider, "name", "externo"), 50),
                        "externo",
                    )
                    warnings.append(
                        f"Busca visual {provider_name} indisponivel "
                        f"({type(exc).__name__}); continuando com outras opcoes."
                    )
    ordered_results = _round_robin_results(collected, query_buckets)
    return VisualSearchReport(
        queries=normalized_queries,
        kind=kind,
        results=tuple(ordered_results[:limit]),
        warnings=tuple(_dedupe_strings(warnings)),
    )


def inspect_visual_result(
    project_root: Path,
    result: VisualSearchResult,
    *,
    shot_duration_seconds: float | None = None,
    source_start_seconds: float = 0.0,
    source_end_seconds: float | None = None,
    crossfade_seconds: float = 0.0,
    speed: float = DEFAULT_VIDEO_SPEED,
    freeze_start_seconds: float | None = None,
    freeze_duration_seconds: float | None = None,
    output_fps: int = 30,
    cache_root: Path | None = None,
    target_width: int = TARGET_WIDTH,
    target_height: int = TARGET_HEIGHT,
) -> VisualInspection:
    """Download, validate and technically score one explicitly selected candidate."""
    if not isinstance(result, VisualSearchResult):
        raise VisualSearchError("Candidato visual invalido para inspecao.")
    if result.candidate_asset_entry is None or result.download_url is None:
        raise VisualSearchError(
            "O candidato visual selecionado nao oferece download direto aprovado."
        )
    if target_width <= 0 or target_height <= 0:
        raise VisualSearchError("Resolucao alvo invalida para inspecao visual.")
    if result.kind != "video" and (
        not _is_finite_number(speed) or float(speed) != DEFAULT_VIDEO_SPEED
    ):
        raise VisualSearchError("speed so pode ser usado na inspecao de video.")
    if result.kind != "video" and (
        freeze_start_seconds is not None or freeze_duration_seconds is not None
    ):
        raise VisualSearchError(
            "freeze_frame so pode ser usado na inspecao de video."
        )

    category = "video" if result.kind == "video" else "image"
    project_root = project_root.resolve()
    cache_dir = media_cache_directory(project_root, cache_root, category)
    max_bytes = (
        MAX_EXTERNAL_VIDEO_BYTES
        if result.kind == "video"
        else MAX_EXTERNAL_IMAGE_BYTES
    )
    host = (urlparse(result.download_url).hostname or "").casefold()
    if not host or host not in set(result.allowed_download_hosts):
        raise VisualSearchError(
            "O host do candidato visual nao esta aprovado para download."
        )

    try:
        path = download_to_cache(
            result.download_url,
            cache_dir,
            result.suggested_file or "visual.bin",
            f"candidato visual {result.provider_id!r}",
            allowed_hosts={host},
            require_https=True,
            max_bytes=max_bytes,
        )
    except RuntimeError as exc:
        raise VisualSearchError(
            "Nao foi possivel obter o candidato visual selecionado."
        ) from exc

    if result.kind == "image":
        try:
            width, height = _probe_image(path)
        except RuntimeError as exc:
            path.unlink(missing_ok=True)
            raise VisualSearchError(
                "A imagem selecionada e invalida ou nao pode ser lida."
            ) from exc
        score, breakdown = calculate_visual_score(
            "image",
            width,
            height,
            target_width=target_width,
            target_height=target_height,
        )
        return VisualInspection(
            result=result,
            path=path,
            width=width,
            height=height,
            aspect_ratio=round(width / height, 6),
            duration_seconds=None,
            fps=None,
            motion=None,
            trim=None,
            visual_score=score,
            score_breakdown=breakdown,
        )

    try:
        video_info = probe_video_stream(path)
    except RuntimeError as exc:
        path.unlink(missing_ok=True)
        raise VisualSearchError(
            "O video selecionado e invalido ou nao possui stream de video utilizavel."
        ) from exc

    trim = assess_trim(
        video_info.duration,
        shot_duration_seconds=shot_duration_seconds,
        source_start_seconds=source_start_seconds,
        source_end_seconds=source_end_seconds,
        crossfade_seconds=crossfade_seconds,
        speed=speed,
        freeze_start_seconds=freeze_start_seconds,
        freeze_duration_seconds=freeze_duration_seconds,
        output_fps=output_fps,
    )
    warnings: list[str] = []
    motion: MotionAnalysis | None = None
    try:
        motion_end = source_end_seconds
        if (
            motion_end is None
            and trim.reason in {"safe", "insufficient_duration_no_loop"}
            and trim.required_seconds is not None
        ):
            motion_end = min(
                video_info.duration,
                source_start_seconds + trim.required_seconds,
            )
        motion = analyze_video_motion(
            path,
            video_info.duration,
            source_start_seconds=source_start_seconds,
            source_end_seconds=motion_end,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        warnings.append(
            f"Analise de movimento indisponivel ({type(exc).__name__}); "
            "o candidato continua valido para avaliacao manual."
        )

    score, breakdown = calculate_visual_score(
        "video",
        video_info.width,
        video_info.height,
        motion=motion,
        trim=trim,
        target_width=target_width,
        target_height=target_height,
    )
    return VisualInspection(
        result=result,
        path=path,
        width=video_info.width,
        height=video_info.height,
        aspect_ratio=round(video_info.width / video_info.height, 6),
        duration_seconds=video_info.duration,
        fps=video_info.fps,
        motion=motion,
        trim=trim,
        visual_score=score,
        score_breakdown=breakdown,
        warnings=tuple(warnings),
    )


def analyze_video_motion(
    path: Path,
    duration_seconds: float,
    *,
    source_start_seconds: float = 0.0,
    source_end_seconds: float | None = None,
) -> MotionAnalysis:
    """Estimate movement in the selected source interval using lightweight FFmpeg."""
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise RuntimeError("Duracao invalida para analise de movimento.")
    start_at = _finite_or_default(source_start_seconds, math.nan)
    end_at = (
        duration_seconds
        if source_end_seconds is None
        else _finite_or_default(source_end_seconds, math.nan)
    )
    if (
        not math.isfinite(start_at)
        or not math.isfinite(end_at)
        or start_at < 0
        or start_at >= duration_seconds
        or end_at <= start_at
        or end_at > duration_seconds + 1e-6
    ):
        raise RuntimeError("Intervalo invalido para analise de movimento.")
    end_at = min(end_at, duration_seconds)
    selected_duration = end_at - start_at
    window = min(MOTION_WINDOW_SECONDS, selected_duration)
    starts = _unique_numbers(
        (
            start_at,
            start_at + max(0.0, (selected_duration - window) / 2.0),
            start_at + max(0.0, selected_duration - window),
        )
    )
    samples_by_window: list[tuple[float, ...]] = []
    sampled_windows: list[tuple[float, float]] = []
    for start in starts:
        samples = _motion_samples(path, start, window)
        if samples:
            samples_by_window.append(samples)
            sampled_windows.append((round(start, 6), round(window, 6)))
    if not samples_by_window:
        raise RuntimeError("FFmpeg nao retornou amostras para analise de movimento.")

    opening_samples = samples_by_window[0]
    all_samples = tuple(
        sample for window_samples in samples_by_window for sample in window_samples
    )
    opening_score = _motion_value_to_score(_mean(opening_samples))
    motion_score = _motion_value_to_score(_mean(all_samples))
    static_ratio = sum(
        sample <= STATIC_SAMPLE_THRESHOLD for sample in all_samples
    ) / len(all_samples)
    return MotionAnalysis(
        opening_motion_score=round(opening_score, 2),
        motion_score=round(motion_score, 2),
        static_sample_ratio=round(static_ratio, 4),
        is_practically_static=static_ratio >= STATIC_FRAME_RATIO,
        sample_count=len(all_samples),
        sampled_windows=tuple(sampled_windows),
    )


def assess_trim(
    duration_seconds: float,
    *,
    shot_duration_seconds: float | None = None,
    source_start_seconds: float = 0.0,
    source_end_seconds: float | None = None,
    crossfade_seconds: float = 0.0,
    speed: float = DEFAULT_VIDEO_SPEED,
    freeze_start_seconds: float | None = None,
    freeze_duration_seconds: float | None = None,
    output_fps: int = 30,
) -> TrimAssessment:
    """Apply the renderer's no-loop trim policy without mutating episode files."""
    if not _is_finite_number(duration_seconds) or duration_seconds <= 0:
        raise RuntimeError("Duracao real invalida para validar trim.")
    start = _finite_or_default(source_start_seconds, math.nan)
    requested_end = (
        None
        if source_end_seconds is None
        else _finite_or_default(source_end_seconds, math.nan)
    )
    fade = _finite_or_default(crossfade_seconds, math.nan)
    playback_speed = _finite_or_default(speed, math.nan)
    has_freeze_start = freeze_start_seconds is not None
    has_freeze_duration = freeze_duration_seconds is not None
    tolerance = 1e-6

    if not MIN_VIDEO_SPEED <= playback_speed <= MAX_VIDEO_SPEED:
        return _unsafe_trim(
            start if math.isfinite(start) else 0.0,
            start if math.isfinite(start) else 0.0,
            shot_duration_seconds,
            "speed_invalid",
            crossfade_seconds=fade,
            speed=speed,
        )
    if not math.isfinite(start) or start < 0:
        return _unsafe_trim(
            0.0,
            0.0,
            shot_duration_seconds,
            "source_start_invalid",
            crossfade_seconds=fade,
            speed=playback_speed,
        )
    if not math.isfinite(fade) or fade < 0:
        return _unsafe_trim(
            start,
            start,
            shot_duration_seconds,
            "crossfade_invalid",
            crossfade_seconds=fade,
            speed=playback_speed,
        )
    if has_freeze_start != has_freeze_duration:
        return _unsafe_trim(
            start,
            start,
            shot_duration_seconds,
            "freeze_incomplete",
            crossfade_seconds=fade,
            speed=playback_speed,
        )
    if start >= duration_seconds - tolerance:
        return _unsafe_trim(
            start,
            duration_seconds,
            shot_duration_seconds,
            "start_at_or_after_end",
            crossfade_seconds=fade,
            speed=playback_speed,
        )
    if requested_end is not None and (
        not math.isfinite(requested_end) or requested_end <= start
    ):
        return _unsafe_trim(
            start,
            start,
            shot_duration_seconds,
            "source_end_invalid",
            crossfade_seconds=fade,
            speed=playback_speed,
        )
    if requested_end is not None and requested_end > duration_seconds + tolerance:
        return _unsafe_trim(
            start,
            requested_end,
            shot_duration_seconds,
            "source_end_after_media",
            crossfade_seconds=fade,
            speed=playback_speed,
        )

    effective_end = min(requested_end or duration_seconds, duration_seconds)
    available = max(0.0, effective_end - start)
    if shot_duration_seconds is None:
        if has_freeze_start:
            return _unsafe_trim(
                start,
                effective_end,
                shot_duration_seconds,
                "freeze_requires_shot_duration",
                crossfade_seconds=fade,
                speed=playback_speed,
            )
        return TrimAssessment(
            safe_for_shot=None,
            source_start_seconds=round(start, 6),
            source_end_seconds=round(effective_end, 6),
            available_seconds=round(available, 6),
            required_seconds=None,
            margin_seconds=None,
            reason="shot_duration_not_provided",
            speed=round(playback_speed, 6),
            output_fps=output_fps if _valid_output_fps(output_fps) else None,
        )
    shot_duration = _finite_or_default(shot_duration_seconds, math.nan)
    if not math.isfinite(shot_duration) or shot_duration <= 0:
        return _unsafe_trim(
            start,
            effective_end,
            shot_duration_seconds,
            "shot_duration_invalid",
            crossfade_seconds=fade,
            speed=playback_speed,
        )
    resolved_freeze = None
    if has_freeze_start:
        freeze_start = _finite_or_default(freeze_start_seconds, math.nan)
        freeze_duration = _finite_or_default(freeze_duration_seconds, math.nan)
        if (
            not _valid_output_fps(output_fps)
            or not math.isfinite(freeze_start)
            or freeze_start < 0
            or not math.isfinite(freeze_duration)
            or not MIN_FREEZE_DURATION_SECONDS
            <= freeze_duration
            <= MAX_FREEZE_DURATION_SECONDS
        ):
            return _unsafe_trim(
                start,
                effective_end,
                shot_duration_seconds,
                "freeze_invalid",
                crossfade_seconds=fade,
                speed=playback_speed,
            )
        shot_frames = max(1, math.ceil(shot_duration * output_fps - 1e-9))
        try:
            resolved_freeze = resolve_freeze_frame(
                FreezeFrameSpec(
                    start_seconds=freeze_start,
                    duration_seconds=freeze_duration,
                ),
                shot_frames,
                output_fps,
                "freeze_frame",
            )
        except RuntimeError:
            return _unsafe_trim(
                start,
                effective_end,
                shot_duration_seconds,
                "freeze_invalid",
                crossfade_seconds=fade,
                speed=playback_speed,
            )

    required_output = shot_duration + fade
    saved_output = (
        resolved_freeze.added_frames / output_fps
        if resolved_freeze is not None
        else 0.0
    )
    moving_output = required_output - saved_output
    required = moving_output * playback_speed
    margin = available - required
    safe = available + tolerance >= required
    return TrimAssessment(
        safe_for_shot=safe,
        source_start_seconds=round(start, 6),
        source_end_seconds=round(effective_end, 6),
        available_seconds=round(available, 6),
        required_seconds=round(required, 6),
        margin_seconds=round(margin, 6),
        reason="safe" if safe else "insufficient_duration_no_loop",
        speed=round(playback_speed, 6),
        required_output_seconds=round(required_output, 6),
        moving_output_seconds=round(moving_output, 6),
        output_fps=output_fps if _valid_output_fps(output_fps) else None,
        freeze_start_frame=(
            resolved_freeze.start_frame if resolved_freeze is not None else None
        ),
        freeze_duration_frames=(
            resolved_freeze.duration_frames if resolved_freeze is not None else None
        ),
    )


def calculate_visual_score(
    kind: Literal["image", "video"],
    width: int,
    height: int,
    *,
    motion: MotionAnalysis | None = None,
    trim: TrimAssessment | None = None,
    target_width: int = TARGET_WIDTH,
    target_height: int = TARGET_HEIGHT,
) -> tuple[float, dict[str, float]]:
    """Return an explainable technical score; semantic relevance stays with GPT."""
    if kind not in {"image", "video"} or min(width, height, target_width, target_height) <= 0:
        raise RuntimeError("Metadados invalidos para calcular visual_score.")
    cover_scale = max(target_width / width, target_height / height)
    resolution_score = 100.0 / max(1.0, cover_scale)
    source_aspect = width / height
    target_aspect = target_width / target_height
    aspect_score = 100.0 * min(
        source_aspect / target_aspect,
        target_aspect / source_aspect,
    )

    if kind == "image":
        score = 0.65 * resolution_score + 0.35 * aspect_score
        breakdown = {
            "resolution": round(resolution_score, 2),
            "aspect_ratio": round(aspect_score, 2),
        }
    else:
        motion_score = (
            50.0
            if motion is None
            else 0.60 * motion.motion_score + 0.40 * motion.opening_motion_score
        )
        if trim is None or trim.safe_for_shot is None:
            duration_score = 100.0
        elif trim.safe_for_shot:
            duration_score = 100.0
        elif (
            trim.reason == "insufficient_duration_no_loop"
            and trim.required_seconds
            and trim.required_seconds > 0
        ):
            duration_score = 100.0 * min(
                1.0,
                trim.available_seconds / trim.required_seconds,
            )
        else:
            duration_score = 0.0
        score = (
            0.35 * resolution_score
            + 0.20 * aspect_score
            + 0.30 * motion_score
            + 0.15 * duration_score
        )
        if motion is not None and motion.is_practically_static:
            score = min(score, 45.0)
        if trim is not None and trim.safe_for_shot is False:
            score = min(score, 40.0)
        breakdown = {
            "resolution": round(resolution_score, 2),
            "aspect_ratio": round(aspect_score, 2),
            "motion": round(motion.motion_score if motion else motion_score, 2),
            "opening_motion": round(
                motion.opening_motion_score if motion else motion_score, 2
            ),
            "trim": round(duration_score, 2),
        }
    return round(max(0.0, min(100.0, score)), 2), breakdown


def rank_visual_inspections(
    inspections: Sequence[VisualInspection],
) -> tuple[VisualInspection, ...]:
    """Rank inspected candidates by technical score with stable tie-breaking."""
    return tuple(
        sorted(
            inspections,
            key=lambda item: (
                -item.visual_score,
                item.result.name.casefold(),
                item.result.provider_id.casefold(),
            ),
        )
    )


def main(
    argv: Sequence[str] | None = None,
    default_project_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pesquisa e inspeciona imagens/videos para autoria; nunca e chamado pelo renderer."
        )
    )
    parser.add_argument(
        "queries",
        nargs="+",
        help="Uma ou mais consultas; coloque cada consulta com espacos entre aspas.",
    )
    parser.add_argument("--kind", choices=("image", "video", "any"), default="any")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument(
        "--external",
        action="store_true",
        help="Habilita as APIs publicas Wikimedia Commons e Openverse Images.",
    )
    parser.add_argument(
        "--inspect-top",
        type=int,
        default=0,
        metavar="N",
        help="Baixa e inspeciona tecnicamente os N primeiros candidatos aprovados.",
    )
    parser.add_argument("--shot-duration", type=float)
    parser.add_argument("--source-start", type=float, default=0.0)
    parser.add_argument("--source-end", type=float)
    parser.add_argument("--crossfade", type=float, default=0.0)
    parser.add_argument("--speed", type=float, default=DEFAULT_VIDEO_SPEED)
    parser.add_argument("--freeze-start", type=float)
    parser.add_argument("--freeze-duration", type=float)
    parser.add_argument("--output-fps", type=int, default=30)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root or Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    report = search_visual(
        args.project_root,
        args.queries,
        args.kind,
        include_external=args.external,
        limit=args.limit,
    )
    output = report.as_dict()
    warnings = list(output["warnings"])
    inspected_candidates: list[VisualInspection] = []
    if args.inspect_top < 0:
        warnings.append("--inspect-top precisa ser zero ou positivo.")
    elif args.inspect_top and not args.external:
        warnings.append("--inspect-top requer --external; nenhuma midia foi baixada.")
    elif args.inspect_top:
        for result in report.results:
            if len(inspected_candidates) >= args.inspect_top:
                break
            if result.candidate_asset_entry is None:
                continue
            try:
                inspection = inspect_visual_result(
                    args.project_root,
                    result,
                    shot_duration_seconds=args.shot_duration,
                    source_start_seconds=args.source_start,
                    source_end_seconds=args.source_end,
                    crossfade_seconds=args.crossfade,
                    speed=args.speed,
                    freeze_start_seconds=args.freeze_start,
                    freeze_duration_seconds=args.freeze_duration,
                    output_fps=args.output_fps,
                )
                inspected_candidates.append(inspection)
            except VisualSearchError as exc:
                warnings.append(str(exc))
    output["warnings"] = _dedupe_strings(warnings)
    ranked_inspections: list[dict[str, object]] = []
    for technical_rank, inspection in enumerate(
        rank_visual_inspections(inspected_candidates), start=1
    ):
        serialized = inspection.as_dict(args.project_root)
        serialized["technical_rank"] = technical_rank
        ranked_inspections.append(serialized)
    output["inspections"] = ranked_inspections
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _request_json(url: str, params: dict[str, object], provider_name: str) -> object:
    response = None
    try:
        response = requests.get(
            url,
            params=params,
            headers=VISUAL_SEARCH_HEADERS,
            timeout=(5, 25),
        )
        status = int(response.status_code)
        if status >= 400:
            raise VisualSearchError(
                f"Busca {provider_name} indisponivel (HTTP {status}); continuando."
            )
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise VisualSearchError(
                f"Busca {provider_name} retornou dados invalidos; continuando."
            ) from exc
    except VisualSearchError:
        raise
    except requests.RequestException as exc:
        raise VisualSearchError(
            f"Busca {provider_name} indisponivel ({type(exc).__name__}); continuando."
        ) from exc
    finally:
        if response is not None:
            response.close()


def _parse_openverse_image(
    raw: dict[object, object],
    rank: int,
) -> VisualSearchResult | None:
    provider_id = _clean_text(raw.get("id"), 100)
    name = _clean_text(raw.get("title"), 200)
    source = _clean_text(raw.get("source") or raw.get("provider"), 60).casefold()
    source_page = _safe_http_url(
        raw.get("foreign_landing_url") or raw.get("detail_url"), require_https=True
    )
    if not provider_id or not name or not source or not source_page:
        return None
    media_url = _safe_http_url(raw.get("url"), require_https=True)
    thumbnail_url = _safe_http_url(raw.get("thumbnail"), require_https=True)
    creator = _clean_text(raw.get("creator"), 160)
    license_code = _clean_text(raw.get("license"), 80)
    license_url = _safe_http_url(raw.get("license_url"), require_https=True)
    attribution = _clean_text(raw.get("attribution"), 500) or _clean_text(
        f"{name} - {creator or 'autor nao informado'} - "
        f"{license_code or 'licenca nao informada'}",
        500,
    )
    width = _positive_int(raw.get("width"))
    height = _positive_int(raw.get("height"))
    suffix = _url_suffix(media_url)
    mime_type = _clean_text(raw.get("mime_type"), 100) or None
    download_url, hosts, note = _approved_openverse_image(
        media_url,
        source,
        license_code,
        license_url,
    )
    return VisualSearchResult(
        provider_id=provider_id,
        name=name,
        kind="image",
        source=source,
        source_page_url=source_page,
        creator=creator,
        license=license_code,
        license_url=license_url,
        attribution=attribution,
        search_provider="openverse_images",
        description=_clean_text(raw.get("description"), 600),
        width=width,
        height=height,
        file_size_bytes=_positive_int(raw.get("filesize")),
        file_format=suffix.lstrip(".") if suffix in SUPPORTED_IMAGE_SUFFIXES else None,
        mime_type=mime_type,
        thumbnail_url=thumbnail_url or None,
        tags=_openverse_tags(raw.get("tags")),
        provider_rank=rank,
        download_url=download_url,
        allowed_download_hosts=hosts,
        download_note=note,
    )


def _wikimedia_pages(payload: object) -> list[dict[object, object]]:
    if not isinstance(payload, dict):
        raise VisualSearchError("Busca Wikimedia Commons retornou estrutura invalida.")
    query = payload.get("query")
    if query is None:
        return []
    if not isinstance(query, dict):
        raise VisualSearchError("Busca Wikimedia Commons retornou estrutura invalida.")
    pages = query.get("pages", [])
    if isinstance(pages, dict):
        pages = list(pages.values())
    if not isinstance(pages, list):
        raise VisualSearchError("Busca Wikimedia Commons retornou estrutura invalida.")
    return [page for page in pages if isinstance(page, dict)]


def _parse_wikimedia_result(
    raw: dict[object, object],
    requested_kind: Literal["image", "video"],
    rank: int,
) -> VisualSearchResult | None:
    image_info = raw.get("imageinfo")
    if not isinstance(image_info, list) or not image_info or not isinstance(image_info[0], dict):
        return None
    info = image_info[0]
    media_url = _safe_http_url(info.get("url"), require_https=True)
    source_page = _safe_http_url(info.get("descriptionurl"), require_https=True)
    suffix = _url_suffix(media_url)
    mime_type = _clean_text(info.get("mime"), 100).casefold()
    media_type = _clean_text(info.get("mediatype"), 40).casefold()
    inferred_kind: Literal["image", "video"] | None = None
    if suffix in VIDEO_ASSET_EXTENSIONS or mime_type.startswith("video/") or media_type == "video":
        inferred_kind = "video"
    elif suffix in SUPPORTED_IMAGE_SUFFIXES or mime_type.startswith("image/") or media_type in {"bitmap", "drawing"}:
        inferred_kind = "image"
    if inferred_kind != requested_kind or suffix not in SUPPORTED_VISUAL_SUFFIXES:
        return None

    provider_id = _clean_text(raw.get("pageid"), 100)
    title = _clean_text(raw.get("title"), 220)
    name = re.sub(r"^File:\s*", "", title, flags=re.IGNORECASE)
    if not provider_id or not name or not media_url or not source_page:
        return None
    metadata = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}
    creator = _metadata_text(metadata, "Artist", 160) or _metadata_text(
        metadata, "Credit", 160
    )
    license_name = _metadata_text(metadata, "LicenseShortName", 100) or _metadata_text(
        metadata, "UsageTerms", 100
    )
    license_url = _safe_http_url(
        _metadata_raw(metadata, "LicenseUrl"), require_https=True
    )
    attribution = _metadata_text(metadata, "Credit", 500) or _clean_text(
        f"{name} - {creator or 'autor nao informado'} - Wikimedia Commons",
        500,
    )
    thumbnail_url = _safe_http_url(info.get("thumburl"), require_https=True)
    host = (urlparse(media_url).hostname or "").casefold()
    license_allowed = _license_allows_direct_use(license_name)
    downloadable = host == "upload.wikimedia.org" and license_allowed
    note = None
    if not license_allowed:
        note = "licenca ausente ou exige revisao antes do download"
    elif host != "upload.wikimedia.org":
        note = "host de download nao aprovado"
    return VisualSearchResult(
        provider_id=provider_id,
        name=name,
        kind=inferred_kind,
        source="wikimedia_commons",
        source_page_url=source_page,
        creator=creator,
        license=license_name,
        license_url=license_url,
        attribution=attribution,
        search_provider="wikimedia_commons",
        description=_metadata_text(metadata, "ImageDescription", 600),
        width=_positive_int(info.get("width")),
        height=_positive_int(info.get("height")),
        file_size_bytes=_positive_int(info.get("size")),
        duration_seconds=_positive_float(info.get("duration")),
        file_format=suffix.lstrip("."),
        mime_type=mime_type or None,
        thumbnail_url=thumbnail_url or None,
        provider_rank=rank,
        download_url=media_url if downloadable else None,
        allowed_download_hosts=(host,) if downloadable else (),
        download_note=note,
    )


def _approved_openverse_image(
    media_url: str,
    source: str,
    license_name: str,
    license_url: str,
) -> tuple[str | None, tuple[str, ...], str | None]:
    suffix = _url_suffix(media_url)
    host = (urlparse(media_url).hostname or "").casefold()
    allowed_hosts = OPENVERSE_IMAGE_HOSTS.get(source, frozenset())
    if not _license_allows_direct_use(license_name) or not license_url:
        return None, (), "licenca exige revisao manual"
    if not media_url or suffix not in SUPPORTED_IMAGE_SUFFIXES:
        return None, (), "URL nao e uma imagem direta suportada"
    if host not in allowed_hosts:
        return None, (), "host de download nao aprovado"
    return media_url, (host,), None


def _motion_samples(path: Path, start: float, duration: float) -> tuple[float, ...]:
    output = run_ffmpeg_capture(
        [
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{duration:.6f}",
            "-i",
            path,
            "-an",
            "-vf",
            (
                f"fps={MOTION_SAMPLE_FPS},scale=160:-2:flags=fast_bilinear,"
                "format=gray,tblend=all_mode=difference,signalstats,"
                "metadata=print:key=lavfi.signalstats.YAVG"
            ),
            "-f",
            "null",
            "-",
        ]
    )
    samples: list[float] = []
    for raw in re.findall(r"lavfi\.signalstats\.YAVG\s*=\s*([0-9]+(?:\.[0-9]+)?)", output):
        value = float(raw)
        if math.isfinite(value) and value >= 0:
            samples.append(value)
    return tuple(samples)


def _probe_image(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as opened:
            width, height = opened.size
            opened.verify()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise RuntimeError("Arquivo de imagem invalido.") from exc
    if width <= 0 or height <= 0:
        raise RuntimeError("Resolucao de imagem invalida.")
    return width, height


def _unsafe_trim(
    start: float,
    end: float,
    shot_duration: float | None,
    reason: str,
    *,
    crossfade_seconds: float = 0.0,
    speed: float = DEFAULT_VIDEO_SPEED,
) -> TrimAssessment:
    shot = (
        float(shot_duration)
        if _is_finite_number(shot_duration) and float(shot_duration) > 0
        else None
    )
    fade = _finite_or_default(crossfade_seconds, math.nan)
    playback_speed = _finite_or_default(speed, math.nan)
    valid_speed = MIN_VIDEO_SPEED <= playback_speed <= MAX_VIDEO_SPEED
    required_output = (
        shot + fade
        if shot is not None and math.isfinite(fade) and fade >= 0
        else None
    )
    required = (
        required_output * playback_speed
        if required_output is not None and valid_speed
        else None
    )
    return TrimAssessment(
        safe_for_shot=False,
        source_start_seconds=round(max(0.0, start), 6),
        source_end_seconds=round(max(0.0, end), 6),
        available_seconds=round(max(0.0, end - start), 6),
        required_seconds=round(required, 6) if required is not None else None,
        margin_seconds=None,
        reason=reason,
        speed=round(playback_speed, 6) if valid_speed else None,
        required_output_seconds=(
            round(required_output, 6) if required_output is not None else None
        ),
    )


def _dedupe_key(result: VisualSearchResult) -> str:
    candidate = result.download_url or result.source_page_url
    parsed = urlsplit(candidate)
    normalized_url = urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, "", "")
    )
    if normalized_url:
        return normalized_url.casefold()
    return f"{result.source}:{result.provider_id}".casefold()


def _round_robin_results(
    results: Sequence[VisualSearchResult],
    query_buckets: Sequence[Sequence[int]],
) -> tuple[VisualSearchResult, ...]:
    """Keep multi-query output balanced while preserving each provider's order."""
    if not query_buckets:
        return tuple(results)
    positions = [0] * len(query_buckets)
    ordered: list[VisualSearchResult] = []
    emitted: set[int] = set()
    while True:
        progressed = False
        for bucket_index, bucket in enumerate(query_buckets):
            while (
                positions[bucket_index] < len(bucket)
                and bucket[positions[bucket_index]] in emitted
            ):
                positions[bucket_index] += 1
            if positions[bucket_index] >= len(bucket):
                continue
            result_index = bucket[positions[bucket_index]]
            positions[bucket_index] += 1
            if 0 <= result_index < len(results):
                emitted.add(result_index)
                ordered.append(results[result_index])
                progressed = True
        if not progressed:
            break
    ordered.extend(result for index, result in enumerate(results) if index not in emitted)
    return tuple(ordered)


def _normalize_queries(queries: str | Sequence[str]) -> tuple[str, ...]:
    raw_queries = (queries,) if isinstance(queries, str) else tuple(queries)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_queries:
        value = _clean_text(raw, 200)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            normalized.append(value)
    return tuple(normalized)


def _metadata_raw(metadata: dict[object, object], key: str) -> object:
    raw = metadata.get(key)
    if isinstance(raw, dict):
        return raw.get("value", "")
    return raw


def _metadata_text(metadata: dict[object, object], key: str, limit: int) -> str:
    return _clean_text(_strip_html(_metadata_raw(metadata, key)), limit)


def _strip_html(value: object) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"<[^>]*>", " ", text)


def _license_allows_direct_use(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    tokens = set(normalized.split())
    if "nc" in tokens or "nd" in tokens:
        return False
    if "public domain" in normalized or normalized in {"pdm", "cc0"}:
        return True
    return normalized.startswith("cc by") or normalized in {"by", "by sa"}


def _openverse_tags(raw_tags: object) -> tuple[str, ...]:
    if not isinstance(raw_tags, list):
        return ()
    tags: list[str] = []
    for raw in raw_tags:
        value = raw.get("name") if isinstance(raw, dict) else raw
        tag = _clean_text(value, 60)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 12:
            break
    return tuple(tags)


def _safe_http_url(raw_value: object, *, require_https: bool = False) -> str:
    value = str(raw_value or "").strip()
    parsed = urlparse(value)
    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if (
        parsed.scheme.casefold() not in allowed_schemes
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return value


def _url_suffix(value: str) -> str:
    return Path(unquote(urlparse(value).path)).suffix.casefold()


def _clean_text(raw_value: object, max_length: int) -> str:
    value = re.sub(r"\s+", " ", str(raw_value or "")).strip()
    return value[:max_length]


def _safe_path_component(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-_")
    return normalized or fallback


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _finite_or_default(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _is_finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _valid_output_fps(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _motion_value_to_score(value: float) -> float:
    return max(0.0, min(100.0, value * 5.0))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _unique_numbers(values: Sequence[float]) -> tuple[float, ...]:
    unique: list[float] = []
    seen: set[float] = set()
    for value in values:
        rounded = round(value, 6)
        if rounded not in seen:
            seen.add(rounded)
            unique.append(rounded)
    return tuple(unique)


def _dedupe_strings(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)
