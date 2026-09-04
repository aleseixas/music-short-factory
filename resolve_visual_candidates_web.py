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
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
YOUTUBE_PO_PROVIDER_IMAGE = "brainicism/bgutil-ytdlp-pot-provider:1.3.2"
YOUTUBE_PO_PROVIDER_CONTAINER = "music-short-factory-bgutil"
YOUTUBE_PO_PROVIDER_HOST = "127.0.0.1"
YOUTUBE_PO_PROVIDER_PORT = 4416
YOUTUBE_PO_PROVIDER_URL = f"http://{YOUTUBE_PO_PROVIDER_HOST}:{YOUTUBE_PO_PROVIDER_PORT}"


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


def _candidate_result(candidate: dict, slot_id: str, index: int) -> VisualSearchResult:
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
        allowed_download_hosts=(host,),
        download_note=(
            "general web direct-media candidate; rights metadata is non-blocking"
        ),
    )


def _inspect_candidate(project_root: Path, slot: dict, candidate: dict, index: int):
    slot_id = str(slot.get("id") or "").strip()
    result = _candidate_result(candidate, slot_id, index)
    required = legacy._number(slot.get("required_seconds"), legacy.DEFAULT_REQUIRED_SECONDS)
    crossfade = legacy._number(slot.get("crossfade_seconds"), legacy.DEFAULT_CROSSFADE_SECONDS)
    start = legacy._number(candidate.get("source_start_seconds"), 0.0)
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

    status = _explicit_rights_status(candidate, result)
    ranked = RankedInspection(
        base=base,
        technical_visual_score=base.visual_score,
        visual_score=selection_score(base.visual_score, status),
        rights_status=status,
        rights_rank_adjustment=rights_rank_adjustment(status),
    )
    return result, ranked, reasons


def _score_record(index: int, candidate: dict, result: VisualSearchResult, inspection, reasons: list[str]) -> dict:
    return {
        "candidate_index": index,
        "name": result.name,
        "kind": result.kind,
        "visual_score": inspection.technical_visual_score,
        "selection_score": inspection.visual_score,
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
        desired = _safe_filename(
            str(candidate.get("file") or ""),
            f"{slot_id}-{result.provider_id}",
            source_path.suffix.lower(),
        )
        destination = episode_assets / desired
        if source_path.resolve() != destination.resolve():
            shutil.copy2(source_path, destination)
        return {
            "id": slot_id,
            "file": desired,
            "credit": credit,
            "license": result.license,
            "focus": focus,
        }

    file_name = str(candidate.get("file") or result.suggested_file or f"{slot_id}.{result.file_format}")
    return {
        "id": slot_id,
        "file": file_name,
        "url": result.download_url,
        "credit": credit,
        "license": result.license,
        "focus": focus,
    }


def _metadata_priority(candidate: dict, index: int):
    # Keep editorial/technical pre-ranking dominant. Rights are intentionally not a hard gate.
    return legacy._metadata_priority_original(candidate, index)


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
    legacy._inspect_candidate = _inspect_candidate
    legacy._score_record = _score_record
    legacy._asset_entry = _asset_entry
    legacy._metadata_priority = _metadata_priority


def main() -> int:
    global CURRENT_EPISODE
    if len(sys.argv) >= 2:
        CURRENT_EPISODE = str(sys.argv[1]).strip()
    _install_patches()
    return legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
