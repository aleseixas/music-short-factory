from __future__ import annotations

import argparse
from pathlib import Path
import sys

from engine.duplicates import DUPLICATE_CODE, find_duplicate_candidate, format_duplicate, infer_identity_from_story


def main() -> int:
    parser = argparse.ArgumentParser(description="Detecta episodio/candidata musical duplicada.")
    parser.add_argument("--song", default="", help="Nome da musica candidata.")
    parser.add_argument("--artist", default="", help="Artista da musica candidata.")
    parser.add_argument("--slug", default="", help="Slug provavel da candidata.")
    parser.add_argument(
        "--episode",
        default="",
        help="Slug de episodio ja criado; infere musica/artista do story.json e ignora o proprio slug.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    root = args.project_root.resolve()

    song = args.song.strip()
    artist = args.artist.strip()
    slug = args.slug.strip()
    exclude_slug: str | None = None

    if args.episode:
        episode = args.episode.strip()
        story_path = root / "episodes" / episode / "story.json"
        if not story_path.is_file():
            print(f"ERRO: story.json nao encontrado para {episode!r}.", file=sys.stderr)
            return 1
        inferred_song, inferred_artist = infer_identity_from_story(story_path)
        song = song or inferred_song
        artist = artist or inferred_artist
        slug = slug or episode
        exclude_slug = episode

    if not slug and not (song and artist):
        print("ERRO: informe --slug ou --song + --artist.", file=sys.stderr)
        return 1

    match = find_duplicate_candidate(
        root,
        song=song,
        artist=artist,
        slug=slug,
        exclude_slug=exclude_slug,
    )
    if match is not None:
        print(format_duplicate(match))
        return 2

    print(f"CANDIDATE_UNIQUE: song={song or 'N/A'}; artist={artist or 'N/A'}; slug={slug or 'N/A'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
