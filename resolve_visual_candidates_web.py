from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlparse

import resolve_visual_candidates as legacy
import engine.visual_search_web as web_engine

from engine.models import VIDEO_ASSET_EXTENSIONS
from engine.visual_candidates import normalize_visual_candidate
from engine.visual_vibe_scoring import (
    SEMANTIC_FIT_RANK, apply_vibe_adjustment, semantic_selection_score,
)
from engine.visual_search import VisualInspection, VisualSearchResult
from engine.visual_search_web import (
    candidate_rights_status,
    inspect_candidate as inspect_web_candidate,
    rights_rank_adjustment,
    selection_score,
)


PROJECT_ROOT = Path(__file__).resolve().parent
CURRENT_EPISODE = ""
WEB_DOWNLOADED_PATHS: dict[str, Path] = {}
SELECTED_VIDEO_SEGMENTS: dict[str, list[tuple[float, float]]] = {}
INSPECTED_VIDEO_SEGMENTS: dict[tuple[str, str], tuple[str, float, float]] = {}
MAX_VIDEO_USES_PER_SOURCE = 3
AUTO_REUSE_GAP_SECONDS = 2.0
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
KNOWN_REDIRECT_HOSTS = {
    "commons.wikimedia.org": ("commons.wikimedia.org", "upload.wikimedia.org"),
}
YOUTUBE_PO_PROVIDER_IMAGE = "brainicism/bgutil-ytdlp-pot-provider:1.3.2"
YOUTUBE_PO_PROVIDER_CONTAINER = "music-short-factory-bgutil"
YOUTUBE_PO_PROVIDER_HOST = "127.0.0.1"
YOUTUBE_PO_PROVIDER_PORT = 4416
YOUTUBE_PO_PROVIDER_URL = f"http://{YOUTUBE_PO_PROVIDER_HOST}:{YOUTUBE_PO_PROVIDER_PORT}"
_LEGACY_CANDIDATE_SOURCE_KEY = legacy._candidate_source_key


@dataclass(frozen=True)
class RankedInspection:
    base: VisualInspection
    technical_visual_score: float
    visual_score: float
    rights_status: str
    rights_rank_adjustment: float

    def __getattr__(self, name: str):
        return getattr(self.base, name)


def _safe_https_url(raw: object) -> str:
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
    return value


def _kind(candidate: dict) -> str:
    value = str(candidate.get("kind") or "").strip().lower()
    if value not in {"image", "video"}:
        raise RuntimeError("kind deve ser image ou video")
    return value


def _semantic_fit(candidate: dict) -> str | None:
    value = str(candidate.get("semantic_fit") or "").strip().casefold()
    return value if value in SEMANTIC_FIT_RANK else None


def _semantic_rank(candidate: dict) -> int:
    fit = _semantic_fit(candidate)
    return SEMANTIC_FIT_RANK.get(fit, -1)


def _semantic_ranking_score(candidate: dict, technical_selection_score: float) -> float:
    """Return a 0-100 lexicographic score: semantic tier first, technical score second.

    Pools created before semantic_fit keep the previous raw technical selection score.
    When semantic_fit is present, each tier occupies a non-overlapping 25-point band,
    so a technically excellent generic candidate cannot beat a direct/exact candidate.
    """
    fit = _semantic_fit(candidate)
    technical = apply_vibe_adjustment(
        max(0.0, min(100.0, float(technical_selection_score))),
        candidate.get("_artist_vibe_score"),
    )
    return semantic_selection_score(technical, fit)


def _best_explicit_semantic_rank(slot: dict) -> int | None:
    candidates = slot.get("candidates")
    if not isinstance(candidates, list):
        return None
    ranks = [
        _semantic_rank(candidate)
        for candidate in candidates
        if isinstance(candidate, dict) and _semantic_fit(candidate) is not None
    ]
    return max(ranks) if ranks else None


def _semantic_gate_reason(slot: dict, candidate: dict) -> str | None:
    """Keep video-first fallback from overriding a better semantic tier across media types."""
    best_rank = _best_explicit_semantic_rank(slot)
    fit = _semantic_fit(candidate)
    if best_rank is None or fit is None:
        return None
    current_rank = SEMANTIC_FIT_RANK[fit]
    if current_rank >= best_rank:
        return None
    best_fit = next(name for name, rank in SEMANTIC_FIT_RANK.items() if rank == best_rank)
    return f"semantic_below_{best_fit}"


