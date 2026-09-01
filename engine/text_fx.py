from __future__ import annotations

from pathlib import Path

from .captions import _ass_color, _escape_ass_text, ass_time
from .config import CaptionStyle, HighlightStyle
from .models import TextFxCue


def write_text_fx_ass(
    cues: tuple[TextFxCue, ...],
    path: Path,
    width: int,
    height: int,
    captions: CaptionStyle,
    highlights: HighlightStyle,
    video_duration: float,
) -> None:
    """Write a separate, centered ASS layer for editorial kinetic text.

    This intentionally stays separate from word captions: highlights live at the
    top of each shot, text FX occupies the safe center, and captions render last
    in the lower third.
    """
    text_color = _ass_color(captions.text_color)
    outline_color = _ass_color(captions.outline_color)
    accent_color = _ass_color(highlights.accent_color, alpha=False)
    base_size = max(captions.font_size + 34, round(height * 0.07))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    styles: list[str] = []
    events: list[str] = []
    for index, cue in enumerate(cues, start=1):
        start = cue.start_seconds
        end = min(cue.end_seconds, video_duration)
        if start >= video_duration or end <= start:
            print(
                f"[text_fx] aviso: cue {index} ({cue.animation}) ignorada; "
                "intervalo fora do video."
            )
            continue
        font_size = _font_size(cue.text, width, height, base_size)
        style_name = f"Kinetic{index}"
        outline = max(3, round(font_size * 0.065))
        shadow = max(1, round(font_size * 0.028))
        styles.append(
            f"Style: {style_name},{captions.font_name},{font_size},{text_color},{text_color},"
            f"{outline_color},&H50000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},5,"
            "54,54,0,1"
        )
        x, y = width // 2, round(height * 0.46)
        tags = _animation_tags(cue, x, y, height)
        text = _render_text(cue, style_name, accent_color, font_size)
        events.append(
            f"Dialogue: 1,{ass_time(start)},{ass_time(end)},{style_name},,0,0,0,,{tags}{text}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        header.replace("\n[Events]", "\n" + "\n".join(styles) + "\n[Events]")
        + "\n".join(events)
        + "\n",
        encoding="utf-8-sig",
    )


def _font_size(text: str, width: int, height: int, base_size: int) -> int:
    lines = text.splitlines() or [text]
    longest = max((len(line) for line in lines), default=1)
    # Conservative character-width estimate leaves 10% on each horizontal side.
    by_width = max(round(height * 0.032), round((width * 0.80) / max(1, longest * 0.62)))
    by_height = max(round(height * 0.032), round((height * 0.32) / max(1, len(lines) * 1.25)))
    return min(base_size, by_width, by_height)


def _animation_tags(cue: TextFxCue, x: int, y: int, height: int) -> str:
    duration_ms = max(1, round((cue.end_seconds - cue.start_seconds) * 1000))
    enter = min(240, max(80, round(duration_ms * 0.28)))
    exit_duration = min(180, max(80, round(duration_ms * 0.18)), max(1, duration_ms // 3))
    intensity = cue.intensity
    fade_in = min(120, enter)
    common = f"{{\\an5\\pos({x},{y})\\fad({fade_in},{exit_duration})"
    if cue.animation == "pop_in":
        start = round(88 - 12 * intensity)
        peak = round(102 + 6 * intensity)
        settle = min(duration_ms - 1, enter * 2)
        return (
            f"{common}\\fscx{start}\\fscy{start}"
            f"\\t(0,{enter},\\fscx{peak}\\fscy{peak})"
            f"\\t({enter},{settle},\\fscx100\\fscy100)}}"
        )
    if cue.animation == "scale_bounce":
        start = round(80 - 10 * intensity)
        peak = round(106 + 6 * intensity)
        undershoot = round(98 - 2 * intensity)
        middle = min(duration_ms - 1, enter * 2)
        settle = min(duration_ms - 1, enter * 3)
        return (
            f"{common}\\fscx{start}\\fscy{start}"
            f"\\t(0,{enter},\\fscx{peak}\\fscy{peak})"
            f"\\t({enter},{middle},\\fscx{undershoot}\\fscy{undershoot})"
            f"\\t({middle},{settle},\\fscx100\\fscy100)}}"
        )
    if cue.animation == "slide_up":
        offset = max(10, round((24 + 36 * intensity) * height / 1280))
        return f"{{\\an5\\move({x},{y + offset},{x},{y},0,{enter})\\fad({fade_in},{exit_duration})}}"
    start = round(97 - 5 * intensity)
    return (
        f"{common}\\fscx{start}\\fscy{start}"
        f"\\t(0,{enter},\\fscx100\\fscy100)}}"
    )


def _render_text(cue: TextFxCue, style_name: str, accent_color: str, font_size: int) -> str:
    text = cue.text
    if cue.accent_text:
        lower = text.casefold()
        index = lower.index(cue.accent_text.casefold())
        before = _escape_ass_text(text[:index]).replace("\n", r"\N")
        accent = _escape_ass_text(text[index:index + len(cue.accent_text)]).replace("\n", r"\N")
        after = _escape_ass_text(text[index + len(cue.accent_text):]).replace("\n", r"\N")
        accent_size = round(font_size * 1.12)
        return f"{before}{{\\c{accent_color}\\fs{accent_size}}}{accent}{{\\r{style_name}}}{after}"
    return _escape_ass_text(text).replace("\n", r"\N")
