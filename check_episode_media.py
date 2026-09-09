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


RECENT_BACKGROUND_TRACK_WINDOW = 20


def _single_line(value: object) -> str:
    return " ".join(str(value).replace("\x00", "").split())


def _classify_preflight_failure(exc: Exception, slug: str) -> tuple[str, bool, str, str]:
    """Return a stable machine-readable error code and a concrete repair hint."""

    detail = _single_line(exc)
    folded = detail.casefold()
    episode_root = f"episodes/{slug}" if slug else "episodes/<slug>"

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
    aliases: list[tuple[str, str]] = [
        ("asset_id", asset.id.casefold()),
        ("file", asset.file.casefold()),
    ]
    if asset.url:
        aliases.append(("url", _normalize_url_identity(asset.url)))
    return tuple(aliases)


def _assert_intra_episode_visuals_unique(episode: Episode) -> None:
    """Block reuse of the same main visual across shots in one episode."""

    seen: dict[tuple[str, str], tuple[str, str]] = {}

    for shot in episode.shots:
        asset = episode.assets.get(shot.asset_id)
        if asset is None:
            raise RuntimeError(
                f"Shot {shot.id!r} referencia asset inexistente {shot.asset_id!r}."
            )

        aliases = _visual_aliases(asset)
        for identity in aliases:
            previous = seen.get(identity)
            if previous is None:
                continue

            previous_shot, previous_asset = previous
            identity_kind, identity_value = identity
            raise RuntimeError(
                "INTRA_EPISODE_VISUAL_REUSE_BLOCKED: o mesmo visual foi usado "
                "mais de uma vez dentro do episodio. "
                f"Shot {shot.id!r} (asset={asset.id!r}) repete o visual de "
                f"{previous_shot!r} (asset={previous_asset!r}); "
                f"identidade={identity_kind}:{identity_value}. "
                "Cada shot principal deve usar uma imagem ou video diferente."
            )

        for identity in aliases:
            seen[identity] = (shot.id, asset.id)

    print(
        "[preflight] intra-episode visual uniqueness OK: "
        f"{len(episode.shots)} shot(s) sem reutilizacao"
    )


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
    parser = argparse.ArgumentParser(description="Validate episode media before publish queue.")
    parser.add_argument("episode", help="Episode slug")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    config = load_project_config(root / "config" / "config.json")
    episodes_dir = Path(config.paths.episodes_dir)
    cache_dir = Path(config.paths.cache_dir)
    if not cache_dir.is_absolute():
        cache_dir = root / cache_dir
    episode = load_episode(root, config.paths.episodes_dir, args.episode)

    work_dir = root / config.paths.work_dir / ".media-preflight" / episode.name
    video_cache = cache_dir / "video"
    manager = AssetManager(
        assets_dir=episode.assets_dir,
        work_dir=work_dir,
        width=config.render.width,
        height=config.render.height,
        scale=1,
        allowed_assets_root=episode.directory,
        video_cache_dir=video_cache,
    )

    print("[preflight] validating intra-episode visual uniqueness...")
    _assert_intra_episode_visuals_unique(episode)

    print(f"[preflight] validating {len(episode.assets)} episode assets...")
    manager.ensure_all(tuple(episode.assets.values()))
    print("[preflight] assets OK")

    resolved_music = resolve_background_music(
        root,
        episode.background_music,
        episode.name,
        cache_root=cache_dir,
    )
    if resolved_music is None:
        print("[preflight] background music: none")
    else:
        print(
            f"[preflight] background music OK: profile={resolved_music.profile} "
            f"file={resolved_music.path}"
        )
        if episode.background_music is not None:
            _assert_background_is_fresh(
                root,
                episodes_dir,
                cache_dir,
                episode.name,
                episode.background_music,
                resolved_music.path,
            )

    print("MEDIA_PREFLIGHT_RESULT=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _emit_structured_failure(exc)
        raise
