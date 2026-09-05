from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata


DUPLICATE_CODE = "DUPLICATE_CANDIDATE"


@dataclass(frozen=True)
class DuplicateMatch:
    kind: str
    path: str
    slug: str
    reason: str


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_like = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", ascii_like.casefold()))


def _story_payload(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _story_corpus(path: Path, slug: str) -> str:
    data = _story_payload(path)
    title = str(data.get("title") or "")
    segments = data.get("segments")
    first_text = ""
    if isinstance(segments, list) and segments and isinstance(segments[0], dict):
        first_text = str(segments[0].get("text") or "")
    return normalize_text(f"{slug} {title} {first_text}")


def infer_identity_from_story(path: Path) -> tuple[str, str]:
    data = _story_payload(path)
    title = str(data.get("title") or "").strip()
    if title:
        parts = re.split(r"\s+[—–-]\s+", title, maxsplit=1)
        if len(parts) == 2:
            left, right = parts
            right = re.split(r"[:|]", right, maxsplit=1)[0]
            if left.strip() and right.strip():
                # Most current episodes use "Artist — Song: framing".
                return right.strip(), left.strip()

    segments = data.get("segments")
    if isinstance(segments, list) and segments and isinstance(segments[0], dict):
        text = str(segments[0].get("text") or "").strip()
        match = re.match(
            r"^(.+?),\s+(?:de|da|do)\s+(.+?)(?:,|\.|:|;|\s+nao\b|\s+não\b)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).strip(), match.group(2).strip()
    return "", ""


def _slug_from_retry_name(stem: str) -> str:
    return re.sub(r"-retry-[12]$", "", stem)


def find_duplicate_candidate(
    project_root: Path,
    *,
    song: str,
    artist: str,
    slug: str,
    exclude_slug: str | None = None,
) -> DuplicateMatch | None:
    root = project_root.resolve()
    candidate_slug = normalize_text(slug)
    song_norm = normalize_text(song)
    artist_norm = normalize_text(artist)
    exclude_norm = normalize_text(exclude_slug or "")

    episodes_root = root / "episodes"
    if episodes_root.is_dir():
        for episode_dir in sorted(path for path in episodes_root.iterdir() if path.is_dir()):
            existing_slug = episode_dir.name
            existing_norm = normalize_text(existing_slug)
            if exclude_norm and existing_norm == exclude_norm:
                continue
            if candidate_slug and existing_norm == candidate_slug:
                return DuplicateMatch(
                    "episode",
                    f"episodes/{existing_slug}",
                    existing_slug,
                    "exact_slug",
                )

            story_path = episode_dir / "story.json"
            if not story_path.is_file():
                continue
            existing_song, existing_artist = infer_identity_from_story(story_path)
            if (
                song_norm
                and artist_norm
                and normalize_text(existing_song) == song_norm
                and normalize_text(existing_artist) == artist_norm
            ):
                return DuplicateMatch(
                    "episode",
                    f"episodes/{existing_slug}",
                    existing_slug,
                    "same_song_and_artist",
                )

            corpus = _story_corpus(story_path, existing_slug)
            if song_norm and artist_norm and song_norm in corpus and artist_norm in corpus:
                return DuplicateMatch(
                    "episode",
                    f"episodes/{existing_slug}",
                    existing_slug,
                    "song_and_artist_present_in_story",
                )

    for directory_name, kind in ((".publish-queue", "queue"), (".publish-retry", "retry")):
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for trigger in sorted(directory.glob("*.txt")):
            trigger_slug = (
                _slug_from_retry_name(trigger.stem) if kind == "retry" else trigger.stem
            )
            if exclude_norm and normalize_text(trigger_slug) == exclude_norm:
                continue
            if candidate_slug and normalize_text(trigger_slug) == candidate_slug:
                return DuplicateMatch(
                    kind,
                    f"{directory_name}/{trigger.name}",
                    trigger_slug,
                    "existing_publish_trigger",
                )

    return None


def format_duplicate(match: DuplicateMatch) -> str:
    return (
        f"{DUPLICATE_CODE}: kind={match.kind}; path={match.path}; "
        f"slug={match.slug}; reason={match.reason}"
    )
