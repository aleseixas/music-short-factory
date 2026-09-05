from __future__ import annotations

import argparse
from pathlib import Path

from engine.assets import AssetManager
from engine.config import load_project_config
from engine.episode import load_episode
from engine.music import resolve_background_music


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate episode media before publish queue.")
    parser.add_argument("episode", help="Episode slug")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    config = load_project_config(root / "config" / "config.json")
    episode = load_episode(root, config.paths.episodes_dir, args.episode)

    work_dir = root / config.paths.work_dir / ".media-preflight" / episode.name
    video_cache = root / config.paths.cache_dir / "video"
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
        cache_root=Path(config.paths.cache_dir),
    )
    if resolved_music is None:
        print("[preflight] background music: none")
    else:
        print(
            f"[preflight] background music OK: profile={resolved_music.profile} "
            f"file={resolved_music.path}"
        )

    print("MEDIA_PREFLIGHT_RESULT=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MEDIA_PREFLIGHT_RESULT=FAIL: {exc}")
        raise
