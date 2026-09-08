from __future__ import annotations

import argparse
from collections.abc import Sequence
import ipaddress
import json
import math
from pathlib import Path
import re
from typing import Literal
from urllib.parse import unquote, urlparse

import requests

from .ffmpeg import probe_video_stream
from .media_cache import media_cache_directory
from .models import VIDEO_ASSET_EXTENSIONS
from .visual_repetition import VisualHistoryEntry, load_visual_history
from .visual_search import (
    MAX_EXTERNAL_VIDEO_BYTES,
    OpenverseImageProvider,
    VisualInspection,
    VisualKind,
    VisualSearchError,
    VisualSearchResult,
    WikimediaCommonsProvider,
    analyze_video_motion,
    apply_visual_repetition,
    assess_trim,
    calculate_visual_score,
    inspect_visual_result,
    search_visual,
)


RightsStatus = Literal["verified", "unknown", "restricted"]

WEB_HEADERS = {
    "User-Agent": (
        "MusicShortFactory/10.0 "
        "(+https://github.com/aleseixas/music-short-factory; web-visual-discovery)"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.5",
}
DUCKDUCKGO_HOME = "https://duckduckgo.com/"
DUCKDUCKGO_IMAGES = "https://duckduckgo.com/i.js"
WEB_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
RIGHTS_RESTRICTED_PENALTY = -12.0
MAX_WEB_IMAGE_BYTES = 50 * 1024 * 1024


def infer_rights_status(
    *,
    license_name: str = "",
    license_url: str = "",
    source: str = "",
) -> RightsStatus:
    """Classify only what the metadata explicitly says; absence stays unknown."""
    raw = " ".join((license_name, license_url, source)).casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    tokens = set(normalized.split())

    restricted_markers = (
        "all rights reserved",
        "standard youtube license",
        "youtube standard license",
        "standard license",
        "no derivatives",
        "noncommercial",
    )
    if any(marker in normalized for marker in restricted_markers):
        return "restricted"
    if "nd" in tokens or "nc" in tokens:
        return "restricted"

    verified_markers = (
        "public domain",
        "creativecommons org/publicdomain",
        "creative commons attribution",
        "cc by",
        "cc0",
    )
    if any(marker in raw for marker in verified_markers):
        return "verified"
    if normalized in {"pdm", "by", "by sa"}:
        return "verified"
    return "unknown"


def rights_rank_adjustment(status: RightsStatus) -> float:
    """Rights metadata is never a gate. Restricted only loses ranking points."""
    if status == "restricted":
        return RIGHTS_RESTRICTED_PENALTY
    return 0.0


def selection_score(visual_score: float, status: RightsStatus) -> float:
    return round(
        max(0.0, min(100.0, float(visual_score) + rights_rank_adjustment(status))),
        2,
    )


def candidate_rights_status(result: VisualSearchResult) -> RightsStatus:
    return infer_rights_status(
        license_name=result.license,
        license_url=result.license_url,
        source=result.source,
    )


def serialize_candidate(result: VisualSearchResult) -> dict[str, object]:
    payload = result.as_dict()
    status = candidate_rights_status(result)
    payload["rights_status"] = status
    payload["rights_rank_adjustment"] = rights_rank_adjustment(status)
    payload["rights_blocks_selection"] = False
    payload["selection_eligible"] = True
    payload["rights_note"] = (
        "Rights metadata is informational for ranking. Unknown does not block; "
        "restricted is penalized but remains eligible."
    )
    return payload


class DuckDuckGoWebImageProvider:
    """Discover general-web images. License metadata is unknown unless supplied elsewhere."""

    name = "web-images"

    def search(
        self,
        query: str,
        kind: VisualKind = "image",
        limit: int = 8,
    ) -> tuple[VisualSearchResult, ...]:
        if kind == "video":
            return ()
        vqd = _duckduckgo_vqd(query)
        payload = _request_web_json(
            DUCKDUCKGO_IMAGES,
            params={
                "l": "us-en",
                "o": "json",
                "q": query,
                "vqd": vqd,
                "f": ",,,",
                "p": "1",
            },
            referer=f"https://duckduckgo.com/?q={requests.utils.quote(query)}&iax=images&ia=images",
        )
        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            raise VisualSearchError("Busca web de imagens retornou estrutura invalida.")

        results: list[VisualSearchResult] = []
        for rank, raw in enumerate(raw_results, start=1):
            if not isinstance(raw, dict):
                continue
            parsed = _parse_web_image(raw, rank)
            if parsed is not None:
                results.append(parsed)
            if len(results) >= limit:
                break
        return tuple(results)


class YouTubeWebVideoProvider:
    """Discover public web video pages through yt-dlp search without downloading them yet."""

    name = "web-videos"

    def search(
        self,
        query: str,
        kind: VisualKind = "video",
        limit: int = 8,
    ) -> tuple[VisualSearchResult, ...]:
        if kind == "image":
            return ()
        YoutubeDL, DownloadError = _yt_dlp_api()
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "ignoreerrors": True,
            "extract_flat": "in_playlist",
            "playlistend": limit,
            "noplaylist": True,
        }
        try:
            with YoutubeDL(options) as ydl:
                payload = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        except DownloadError as exc:
            raise VisualSearchError("Busca web de videos indisponivel; continuando.") from exc
        except Exception as exc:
            raise VisualSearchError(
                f"Busca web de videos indisponivel ({type(exc).__name__}); continuando."
            ) from exc

        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return ()
        results: list[VisualSearchResult] = []
        for rank, raw in enumerate(entries, start=1):
            if not isinstance(raw, dict):
                continue
            parsed = _parse_youtube_result(raw, rank)
            if parsed is not None:
                results.append(parsed)
            if len(results) >= limit:
                break
        return tuple(results)


