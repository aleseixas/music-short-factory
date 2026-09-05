from __future__ import annotations

import argparse
from pathlib import Path
import sys
from collections.abc import Sequence

from engine.config import load_project_config
from engine.duplicates import find_duplicate_candidate, format_duplicate
from engine.episode import create_episode
from publishing.metadata import create_post_template


def main(
    argv: Sequence[str] | None = None,
    default_project_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Cria os templates de um episodio novo.")
    parser.add_argument("episode", help="Slug do episodio (ex.: my_eyes).")
    parser.add_argument("--song", default="", help="Nome da musica para checagem de duplicidade.")
    parser.add_argument("--artist", default="", help="Artista para checagem de duplicidade.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root or Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    try:
        duplicate = find_duplicate_candidate(
            project_root,
            song=args.song,
            artist=args.artist,
            slug=args.episode,
        )
        if duplicate is not None:
            print(f"ERRO: {format_duplicate(duplicate)}", file=sys.stderr)
            print(
                "Descarte somente esta candidata e avance para a proxima musica do pool.",
                file=sys.stderr,
            )
            return 2

        config = load_project_config(project_root / "config" / "config.json")
        destination = create_episode(
            project_root,
            config.paths.episodes_dir,
            args.episode,
        )
        create_post_template(destination)
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    print(f"Episodio criado em: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(default_project_root=Path(__file__).resolve().parent))
