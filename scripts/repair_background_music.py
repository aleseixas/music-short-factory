from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from check_episode_media import (
    _candidate_aliases,
    _historical_background_entry,
    _recent_episode_slugs,
)
from engine.config import load_project_config
from engine.models import BackgroundMusicSpec
from engine.music import (
    MUSIC_ROOT,
    _load_music_profiles,
    background_music_candidates,
    resolve_background_music,
)


LOCAL_PROFILE_ORDER = (
    "ambient_calm",
    "bright_fun",
    "dark_cinematic",
    "emotional_piano",
    "energetic_hype",
    "hiphop_groove",
    "latin_pop_uplifting",
    "uplifting_documentary",
)


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON invalido: {path}")
    return data


def _used_recent_aliases(root: Path, episodes_dir: Path, slug: str) -> set[str]:
    used: set[str] = set()
    for previous_slug in _recent_episode_slugs(root, episodes_dir, slug):
        historical = _historical_background_entry(root, episodes_dir, previous_slug)
        if historical is None:
            continue
        _profile, entry = historical
        used.update(_candidate_aliases(entry))
    return used


def _all_profile_order(root: Path, current: str) -> tuple[str, ...]:
    """Return every configured profile in deterministic repair order.

    Local profiles stay first because they are cheap/reliable, but external and fallback
    profiles are included in the same repair pass. This prevents a recoverable freshness
    failure from requiring human intervention just because all local tracks were used recently.
    """
    music_root = (root / MUSIC_ROOT).resolve()
    profiles = _load_music_profiles(root.resolve(), music_root)
    available = set(profiles)

    ordered = [profile for profile in LOCAL_PROFILE_ORDER if profile in available]
    ordered.extend(sorted(available.difference(ordered), key=str.casefold))
    if not ordered:
        return ()

    if current in ordered:
        start = ordered.index(current) + 1
        ordered = ordered[start:] + ordered[:start]
    return tuple(profile for profile in ordered if profile != current)


def _resolved_catalog_entry(candidates, resolved_path: Path):
    resolved = resolved_path.resolve()
    for entry in candidates:
        try:
            if entry.local_path.resolve() == resolved:
                return entry
        except OSError:
            pass
        relative = entry.relative_file.replace("\\", "/").casefold()
        if resolved.as_posix().casefold().endswith("/" + relative):
            return entry
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Switch an episode background automatically to a resolvable fresh profile "
            "from the complete configured music catalog."
        )
    )
    parser.add_argument("episode", help="Episode slug")
    args = parser.parse_args()

    root = PROJECT_ROOT
    config = load_project_config(root / "config" / "config.json")
    episodes_dir = Path(config.paths.episodes_dir)
    timeline_path = root / episodes_dir / args.episode / "timeline.json"
    if not timeline_path.is_file():
        raise RuntimeError(f"Timeline ausente: {timeline_path}")

    timeline = _load_json(timeline_path)
    background = timeline.get("background_music")
    if not isinstance(background, dict):
        raise RuntimeError("background_music ausente ou invalido no timeline.json")

    current = str(background.get("profile") or "").strip()
    if not current:
        raise RuntimeError("background_music.profile ausente")

    try:
        volume = float(background.get("volume", 0.1))
    except (TypeError, ValueError):
        volume = 0.1

    recent_aliases = _used_recent_aliases(root, episodes_dir, args.episode)
    replacement: str | None = None

    profile_order = _all_profile_order(root, current)
    print(f"BACKGROUND_AUTO_REPAIR_PROFILE_COUNT={len(profile_order)}")

    for profile in profile_order:
        spec = BackgroundMusicSpec(profile=profile, volume=volume)
        try:
            candidates = background_music_candidates(root, spec, args.episode)
        except RuntimeError as exc:
            print(f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} reason=invalid detail={exc}")
            continue
        if not candidates:
            print(f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} reason=no_candidates")
            continue

        # Resolve now instead of merely trusting catalog metadata. Remote HTTP failures,
        # invalid codecs and bad files are therefore skipped inside auto-repair rather than
        # consuming another human-guided preflight attempt.
        try:
            resolved = resolve_background_music(root, spec, args.episode)
        except RuntimeError as exc:
            print(f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} reason=unresolvable detail={exc}")
            continue
        if resolved is None:
            print(f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} reason=unresolvable")
            continue

        selected = _resolved_catalog_entry(candidates, resolved.path)
        if selected is None:
            print(f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} reason=selected_entry_unknown")
            continue
        if not recent_aliases.isdisjoint(_candidate_aliases(selected)):
            print(
                f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} "
                f"reason=recent_reuse file={selected.relative_file}"
            )
            continue

        replacement = profile
        print(
            f"BACKGROUND_AUTO_REPAIR_SELECTED profile={profile} "
            f"file={selected.relative_file}"
        )
        break

    if replacement is None:
        raise RuntimeError(
            "Nenhum profile alternativo resolvivel e fresco disponivel em todo o catalogo."
        )

    background["profile"] = replacement
    timeline["background_music"] = background
    timeline_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"BACKGROUND_AUTO_REPAIR: {current} -> {replacement} (catalog-wide-verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
