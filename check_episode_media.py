from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

from engine.assets import AssetManager
from engine.audio_library import AudioCatalogEntry
from engine.config import load_project_config
from engine.episode import load_episode
from engine.models import AssetSpec, BackgroundMusicSpec, Episode
from engine.music import MUSIC_ROOT, background_music_candidates, resolve_background_music
from engine.youtube import canonical_youtube_url, extract_youtube_video_id


RECENT_BACKGROUND_TRACK_WINDOW = 20


def _single_line(value: object) -> str:
    return " ".join(str(value).replace("\x00", "").split())


def _classify_preflight_failure(exc: Exception, slug: str) -> tuple[str, bool, str, str]:
    """Return a stable machine-readable error code and a concrete repair hint."""

    detail = _single_line(exc)
    folded = detail.casefold()
    episode_root = f"episodes/{slug}" if slug else "episodes/<slug>"

    if any(token in folded for token in ("source_start_seconds", "source_end_seconds", "trecho de video insuficiente", "unsafe_trim")):
        return ("VIDEO_SOURCE_WINDOW_INVALID", True, f"{episode_root}/timeline.json",
                "Select a sufficiently long source and a valid trim for this shot, then validate again; do not blindly retry.")

    if "asset desconhecido no plano" in folded or "asset de overlay desconhecido" in folded:
        return ("SHOT_REFERENCES_MISSING_ASSET", True, f"{episode_root}/timeline.json + {episode_root}/assets.json",
                "Resolve the named reference to a verified candidate for the same shot.")

    if any(token in folded for token in ("timed out", "timeout", "connection reset", "temporarily unavailable")):
        return ("REMOTE_MEDIA_TEMPORARY_FAILURE", False, "remote media provider",
                "Keep the same episode and use bounded backoff after the external provider recovers.")

    if any(token in folded for token in ("ffprobe nao encontrado", "ffprobe_binary nao aponta", "dependencia imageio-ffmpeg ausente")):
        return ("MEDIA_TOOLCHAIN_UNAVAILABLE", False, "runtime dependencies",
                "Install/configure the required media toolchain; changing episode assets cannot fix this error.")

    if "intra_episode_visual_reuse_blocked" in folded:
        return (
            "INTRA_EPISODE_VISUAL_REUSE",
            True,
            f"{episode_root}/assets.json + {episode_root}/timeline.json",
            "Replace the repeated main visual with a different unique source, keep the same slug, then rerun media preflight.",
        )

    if "background_reuse_blocked" in folded:
        return (
            "BACKGROUND_MUSIC_REUSE",
            True,
            f"{episode_root}/timeline.json",
            "Choose a different background track/profile for this same episode, then rerun media preflight.",
        )

    if (
        "background music" in folded
        or "profile de background music" in folded
        or "faixa do profile" in folded
    ):
        return (
            "BACKGROUND_MUSIC_INVALID",
            True,
            f"{episode_root}/timeline.json",
            "Fix or replace the background music profile/source for this same episode, then rerun media preflight.",
        )

    if any(token in folded for token in ("http error 403", "http 403", "status 403")):
        return (
            "VISUAL_ASSET_HTTP_403",
            True,
            f"{episode_root}/assets.json",
            "The remote visual is forbidden. Replace that asset URL/source with another accessible unique candidate for the same shot and rerun preflight.",
        )

    if any(token in folded for token in ("http error 404", "http 404", "status 404")):
        return (
            "VISUAL_ASSET_HTTP_404",
            True,
            f"{episode_root}/assets.json",
            "The remote visual no longer exists. Replace that asset with another accessible unique candidate for the same shot and rerun preflight.",
        )

    if any(token in folded for token in ("http error 429", "http 429", "status 429")):
        return (
            "REMOTE_MEDIA_RATE_LIMITED",
            False,
            "remote media provider / credentials",
            "Do not change topic or queue the episode. Inspect provider/auth/rate-limit state and retry only when external access is healthy.",
        )

    if any(token in folded for token in ("http error 5", "http 5", "status 5")):
        return (
            "REMOTE_MEDIA_PROVIDER_ERROR",
            False,
            "remote media provider",
            "Do not change topic or queue the episode. This is an external/provider failure; retry only after confirming the provider is healthy.",
        )

    if "shot " in folded and "referencia asset inexistente" in folded:
        return (
            "SHOT_REFERENCES_MISSING_ASSET",
            True,
            f"{episode_root}/timeline.json + {episode_root}/assets.json",
            "Make the shot asset_id point to an existing asset, or add the missing asset entry. Keep the same slug and rerun preflight.",
        )

    if any(
        token in folded
        for token in (
            "asset de video invalido",
            "asset de video ausente",
            "arquivo nao e uma imagem valida",
            "asset de imagem",
            "asset de video",
            "asset "
        )
    ) and any(
        token in folded
        for token in (
            "invalido",
            "invalid",
            "ausente",
            "missing",
            "nao encontrado",
            "not found",
            "stream de video ausente",
        )
    ):
        return (
            "VISUAL_ASSET_INVALID",
            True,
            f"{episode_root}/assets.json",
            "Replace the failing image/video entry with a valid accessible unique asset for the same shot, then rerun media preflight.",
        )

    if any(
        token in folded
        for token in (
            "json invalido",
            "timeline.json",
            "assets.json",
            "story.json",
            "delivery invalido",
            "freeze_frame",
            "fps invalido",
        )
    ):
        return (
            "EPISODE_SCHEMA_OR_TIMELINE_INVALID",
            True,
            episode_root,
            "Fix the exact invalid field/file named in MEDIA_PREFLIGHT_ERROR_DETAIL for this same episode, then rerun media preflight.",
        )

    if "ffprobe" in folded or "ffmpeg" in folded:
        return (
            "MEDIA_PROBE_OR_CODEC_ERROR",
            True,
            f"{episode_root}/assets.json",
            "Replace or repair the media file identified in the detail so FFmpeg/ffprobe can decode it, then rerun preflight.",
        )

    return (
        "UNCLASSIFIED_ENGINE_ERROR",
        False,
        "engine/global code or unclassified episode data",
        "Read the traceback immediately above this marker. Do not create a new topic or publish queue; fix the named cause first and keep the same slug.",
    )


