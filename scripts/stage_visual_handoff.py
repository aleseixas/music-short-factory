from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def main() -> int:
    root = Path.cwd()
    slug = str(os.environ.get("EPISODE") or "").strip()
    if not slug:
        raise RuntimeError("EPISODE nao definido para o handoff visual.")

    episode_rel = Path("episodes") / slug
    episode_dir = root / episode_rel
    if not episode_dir.is_dir():
        raise RuntimeError(f"Diretorio do episodio nao encontrado: {episode_rel}")

    handoff_root = root / ".visual-handoff"
    handoff_episode = handoff_root / episode_rel
    handoff_episode.mkdir(parents=True, exist_ok=True)

    copied: set[Path] = set()

    for name in ("assets.json", "timeline.json", "visual_resolution_report.json"):
        source = episode_dir / name
        if not source.is_file():
            continue
        destination = handoff_episode / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.add(source)

    changed = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--modified",
            "--exclude-standard",
            "-z",
            "--",
            str(episode_rel / "assets"),
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.decode("utf-8", errors="surrogateescape")

    for raw in changed.split("\0"):
        if not raw:
            continue
        rel = Path(raw)
        source = root / rel
        if not source.is_file():
            continue
        destination = handoff_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.add(source)
        print(f"Handoff media: {rel}")

    print(f"Visual handoff preparado com {len(copied)} arquivo(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
