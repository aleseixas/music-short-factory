from __future__ import annotations

import argparse
import json
from pathlib import Path


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Switch an episode background to another local profile after a recoverable preflight failure."
    )
    parser.add_argument("episode", help="Episode slug")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    timeline_path = root / "episodes" / args.episode / "timeline.json"
    if not timeline_path.is_file():
        raise RuntimeError(f"Timeline ausente: {timeline_path}")

    timeline = _load_json(timeline_path)
    background = timeline.get("background_music")
    if not isinstance(background, dict):
        raise RuntimeError("background_music ausente ou invalido no timeline.json")

    current = str(background.get("profile") or "").strip()
    if not current:
        raise RuntimeError("background_music.profile ausente")

    # Prefer the next known-local profile. The media preflight remains the source
    # of truth: if the selected profile is unsuitable/unavailable/repeated it will
    # fail again and this helper advances once more on the next recovery cycle.
    if current in LOCAL_PROFILE_ORDER:
        start = LOCAL_PROFILE_ORDER.index(current) + 1
    else:
        start = 0

    candidates = LOCAL_PROFILE_ORDER[start:] + LOCAL_PROFILE_ORDER[:start]
    replacement = next((profile for profile in candidates if profile != current), None)
    if replacement is None:
        raise RuntimeError("Nenhum profile local alternativo disponivel")

    background["profile"] = replacement
    timeline["background_music"] = background
    timeline_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"BACKGROUND_AUTO_REPAIR: {current} -> {replacement}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