def _emit_structured_failure(exc: Exception) -> None:
    slug = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    code, recoverable, target, action = _classify_preflight_failure(exc, slug)
    detail = _single_line(exc) or exc.__class__.__name__
    same_slug = slug or "UNKNOWN"

    print("MEDIA_PREFLIGHT_RESULT=FAIL")
    print(f"MEDIA_PREFLIGHT_ERROR_CODE={code}")
    print(f"MEDIA_PREFLIGHT_ERROR_CLASS={exc.__class__.__name__}")
    print(f"MEDIA_PREFLIGHT_ERROR_DETAIL={detail}")
    print(f"MEDIA_PREFLIGHT_RECOVERABLE={'true' if recoverable else 'false'}")
    print(f"MEDIA_PREFLIGHT_TARGET={target}")
    print(f"MEDIA_PREFLIGHT_RECOMMENDED_ACTION={action}")
    print(f"MEDIA_PREFLIGHT_SAME_SLUG={same_slug}")


def _recent_episode_slugs(
    root: Path,
    episodes_dir: Path,
    current_slug: str,
    limit: int = RECENT_BACKGROUND_TRACK_WINDOW,
) -> list[str]:
    """Return episode creation order from git, newest first, excluding current."""

    try:
        completed = subprocess.run(
            [
                "git",
                "log",
                "--format=",
                "--name-only",
                "--diff-filter=A",
                "--",
                episodes_dir.as_posix(),
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        print(
            "[preflight] background freshness warning: historico git indisponivel "
            f"({exc.__class__.__name__}); seguindo sem hard gate de repeticao."
        )
        return []

    if completed.returncode != 0:
        print(
            "[preflight] background freshness warning: git log falhou; "
            "seguindo sem hard gate de repeticao."
        )
        return []

    prefix = episodes_dir.as_posix().rstrip("/") + "/"
    recent: list[str] = []
    seen: set[str] = set()
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip().replace("\\", "/")
        if not line.startswith(prefix) or not line.endswith("/story.json"):
            continue
        relative = line[len(prefix) :]
        slug, separator, filename = relative.partition("/")
        if not separator or filename != "story.json":
            continue
        if not slug or slug == current_slug or slug in seen:
            continue
        if not (root / episodes_dir / slug / "timeline.json").is_file():
            continue
        seen.add(slug)
        recent.append(slug)
        if len(recent) >= limit:
            break
    return recent


def _normalize_url_identity(value: str) -> str:
    youtube = canonical_youtube_url(value)
    if youtube:
        return youtube
    parsed = urlsplit(value.strip())
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path,
            "",
            "",
        )
    ).casefold()


