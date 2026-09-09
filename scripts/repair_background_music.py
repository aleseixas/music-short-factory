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
from engine.music import background_music_candidates


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


def _profile_order_after(current: str) -> tuple[str, ...]:
    if current in LOCAL_PROFILE_ORDER:
        start = LOCAL_PROFILE_ORDER.index(current) + 1
        return LOCAL_PROFILE_ORDER[start:] + LOCAL_PROFILE_ORDER[:start]
    return LOCAL_PROFILE_ORDER


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Switch an episode background directly to a usable fresh local profile after a recoverable preflight failure."
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

    for profile in _profile_order_after(current):
        if profile == current:
            continue
        try:
            candidates = background_music_candidates(
                root,
                BackgroundMusicSpec(profile=profile, volume=volume),
                args.episode,
            )
        except RuntimeError as exc:
            print(f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} reason=invalid detail={exc}")
            continue
        if not candidates:
            print(f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} reason=no_candidates")
            continue

        selected = candidates[0]
        if not recent_aliases.isdisjoint(_candidate_aliases(selected)):
            print(
                f"BACKGROUND_AUTO_REPAIR_SKIP profile={profile} "
                f"reason=recent_reuse file={selected.relative_file}"
            )
            continue

        replacement = profile
        break

    if replacement is None:
        raise RuntimeError(
            "Nenhum profile local alternativo valido e fresco disponivel para o episodio."
        )

    background["profile"] = replacement
    timeline["background_music"] = background
    timeline_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"BACKGROUND_AUTO_REPAIR: {current} -> {replacement} (fresh-direct)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