def _explicit_rights_status(candidate: dict, result: VisualSearchResult) -> str:
    explicit = str(candidate.get("rights_status") or "").strip().lower()
    if explicit in {"verified", "unknown", "restricted"}:
        return explicit
    return candidate_rights_status(result)


def _is_youtube_candidate(candidate: dict, kind: str) -> bool:
    if kind != "video":
        return False
    provider = str(candidate.get("search_provider") or "").strip().lower()
    if provider == "youtube_web":
        return True
    raw = str(candidate.get("source_page_url") or candidate.get("url") or "").strip()
    host = (urlparse(raw).hostname or "").casefold()
    return host in YOUTUBE_HOSTS


def _candidate_source_key(candidate: dict) -> str:
    """Reserve video segments, not an entire video source, during one resolution."""
    base = _LEGACY_CANDIDATE_SOURCE_KEY(candidate)
    if not base or _kind(candidate) != "video":
        return base

    raw_start = candidate.get("source_start_seconds")
    if raw_start is None:
        # Authored pools frequently reuse a source without preselecting a trim.
        # Keep those candidates independently inspectable; _inspect_candidate will
        # assign a deterministic unused baseline before technical inspection.
        auto_identity = str(candidate.get("file") or candidate.get("name") or "auto")
        auto_identity = re.sub(r"[^A-Za-z0-9._-]+", "_", auto_identity).strip("._")
        return f"{base}:segment:auto:{auto_identity or 'candidate'}"

    start = legacy._number(raw_start, 0.0)
    raw_end = candidate.get("source_end_seconds")
    end = legacy._number(raw_end) if raw_end is not None else None
    end_token = f"{end:.6f}" if end is not None and end > start else "auto"
    return f"{base}:segment:{start:.6f}:{end_token}"


def _video_source_identity(candidate: dict) -> str:
    return _LEGACY_CANDIDATE_SOURCE_KEY(candidate)


def _requested_video_interval(slot: dict, candidate: dict) -> tuple[float, float]:
    start = legacy._number(candidate.get("source_start_seconds"), 0.0)
    required = legacy._number(slot.get("required_seconds"), legacy.DEFAULT_REQUIRED_SECONDS)
    crossfade = legacy._number(slot.get("crossfade_seconds"), legacy.DEFAULT_CROSSFADE_SECONDS)
    assessment = legacy.assess_trim(
        1e12,
        shot_duration_seconds=required,
        source_start_seconds=start,
        crossfade_seconds=crossfade,
        **legacy._playback_options(slot, "video"),
    )
    return start, start + assessment.required_seconds


def _intervals_overlap(
    left_start: float,
    left_end: float,
    right_start: float,
    right_end: float,
) -> bool:
    return min(left_end, right_end) - max(left_start, right_start) > 1e-6


def _reuse_rejection_reason(source_identity: str, start: float, end: float) -> str | None:
    if not source_identity:
        return None
    used = SELECTED_VIDEO_SEGMENTS.get(source_identity, [])
    if len(used) >= MAX_VIDEO_USES_PER_SOURCE:
        return "video_source_reuse_limit"
    if any(
        _intervals_overlap(start, end, used_start, used_end)
        for used_start, used_end in used
    ):
        return "video_segment_overlaps_selected"
    return None


def _assign_auto_video_start(slot: dict, candidate: dict, source_identity: str) -> None:
    if candidate.get("source_start_seconds") is not None:
        return
    used = SELECTED_VIDEO_SEGMENTS.get(source_identity, [])
    if not used:
        candidate["source_start_seconds"] = 0.0
        return

    required = legacy._number(slot.get("required_seconds"), legacy.DEFAULT_REQUIRED_SECONDS)
    crossfade = legacy._number(slot.get("crossfade_seconds"), legacy.DEFAULT_CROSSFADE_SECONDS)
    assessment = legacy.assess_trim(
        1e12,
        shot_duration_seconds=required,
        source_start_seconds=0.0,
        crossfade_seconds=crossfade,
        **legacy._playback_options(slot, "video"),
    )
    spacing = max(AUTO_REUSE_GAP_SECONDS, assessment.required_seconds * 0.5)
    candidate["source_start_seconds"] = round(
        max(end for _start, end in used) + spacing,
        3,
    )