def _visual_aliases(asset: AssetSpec) -> tuple[tuple[str, str], ...]:
    video_id = extract_youtube_video_id(asset.url) if asset.url else None
    aliases: list[tuple[str, str]] = [
        ("asset_id", asset.id.casefold()),
        # YouTube IDs are case-sensitive, including when embedded in filenames.
        # Preserve their identity on the Linux runner; legacy assets keep the
        # historical case-insensitive filename comparison.
        ("file", asset.file if video_id else asset.file.casefold()),
    ]
    if asset.url:
        aliases.append(("youtube", video_id) if video_id else ("url", _normalize_url_identity(asset.url)))
    return tuple(aliases)


def _assert_intra_episode_visuals_unique(episode: Episode) -> None:
    """Compatibility entrypoint for the same strict validator used by both CLIs."""
    from check_episode_media_batch import _collect_visual_structure_errors
    errors = _collect_visual_structure_errors(episode, getattr(episode, "name", "UNKNOWN"))
    if errors:
        raise RuntimeError(str(errors[0]["detail"]))


def _candidate_aliases(entry: AudioCatalogEntry) -> set[str]:
    aliases = {f"file:{entry.relative_file.casefold()}"}
    if entry.url:
        aliases.add(f"url:{_normalize_url_identity(entry.url)}")
    return aliases


def _historical_background_entry(
    root: Path,
    episodes_dir: Path,
    slug: str,
) -> tuple[str, AudioCatalogEntry] | None:
    timeline_path = root / episodes_dir / slug / "timeline.json"
    try:
        data = json.loads(timeline_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    background = data.get("background_music")
    if not isinstance(background, dict):
        return None
    profile = str(background.get("profile", "")).strip()
    if not profile:
        return None

    try:
        candidates = background_music_candidates(
            root,
            BackgroundMusicSpec(profile=profile, volume=0.1),
            slug,
        )
    except RuntimeError:
        return None
    if not candidates:
        return None
    return profile, candidates[0]


def _selected_current_entry(
    root: Path,
    cache_dir: Path,
    episode_slug: str,
    spec: BackgroundMusicSpec,
    resolved_path: Path,
) -> AudioCatalogEntry:
    candidates = background_music_candidates(root, spec, episode_slug)
    if not candidates:
        raise RuntimeError("Background music sem candidatas resolviveis no catalogo.")

    resolved = resolved_path.resolve()
    bases = [
        (root / MUSIC_ROOT).resolve(),
        (cache_dir / "music").resolve(),
    ]
    relative_file: str | None = None
    for base in bases:
        try:
            relative_file = resolved.relative_to(base).as_posix()
            break
        except ValueError:
            continue

    if relative_file is not None:
        normalized = relative_file.casefold()
        for entry in candidates:
            if entry.relative_file.casefold() == normalized:
                return entry
    return candidates[0]


def _assert_background_is_fresh(
    root: Path,
    episodes_dir: Path,
    cache_dir: Path,
    episode_slug: str,
    spec: BackgroundMusicSpec,
    resolved_path: Path,
) -> None:
    current_entry = _selected_current_entry(
        root,
        cache_dir,
        episode_slug,
        spec,
        resolved_path,
    )
    current_aliases = _candidate_aliases(current_entry)
    recent_slugs = _recent_episode_slugs(
        root,
        episodes_dir,
        episode_slug,
        RECENT_BACKGROUND_TRACK_WINDOW,
    )

    if not recent_slugs:
        print("[preflight] background freshness: sem historico recente comparavel")
        return

    for previous_slug in recent_slugs:
        historical = _historical_background_entry(root, episodes_dir, previous_slug)
        if historical is None:
            continue
        previous_profile, previous_entry = historical
        if current_aliases.isdisjoint(_candidate_aliases(previous_entry)):
            continue

        raise RuntimeError(
            "BACKGROUND_REUSE_BLOCKED: a faixa de background escolhida "
            f"({current_entry.relative_file}) ja foi usada recentemente no episodio "
            f"{previous_slug!r} (profile={previous_profile!r}). "
            f"Nao reutilize a mesma faixa dentro dos ultimos "
            f"{RECENT_BACKGROUND_TRACK_WINDOW} episodios. Pesquise/escolha outra "
            "background para ESTE MESMO episodio e rode um novo media preflight; "
            "nao descarte a historia nem crie a publish queue enquanto nao passar."
        )

    print(
        "[preflight] background freshness OK: "
        f"faixa nao repetida nos ultimos {len(recent_slugs)} episodio(s) comparados"
    )


def main() -> int:
    # Local validation and the Action execute exactly the same independent checks.
    from check_episode_media_batch import main as batch_main
    return batch_main()


if __name__ == "__main__":
    raise SystemExit(main())
