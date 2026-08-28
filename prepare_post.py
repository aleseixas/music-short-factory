from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from collections.abc import Sequence

from publishing.metadata import prepare_episode_post


def main(
    argv: Sequence[str] | None = None,
    default_project_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Valida post.json, prepara previews e gera a capa do episodio."
    )
    parser.add_argument("episode", help="Slug do episodio (ex.: duckworth).")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root or Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        prepared = prepare_episode_post(args.project_root.resolve(), args.episode)
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    print(f"Post preparado: {prepared.post_path}")
    print(f"Capa gerada: {prepared.cover_path}")
    print("\nPREVIEWS (edite post.json e execute novamente quando quiser):")
    print(json.dumps(prepared.previews, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(default_project_root=Path(__file__).resolve().parent))
