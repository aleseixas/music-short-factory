from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from .config import StyleConfig


def artist_font_path(font_name: str) -> Path | None:
    """Find a local serif face for intimate directions, with portable fallbacks."""
    name = font_name.casefold()
    serif = any(word in name for word in ("georgia", "times", "serif", "garamond"))
    candidates = (
        (
            "C:/Windows/Fonts/georgia.ttf",
            "C:/Windows/Fonts/times.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
        )
        if serif
        else (
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        )
    )
    return next((Path(value) for value in candidates if Path(value).is_file()), None)


def apply_artist_style(
    style: StyleConfig,
    direction: Mapping[str, object] | None,
) -> StyleConfig:
    """Apply the resolved episode palette to all existing text rendering paths.

    Per-episode direction overrides have already been merged over the profile.
    Without a direction return the original style object, including in legacy
    callers that use a minimal mocked StyleConfig.
    """
    if not direction:
        return style
    cover = direction.get("cover", {})
    editing = direction.get("editing", {})
    captions = {}
    highlights = {}
    for key in ("text_color", "accent_color", "background_color"):
        if key in cover:
            highlights[key] = cover[key]
    if "text_color" in cover:
        captions["text_color"] = cover["text_color"]
    if "accent_color" in cover:
        captions["active_color"] = cover["accent_color"]
    if "font_name" in cover:
        captions["font_name"] = cover["font_name"]
        font = artist_font_path(str(cover["font_name"]))
        if font is not None:
            highlights["font_file"] = str(font)
    transitions = style.transitions
    if "crossfade_seconds" in editing:
        transitions = replace(
            transitions,
            crossfade_seconds=float(editing["crossfade_seconds"]),
        )
    return replace(
        style,
        captions=replace(style.captions, **captions),
        highlights=replace(style.highlights, **highlights),
        transitions=transitions,
    )
