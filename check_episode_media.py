from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit

from engine.assets import AssetManager
from engine.audio_library import AudioCatalogEntry
from engine.config import load_project_config
from engine.episode import load_episode
from engine.models import BackgroundMusicSpec
from engine.music import MUSIC_ROOT, background_music_candidates, resolve_background_music


RECENT_BACKGROUND_TRACK_WINDOW = 20


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
        print(f"MEDIA_PREFLIGHT_RESULT=FAIL: {exc}")
        raise