def _candidate_result(candidate: dict, slot_id: str, index: int) -> VisualSearchResult:
    try:
        candidate = normalize_visual_candidate(candidate)
    except ValueError as exc:
        raise RuntimeError(f"{slot_id}[{index}]: {exc}") from exc
    kind = _kind(candidate)
    file_name = str(candidate.get("file") or "").strip()
    provider_id = str(candidate.get("provider_id") or f"{slot_id}-{index}")
    name = str(candidate.get("name") or file_name or provider_id)
    source_page = _safe_https_url(candidate.get("source_page_url"))
    raw_url = _safe_https_url(candidate.get("url"))
    creator = str(candidate.get("creator") or "")
    license_name = str(candidate.get("license") or "")
    license_url = _safe_https_url(candidate.get("license_url"))
    source = str(candidate.get("source") or "web")
    credit = str(candidate.get("credit") or candidate.get("attribution") or creator or source)
    search_provider = str(candidate.get("search_provider") or "authoring-pool")

    width = candidate.get("width")
    height = candidate.get("height")
    duration = candidate.get("duration_seconds")
    parsed_width = int(width) if isinstance(width, (int, float)) and not isinstance(width, bool) else None
    parsed_height = int(height) if isinstance(height, (int, float)) and not isinstance(height, bool) else None
    parsed_duration = (
        float(duration)
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else None
    )

    if _is_youtube_candidate(candidate, kind):
        page = source_page or raw_url
        if not page:
            raise RuntimeError(f"{slot_id}[{index}]: pagina HTTPS de video web obrigatoria.")
        suffix = Path(file_name).suffix.lower().lstrip(".") or "mp4"
        return VisualSearchResult(
            provider_id=provider_id,
            name=name,
            kind="video",
            source=source or "youtube",
            source_page_url=page,
            creator=creator,
            license=license_name,
            license_url=license_url,
            attribution=credit,
            search_provider="youtube_web",
            description=str(candidate.get("description") or ""),
            width=parsed_width,
            height=parsed_height,
            duration_seconds=parsed_duration,
            file_format=suffix,
            mime_type=str(candidate.get("mime_type") or "") or None,
            thumbnail_url=_safe_https_url(candidate.get("thumbnail_url")) or None,
            download_url=None,
            allowed_download_hosts=(),
            download_note="web video resolved during GitHub Action inspection",
        )

    if not raw_url:
        raise RuntimeError(f"{slot_id}[{index}]: url HTTPS direta ou pagina web suportada obrigatoria.")
    host = (urlparse(raw_url).hostname or "").casefold()
    suffix = Path(file_name or unquote(urlparse(raw_url).path)).suffix.lower()
    valid_suffixes = VIDEO_ASSET_EXTENSIONS if kind == "video" else IMAGE_SUFFIXES
    if suffix not in valid_suffixes:
        raise RuntimeError(
            f"{slot_id}[{index}]: extensao {suffix or '<ausente>'} nao suportada para {kind}."
        )

    allowed_hosts = KNOWN_REDIRECT_HOSTS.get(host, (host,))
    return VisualSearchResult(
        provider_id=provider_id,
        name=name,
        kind=kind,
        source=source,
        source_page_url=source_page or raw_url,
        creator=creator,
        license=license_name,
        license_url=license_url,
        attribution=credit,
        search_provider=search_provider,
        description=str(candidate.get("description") or ""),
        width=parsed_width,
        height=parsed_height,
        duration_seconds=parsed_duration,
        file_format=suffix.lstrip("."),
        mime_type=str(candidate.get("mime_type") or "") or None,
        thumbnail_url=_safe_https_url(candidate.get("thumbnail_url")) or None,
        download_url=raw_url,
        allowed_download_hosts=allowed_hosts,
        download_note=(
            "general web direct-media candidate; rights metadata is non-blocking"
        ),
    )


