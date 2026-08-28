from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from collections.abc import Sequence


def main(
    argv: Sequence[str] | None = None,
    default_project_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Gera um video vertical a partir de um episodio declarativo."
    )
    parser.add_argument("episode", help="Nome da pasta em episodes/ (ex.: through_the_wire).")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root or Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    try:
        from .pipeline import build_video

        asyncio.run(build_video(args.project_root, args.episode))
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    return 0
