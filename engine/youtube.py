"""Canonical, case-sensitive YouTube identity shared by acquisition and preflight."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit


YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
})
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")


def is_youtube_video_id(value: object) -> bool:
    return isinstance(value, str) and _VIDEO_ID.fullmatch(value) is not None


def extract_youtube_video_id(url: str) -> str | None:
    """Ignore tracking/time parameters, never the case-sensitive ``v`` value."""
    try:
        parsed = urlsplit(str(url or "").strip())
        host = (parsed.hostname or "").casefold()
        if (parsed.scheme.casefold() not in {"http", "https"}
                or host not in YOUTUBE_HOSTS or parsed.username or parsed.password):
            return None
        parts = parsed.path.strip("/").split("/")
        if host in {"youtu.be", "www.youtu.be"}:
            video_id = parts[0] if len(parts) == 1 else ""
        elif parsed.path.rstrip("/") == "/watch":
            values = parse_qs(parsed.query).get("v", [])
            video_id = values[0] if len(set(values)) == 1 else ""
        elif len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
            video_id = parts[1]
        else:
            return None
    except (TypeError, ValueError):
        return None
    return video_id if is_youtube_video_id(video_id) else None


def canonical_youtube_url(url: str) -> str | None:
    video_id = extract_youtube_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