def _inspect_candidate(project_root: Path, slot: dict, candidate: dict, index: int):
    slot_id = str(slot.get("id") or "").strip()
    result = _candidate_result(candidate, slot_id, index)
    required = legacy._number(slot.get("required_seconds"), legacy.DEFAULT_REQUIRED_SECONDS)
    crossfade = legacy._number(slot.get("crossfade_seconds"), legacy.DEFAULT_CROSSFADE_SECONDS)

    if result.kind == "video":
        source_identity = _video_source_identity(candidate)
        _assign_auto_video_start(slot, candidate, source_identity)
        start, consumed_end = _requested_video_interval(slot, candidate)
        reuse_reason = _reuse_rejection_reason(source_identity, start, consumed_end)
        if reuse_reason:
            print(
                f"VIDEO_REUSE_SKIP slot={slot_id} id={result.provider_id} "
                f"start={start:.3f} end={consumed_end:.3f} reason={reuse_reason}",
                flush=True,
            )
            raise RuntimeError(reuse_reason)
        INSPECTED_VIDEO_SEGMENTS[(slot_id, _candidate_source_key(candidate))] = (
            source_identity,
            start,
            consumed_end,
        )
    else:
        start = 0.0

    end = candidate.get("source_end_seconds")
    base = inspect_web_candidate(
        project_root,
        result,
        shot_duration_seconds=required if result.kind == "video" else None,
        source_start_seconds=start if result.kind == "video" else 0.0,
        source_end_seconds=(
            legacy._number(end)
            if result.kind == "video" and end is not None
            else None
        ),
        crossfade_seconds=crossfade if result.kind == "video" else 0.0,
        **legacy._playback_options(slot, result.kind),
        repetition_history=slot.get("_repetition_history"),
        exclude_episode=slot.get("_episode"),
    )
    if result.search_provider == "youtube_web":
        WEB_DOWNLOADED_PATHS[result.provider_id] = base.path

    minimum = legacy._number(
        slot.get("min_visual_score"),
        legacy.MIN_VIDEO_SCORE if result.kind == "video" else legacy.MIN_IMAGE_SCORE,
    )
    reasons: list[str] = []
    if base.visual_score < minimum:
        reasons.append(f"score_below_{minimum:.1f}")
    if result.kind == "video":
        if base.trim and base.trim.safe_for_shot is False:
            reasons.append("unsafe_trim")
        if base.is_practically_static is True:
            reasons.append("practically_static")
    semantic_reason = _semantic_gate_reason(slot, candidate)
    if semantic_reason:
        reasons.append(semantic_reason)

    status = _explicit_rights_status(candidate, result)
    ranked = RankedInspection(
        base=base,
        technical_visual_score=base.visual_score,
        visual_score=selection_score(base.selection_score, status),
        rights_status=status,
        rights_rank_adjustment=rights_rank_adjustment(status),
    )
    return result, ranked, reasons


def _score_record(index: int, candidate: dict, result: VisualSearchResult, inspection, reasons: list[str]) -> dict:
    technical_selection_score = inspection.visual_score
    fit = _semantic_fit(candidate)
    final_ranking_score = _semantic_ranking_score(candidate, technical_selection_score)
    return {
        "candidate_index": index,
        "name": result.name,
        "kind": result.kind,
        "visual_score": inspection.technical_visual_score,
        "selection_score": final_ranking_score,
        "technical_selection_score": technical_selection_score,
        **({"artist_vibe": candidate["_artist_vibe_score"]} if candidate.get("_artist_vibe_score") is not None else {}),
        "semantic_fit": fit or "unspecified",
        "semantic_rank": SEMANTIC_FIT_RANK.get(fit) if fit is not None else None,
        "repetition": inspection.repetition.as_dict() if inspection.repetition else None,
        "downgraded_for_repetition": bool(inspection.repetition and inspection.repetition.is_repeated),
        "rights_status": inspection.rights_status,
        "rights_rank_adjustment": inspection.rights_rank_adjustment,
        "rights_blocks_selection": False,
        "width": inspection.width,
        "height": inspection.height,
        "duration_seconds": inspection.duration_seconds,
        "fps": inspection.fps,
        "opening_motion_score": inspection.opening_motion_score,
        "motion_score": inspection.motion_score,
        "practically_static": inspection.is_practically_static,
        "trim_safe": inspection.trim_safe,
        "eligible_for_auto_selection": not reasons,
        "warnings": reasons + list(inspection.warnings),
        "editorial_rank": int(max(1, legacy._number(candidate.get("editorial_rank"), index))),
    }