def inspect_candidate(
    project_root: Path,
    result: VisualSearchResult,
    *,
    shot_duration_seconds: float | None = None,
    source_start_seconds: float = 0.0,
    source_end_seconds: float | None = None,
    crossfade_seconds: float = 0.0,
    speed: float = 1.0,
    freeze_start_seconds: float | None = None,
    freeze_duration_seconds: float | None = None,
    output_fps: int = 30,
    repetition_history: Sequence[VisualHistoryEntry] | None = None,
    exclude_episode: str | None = None,
) -> VisualInspection:
    """Inspect direct-media candidates or web video pages; one failure never aborts the pool."""
    if result.search_provider != "youtube_web":
        return inspect_visual_result(
            project_root,
            result,
            shot_duration_seconds=shot_duration_seconds,
            source_start_seconds=source_start_seconds,
            source_end_seconds=source_end_seconds,
            crossfade_seconds=crossfade_seconds,
            speed=speed,
            freeze_start_seconds=freeze_start_seconds,
            freeze_duration_seconds=freeze_duration_seconds,
            output_fps=output_fps,
            repetition_history=repetition_history,
            exclude_episode=exclude_episode,
        )

    path = _download_web_video(project_root, result)
    try:
        info = probe_video_stream(path)
    except RuntimeError as exc:
        path.unlink(missing_ok=True)
        raise VisualSearchError("Video web baixado e invalido; continuando.") from exc

    trim = assess_trim(
        info.duration,
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
    motion = None
    try:
        motion_end = source_end_seconds
        if motion_end is None and _finite_number(shot_duration_seconds):
            requested = float(shot_duration_seconds) + max(
                0.0,
                float(crossfade_seconds) if _finite_number(crossfade_seconds) else 0.0,
            )
            motion_end = min(info.duration, source_start_seconds + requested)
        motion = analyze_video_motion(
            path,
            info.duration,
            source_start_seconds=source_start_seconds,
            source_end_seconds=motion_end,
        )
    except (RuntimeError, TypeError, ValueError) as exc:
        warnings.append(
            f"Analise de movimento web indisponivel ({type(exc).__name__}); "
            "o candidato continua valido."
        )

    score, breakdown = calculate_visual_score(
        "video",
        info.width,
        info.height,
        motion=motion,
        trim=trim,
    )
    inspection = VisualInspection(
        result=result,
        path=path,
        width=info.width,
        height=info.height,
        aspect_ratio=round(info.width / info.height, 6),
        duration_seconds=info.duration,
        fps=info.fps,
        motion=motion,
        trim=trim,
        visual_score=score,
        score_breakdown=breakdown,
        warnings=tuple(warnings),
    )
    return apply_visual_repetition(
        project_root, inspection, repetition_history, exclude_episode=exclude_episode
    )


def rank_inspections_for_selection(
    inspections: Sequence[VisualInspection],
) -> tuple[VisualInspection, ...]:
    """Rank by final selection score while keeping image and video competition separate."""
    return tuple(
        sorted(
            inspections,
            key=lambda item: (
                item.result.kind,
                -selection_score(item.selection_score, candidate_rights_status(item.result)),
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
            "Pesquisa web-first e inspeciona imagens/videos para autoria; "
            "direitos desconhecidos nao bloqueiam candidatos."
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
        help=(
            "Habilita descoberta web-first (web videos + web images) e fallbacks "
            "Wikimedia Commons/Openverse."
        ),
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Com --external, usa apenas Wikimedia Commons/Openverse.",
    )
    parser.add_argument(
        "--inspect-top",
        type=int,
        default=0,
        metavar="N",
        help="Baixa e inspeciona os N primeiros candidatos tecnicamente acessiveis.",
    )
    parser.add_argument("--shot-duration", type=float)
    parser.add_argument("--source-start", type=float, default=0.0)
    parser.add_argument("--source-end", type=float)
    parser.add_argument("--crossfade", type=float, default=0.0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--freeze-start", type=float)
    parser.add_argument("--freeze-duration", type=float)
    parser.add_argument("--output-fps", type=int, default=30)
    parser.add_argument("--episode", help="Exclui o proprio episodio do historico visual.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root or Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    providers = [WikimediaCommonsProvider(), OpenverseImageProvider()]
    if not args.no_web:
        providers = [YouTubeWebVideoProvider(), DuckDuckGoWebImageProvider(), *providers]

    report = search_visual(
        args.project_root,
        args.queries,
        args.kind,
        include_external=args.external,
        limit=args.limit,
        providers=tuple(providers),
    )
    output = report.as_dict()
    output["results"] = [serialize_candidate(result) for result in report.results]
    output["ranking_policy"] = {
        "rights_are_gate": False,
        "unknown_adjustment": 0.0,
        "verified_adjustment": 0.0,
        "restricted_adjustment": RIGHTS_RESTRICTED_PENALTY,
        "competition": "image_with_image_and_video_with_video",
    }

    warnings = list(output["warnings"])
    inspections: list[VisualInspection] = []
    if args.inspect_top < 0:
        warnings.append("--inspect-top precisa ser zero ou positivo.")
    elif args.inspect_top and not args.external:
        warnings.append("--inspect-top requer --external; nenhuma midia foi baixada.")
    elif args.inspect_top:
        try:
            history, history_warnings = load_visual_history(
                args.project_root, exclude_episode=args.episode
            )
            warnings.extend(history_warnings)
        except Exception:
            history = ()
            warnings.append("Historico visual indisponivel; ranking tecnico mantido.")
        for result in report.results:
            if len(inspections) >= args.inspect_top:
                break
            if result.candidate_asset_entry is None and result.search_provider != "youtube_web":
                continue
            try:
                inspections.append(
                    inspect_candidate(
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
                        exclude_episode=args.episode,
                        repetition_history=history,
                    )
                )
            except VisualSearchError as exc:
                warnings.append(str(exc))

    ranked = rank_inspections_for_selection(inspections)
    kind_counts = {"image": 0, "video": 0}
    serialized_inspections: list[dict[str, object]] = []
    for overall_rank, inspection in enumerate(ranked, start=1):
        kind_counts[inspection.result.kind] += 1
        status = candidate_rights_status(inspection.result)
        payload = inspection.as_dict(args.project_root)
        payload["selection_rank"] = overall_rank
        payload["kind_rank"] = kind_counts[inspection.result.kind]
        payload["rights_status"] = status
        payload["rights_rank_adjustment"] = rights_rank_adjustment(status)
        payload["selection_score"] = selection_score(inspection.selection_score, status)
        payload["rights_blocks_selection"] = False
        serialized_inspections.append(payload)

    output["warnings"] = _dedupe_strings(warnings)
    output["inspections"] = serialized_inspections
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def _duckduckgo_vqd(query: str) -> str:
    response = None
    try:
        response = requests.get(
            DUCKDUCKGO_HOME,
            params={"q": query},
            headers=WEB_HEADERS,
            timeout=(5, 20),
        )
        if int(response.status_code) >= 400:
            raise VisualSearchError("Busca web de imagens indisponivel; continuando.")
        text = str(getattr(response, "text", ""))
        patterns = (
            r"vqd=['\"]([^'\"]+)['\"]",
            r"vqd=([0-9-]+)&",
            r"vqd\s*:\s*['\"]([^'\"]+)['\"]",
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        raise VisualSearchError("Busca web de imagens nao retornou token de consulta.")
    except requests.RequestException as exc:
        raise VisualSearchError("Busca web de imagens indisponivel; continuando.") from exc
    finally:
        if response is not None:
            response.close()


def _request_web_json(
    url: str,
    *,
    params: dict[str, object],
    referer: str,
) -> object:
    response = None
    headers = dict(WEB_HEADERS)
    headers["Referer"] = referer
    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=(5, 25),
        )
        if int(response.status_code) >= 400:
            raise VisualSearchError("Busca web de imagens indisponivel; continuando.")
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise VisualSearchError("Busca web de imagens retornou dados invalidos.") from exc
    except requests.RequestException as exc:
        raise VisualSearchError("Busca web de imagens indisponivel; continuando.") from exc
    finally:
        if response is not None:
            response.close()


def _parse_web_image(raw: dict[object, object], rank: int) -> VisualSearchResult | None:
    image_url = _safe_public_https_url(raw.get("image"))
    source_page = _safe_public_https_url(raw.get("url"))
    thumbnail = _safe_public_https_url(raw.get("thumbnail"))
    title = _clean_text(raw.get("title"), 220)
    if not image_url or not source_page or not title:
        return None
    suffix = Path(unquote(urlparse(image_url).path)).suffix.casefold()
    if suffix not in WEB_IMAGE_SUFFIXES:
        return None
    host = (urlparse(image_url).hostname or "").casefold()
    provider_id = _clean_text(raw.get("id"), 100) or _stable_web_id(image_url)
    source = _clean_text(raw.get("source"), 80) or host
    return VisualSearchResult(
        provider_id=provider_id,
        name=title,
        kind="image",
        source=source,
        source_page_url=source_page,
        creator="",
        license="",
        license_url="",
        attribution=f"{title} - fonte web: {source}",
        search_provider="duckduckgo_web_images",
        description="",
        width=_positive_int(raw.get("width")),
        height=_positive_int(raw.get("height")),
        file_size_bytes=None,
        file_format=suffix.lstrip("."),
        mime_type=None,
        thumbnail_url=thumbnail or None,
        provider_rank=rank,
        download_url=image_url,
        allowed_download_hosts=(host,),
        download_note="rights metadata unknown; candidate remains eligible",
    )


def _parse_youtube_result(raw: dict[object, object], rank: int) -> VisualSearchResult | None:
    provider_id = _clean_text(raw.get("id"), 100)
    title = _clean_text(raw.get("title"), 220)
    webpage = _safe_public_https_url(
        raw.get("webpage_url")
        or raw.get("original_url")
        or (
            f"https://www.youtube.com/watch?v={provider_id}"
            if provider_id
            else ""
        )
    )
    if not provider_id or not title or not webpage:
        return None
    creator = _clean_text(raw.get("uploader") or raw.get("channel"), 160)
    license_name = _clean_text(raw.get("license"), 120)
    thumbnail = _safe_public_https_url(raw.get("thumbnail"))
    if not thumbnail:
        thumbnails = raw.get("thumbnails")
        if isinstance(thumbnails, list):
            for item in reversed(thumbnails):
                if isinstance(item, dict):
                    thumbnail = _safe_public_https_url(item.get("url"))
                    if thumbnail:
                        break
    return VisualSearchResult(
        provider_id=provider_id,
        name=title,
        kind="video",
        source="youtube",
        source_page_url=webpage,
        creator=creator,
        license=license_name,
        license_url="",
        attribution=f"{title} - {creator or 'canal nao informado'} - YouTube",
        search_provider="youtube_web",
        description=_clean_text(raw.get("description"), 600),
        width=_positive_int(raw.get("width")),
        height=_positive_int(raw.get("height")),
        file_size_bytes=None,
        duration_seconds=_positive_float(raw.get("duration")),
        file_format=None,
        mime_type=None,
        thumbnail_url=thumbnail or None,
        provider_rank=rank,
        download_url=None,
        allowed_download_hosts=(),
        download_note="web video page; yt-dlp resolves only when inspected",
    )


def _download_web_video(project_root: Path, result: VisualSearchResult) -> Path:
    YoutubeDL, DownloadError = _yt_dlp_api()
    cache_dir = media_cache_directory(project_root.resolve(), None, "video")
    cache_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_component(f"web-{result.provider_id}-{Path(result.name).stem}")[:80]
    for suffix in VIDEO_ASSET_EXTENSIONS:
        cached = cache_dir / f"{stem}{suffix}"
        if cached.is_file() and 0 < cached.stat().st_size <= MAX_EXTERNAL_VIDEO_BYTES:
            return cached

    outtmpl = str(cache_dir / f"{stem}.%(ext)s")
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": outtmpl,
        "overwrites": False,
        "max_filesize": MAX_EXTERNAL_VIDEO_BYTES,
        "format": (
            "bestvideo[height<=720][ext=mp4]/best[height<=720][ext=mp4]/"
            "bestvideo[height<=720]/best[height<=720]"
        ),
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(result.source_page_url, download=True)
            prepared = Path(ydl.prepare_filename(info)) if isinstance(info, dict) else None
    except DownloadError as exc:
        raise VisualSearchError("Falha ao obter video web; continuando com o pool.") from exc
    except Exception as exc:
        raise VisualSearchError(
            f"Falha ao obter video web ({type(exc).__name__}); continuando com o pool."
        ) from exc

    candidates: list[Path] = []
    if prepared is not None:
        candidates.append(prepared)
    candidates.extend(sorted(cache_dir.glob(f"{stem}.*")))
    for candidate in candidates:
        if (
            candidate.is_file()
            and candidate.suffix.casefold() in VIDEO_ASSET_EXTENSIONS
            and 0 < candidate.stat().st_size <= MAX_EXTERNAL_VIDEO_BYTES
        ):
            return candidate
    raise VisualSearchError("Video web nao gerou arquivo suportado; continuando com o pool.")


def _yt_dlp_api():
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise VisualSearchError(
            "Provider web de video indisponivel: instale as dependencias da main."
        ) from exc
    return YoutubeDL, DownloadError


def _safe_public_https_url(raw: object) -> str:
    value = str(raw or "").strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or not host
        or host in {"localhost", "localhost.localdomain"}
        or host.endswith(".local")
    ):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return ""
    return value


def _stable_web_id(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _clean_text(raw: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(raw or "")).strip()[:limit]


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-_")
    return cleaned or "web-visual"


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


def _finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
