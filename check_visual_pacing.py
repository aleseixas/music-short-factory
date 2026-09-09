from __future__ import annotations

import argparse
from pathlib import Path

from engine.config import load_project_config
from engine.episode import load_episode
from engine.visual_pacing import assert_estimated_visual_pacing


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Smart Visual Pacing before episode media preflight."
    )
    parser.add_argument("episode", help="Episode slug")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    config = load_project_config(root / "config" / "config.json")
    episode = load_episode(root, config.paths.episodes_dir, args.episode)

    print("[pacing] validating estimated visual pacing...")
    report = assert_estimated_visual_pacing(episode)
    for warning in report.warnings:
        print(f"[pacing] aviso {warning.code}: {warning.message}")

    print("VISUAL_PACING_PREFLIGHT_RESULT=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"VISUAL_PACING_PREFLIGHT_RESULT=FAIL: {exc}")
        raise