def _safe_filename(raw: str, fallback: str, suffix: str) -> str:
    source = Path(raw).name if raw else fallback
    stem = Path(source).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or fallback
    return f"{cleaned[:90]}{suffix}"


def _reserve_selected_video_segment(slot_id: str, candidate: dict) -> None:
    key = (slot_id, _candidate_source_key(candidate))
    inspected = INSPECTED_VIDEO_SEGMENTS.get(key)
    if inspected is None:
        return
    source_identity, start, end = inspected
    SELECTED_VIDEO_SEGMENTS.setdefault(source_identity, []).append((start, end))
    print(
        f"VIDEO_REUSE_RESERVED slot={slot_id} source={source_identity} "
        f"start={start:.3f} end={end:.3f} "
        f"use={len(SELECTED_VIDEO_SEGMENTS[source_identity])}/{MAX_VIDEO_USES_PER_SOURCE}",
        flush=True,
    )


def _asset_entry(slot_id: str, candidate: dict, result: VisualSearchResult) -> dict:
    focus = (
        candidate.get("focus")
        if isinstance(candidate.get("focus"), dict)
        else {"x": 0.5, "y": 0.5}
    )
    credit = str(candidate.get("credit") or result.attribution or result.creator or result.source)

    if result.search_provider == "youtube_web":
        source_path = WEB_DOWNLOADED_PATHS.get(result.provider_id)
        if source_path is None or not source_path.is_file():
            raise RuntimeError(f"{slot_id}: video web selecionado nao esta disponivel no cache.")
        episode_assets = PROJECT_ROOT / "episodes" / CURRENT_EPISODE / "assets"
        episode_assets.mkdir(parents=True, exist_ok=True)
        # One provider_id always maps to the same staged file. Multiple asset ids can
        # safely point to it with different trims, which also lets Best Segment see
        # them as the same underlying source and protect against window overlap.
        desired = _safe_filename(
            "",
            f"youtube-{result.provider_id}",
            source_path.suffix.lower(),
        )
        destination = episode_assets / desired
        if source_path.resolve() != destination.resolve() and not destination.is_file():
            shutil.copy2(source_path, destination)
        _reserve_selected_video_segment(slot_id, candidate)
        return {
            "id": slot_id,
            "file": desired,
            "credit": credit,
            "license": result.license,
            "focus": focus,
        }

    file_name = str(candidate.get("file") or result.suggested_file or f"{slot_id}.{result.file_format}")
    if result.kind == "video":
        _reserve_selected_video_segment(slot_id, candidate)
    return {
        "id": slot_id,
        "file": file_name,
        "url": result.download_url,
        "credit": credit,
        "license": result.license,
        "focus": focus,
    }


def _metadata_priority(candidate: dict, index: int):
    # Semantic fit decides the inspection order when authored; technical/editorial
    # metadata remains the tie-breaker. Unlabelled legacy pools keep rank -1.
    priority = legacy._metadata_priority_original(candidate, index)
    if candidate.get("_artist_vibe_score") is not None:
        # The vibe-aware legacy priority already starts with semantic rank.
        return priority
    return (
        _semantic_rank(candidate),
        *priority,
    )


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _warn(message: str) -> None:
    if str(os.getenv("GITHUB_ACTIONS") or "").casefold() == "true":
        print(f"::warning::{message}")
    else:
        print(f"WARNING: {message}")


def _ensure_youtube_po_provider() -> str | None:
    """Start a local bgutil provider on GitHub Actions; failure remains non-blocking."""
    if str(os.getenv("YOUTUBE_PO_TOKEN_DISABLED") or "").casefold() in {"1", "true", "yes"}:
        return None
    if str(os.getenv("GITHUB_ACTIONS") or "").casefold() != "true":
        return None
    if _port_is_open(YOUTUBE_PO_PROVIDER_HOST, YOUTUBE_PO_PROVIDER_PORT):
        return YOUTUBE_PO_PROVIDER_URL

    docker = shutil.which("docker")
    if not docker:
        _warn("YouTube PO Token provider: Docker indisponivel; usando yt-dlp padrao.")
        return None

    subprocess.run(
        [docker, "rm", "-f", YOUTUBE_PO_PROVIDER_CONTAINER],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    started = subprocess.run(
        [
            docker,
            "run",
            "--detach",
            "--rm",
            "--name",
            YOUTUBE_PO_PROVIDER_CONTAINER,
            "--publish",
            f"{YOUTUBE_PO_PROVIDER_HOST}:{YOUTUBE_PO_PROVIDER_PORT}:{YOUTUBE_PO_PROVIDER_PORT}",
            YOUTUBE_PO_PROVIDER_IMAGE,
            "--host",
            "0.0.0.0",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        detail = (started.stderr or started.stdout or "erro desconhecido").strip().splitlines()[-1]
        _warn(f"YouTube PO Token provider nao iniciou ({detail}); usando yt-dlp padrao.")
        return None

    for _ in range(30):
        if _port_is_open(YOUTUBE_PO_PROVIDER_HOST, YOUTUBE_PO_PROVIDER_PORT):
            return YOUTUBE_PO_PROVIDER_URL
        time.sleep(0.5)

    subprocess.run(
        [docker, "rm", "-f", YOUTUBE_PO_PROVIDER_CONTAINER],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    _warn("YouTube PO Token provider nao ficou pronto; usando yt-dlp padrao.")
    return None


def _with_youtube_po_options(params: dict | None, provider_url: str) -> dict:
    patched = dict(params or {})
    raw_extractor_args = patched.get("extractor_args")
    extractor_args = dict(raw_extractor_args) if isinstance(raw_extractor_args, dict) else {}

    raw_youtube = extractor_args.get("youtube")
    youtube_args = dict(raw_youtube) if isinstance(raw_youtube, dict) else {}
    raw_clients = youtube_args.get("player_client")
    if isinstance(raw_clients, (list, tuple)):
        clients = [str(value) for value in raw_clients if str(value).strip()]
    elif raw_clients:
        clients = [str(raw_clients)]
    else:
        clients = []
    if "mweb" not in clients:
        clients.insert(0, "mweb")
    youtube_args["player_client"] = clients
    extractor_args["youtube"] = youtube_args

    raw_bgutil = extractor_args.get("youtubepot-bgutilhttp")
    bgutil_args = dict(raw_bgutil) if isinstance(raw_bgutil, dict) else {}
    bgutil_args["base_url"] = [provider_url]
    extractor_args["youtubepot-bgutilhttp"] = bgutil_args
    patched["extractor_args"] = extractor_args
    return patched


def _install_youtube_po_patch() -> None:
    provider_url = _ensure_youtube_po_provider()
    if not provider_url:
        return

    if not hasattr(web_engine, "_yt_dlp_api_original"):
        web_engine._yt_dlp_api_original = web_engine._yt_dlp_api
    original_api = web_engine._yt_dlp_api_original

    def patched_api():
        YoutubeDL, DownloadError = original_api()

        class PoTokenYoutubeDL(YoutubeDL):
            def __init__(self, params=None, auto_init=True):
                super().__init__(
                    _with_youtube_po_options(params, provider_url),
                    auto_init=auto_init,
                )

        return PoTokenYoutubeDL, DownloadError

    web_engine._yt_dlp_api = patched_api
    print("YouTube web: client mweb + PO Token provider habilitados (sem cookies/conta).")


def _install_patches() -> None:
    _install_youtube_po_patch()
    if not hasattr(legacy, "_metadata_priority_original"):
        legacy._metadata_priority_original = legacy._metadata_priority
    legacy._candidate_result = _candidate_result
    legacy._candidate_source_key = _candidate_source_key
    legacy._inspect_candidate = _inspect_candidate
    legacy._score_record = _score_record
    legacy._asset_entry = _asset_entry
    legacy._metadata_priority = _metadata_priority


def main() -> int:
    global CURRENT_EPISODE
    if len(sys.argv) >= 2:
        CURRENT_EPISODE = str(sys.argv[1]).strip()
    WEB_DOWNLOADED_PATHS.clear()
    SELECTED_VIDEO_SEGMENTS.clear()
    INSPECTED_VIDEO_SEGMENTS.clear()
    _install_patches()
    return legacy.main(prefer_video_candidates=True)


if __name__ == "__main__":
    raise SystemExit(main())
