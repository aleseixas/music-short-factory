from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import CaptionStyle, HighlightStyle
from .models import WordTiming


def write_ass_captions(
    words: tuple[WordTiming, ...],
    path: Path,
    width: int,
    height: int,
    style: CaptionStyle,
) -> None:
    groups = _group_words(words, style)
    text_color = _ass_color(style.text_color)
    active_color = _ass_color(style.active_color, alpha=False)
    outline_color = _ass_color(style.outline_color)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,{style.font_name},{style.font_size},{text_color},{text_color},{outline_color},&H00000000,{style.bold},0,0,0,100,100,0,0,1,{style.outline_size},{style.shadow_size},{style.alignment},{style.margin_left},{style.margin_right},{style.margin_bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    normal_override = _ass_color(style.text_color, alpha=False)
    for group in groups:
        split_at = _balanced_split(
            group,
            style.max_chars,
            style.balanced_split_threshold,
        )
        for active_index, word in enumerate(group):
            start = word.start
            end = group[active_index + 1].start if active_index + 1 < len(group) else group[-1].end
            if end <= start:
                continue
            rendered: list[str] = []
            for index, token in enumerate(group):
                if split_at is not None and index == split_at:
                    rendered.append(r"\N")
                escaped = _escape_ass_text(token.text)
                if index == active_index:
                    rendered.append(f"{{\\c{active_color}}}{escaped}{{\\c{normal_override}}}")
                else:
                    rendered.append(escaped)
            phrase = _join_rendered_words(rendered)
            events.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Shorts,,0,0,0,,{phrase}"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def create_highlight_overlay(
    text: str,
    path: Path,
    root: Path,
    width: int,
    height: int,
    style: HighlightStyle,
) -> Path:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font_path = _find_font(root, style.font_file)
    font_size = style.font_size
    chosen_font = ImageFont.truetype(str(font_path), font_size)

    max_text_width = max(120, min(style.max_width, width - 80))
    longest_word = max(text.split(), key=len, default=text)
    while (
        font_size > style.min_font_size
        and _text_width(draw, longest_word, chosen_font)
        > max_text_width - style.horizontal_padding
    ):
        font_size -= 2
        chosen_font = ImageFont.truetype(str(font_path), font_size)

    lines = _wrap_text(
        draw,
        text,
        chosen_font,
        max_text_width - style.horizontal_padding,
    )
    line_height = max(
        draw.textbbox((0, 0), "Ag", font=chosen_font)[3] + style.line_gap - 1,
        font_size + style.line_gap,
    )
    text_width = max(_text_width(draw, line, chosen_font) for line in lines)
    box_width = min(max_text_width, text_width + style.horizontal_padding)
    box_height = len(lines) * line_height + style.vertical_padding
    left = (width - box_width) // 2
    top = style.top
    right = left + box_width
    bottom = top + box_height

    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=style.border_radius,
        fill=_rgba(style.background_color),
    )
    draw.rectangle(
        (left, top, left + style.accent_width, bottom),
        fill=_rgba(style.accent_color),
    )
    text_y = top + max(0, style.vertical_padding // 2 - 1)
    for line in lines:
        line_width = _text_width(draw, line, chosen_font)
        draw.text(
            ((width - line_width) // 2 + style.accent_width // 2, text_y),
            line,
            font=chosen_font,
            fill=_rgba(style.text_color),
        )
        text_y += line_height

    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)
    return path


def ass_time(seconds: float) -> str:
    total_centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{centiseconds:02}"


def _group_words(words: tuple[WordTiming, ...], style: CaptionStyle) -> list[list[WordTiming]]:
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for word in words:
        proposed = [*current, word]
        proposed_text = " ".join(item.text for item in proposed)
        too_many_words = len(proposed) > style.max_words
        too_many_chars = len(proposed_text) > style.max_chars
        too_long = current and word.end - current[0].start > style.max_duration
        long_gap = current and word.start - current[-1].end > style.long_gap_seconds
        prior_sentence_ended = current and current[-1].text.endswith((".", "!", "?"))
        if current and (too_many_words or too_many_chars or too_long or long_gap or prior_sentence_ended):
            groups.append(current)
            current = [word]
        else:
            current = proposed
        if word.text.endswith((".", "!", "?")):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _balanced_split(
    group: list[WordTiming],
    max_chars: int,
    split_threshold: int,
) -> int | None:
    plain = [word.text for word in group]
    if len(group) < 3 or len(" ".join(plain)) <= min(split_threshold, max_chars):
        return None
    best_index = min(
        range(1, len(group)),
        key=lambda index: abs(len(" ".join(plain[:index])) - len(" ".join(plain[index:]))),
    )
    return best_index


def _join_rendered_words(parts: list[str]) -> str:
    result = ""
    for part in parts:
        if part == r"\N":
            result = result.rstrip() + part
        else:
            if result and not result.endswith(r"\N"):
                result += " "
            result += part
    return result


def _ass_color(value: str, alpha: bool = True) -> str:
    rgba = _rgba(value)
    red, green, blue, opacity = rgba
    ass_alpha = 255 - opacity
    if alpha:
        return f"&H{ass_alpha:02X}{blue:02X}{green:02X}{red:02X}"
    return f"&H{blue:02X}{green:02X}{red:02X}&"


def _rgba(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?", value)
    if not match:
        raise RuntimeError(f"Cor hexadecimal invalida: {value}")
    rgb = match.group(1)
    alpha = match.group(2) or "FF"
    return int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16), int(alpha, 16)


def _escape_ass_text(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _find_font(root: Path, configured: str) -> Path:
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured)
        candidates.append(configured_path if configured_path.is_absolute() else root / configured_path)
    candidates.extend(
        [
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            Path(r"C:\Windows\Fonts\segoeuib.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/Library/Fonts/Arial Bold.ttf"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("Nenhuma fonte bold compativel encontrada para os destaques.")


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        proposed = f"{current} {word}".strip()
        if not current or _text_width(draw, proposed, font) <= max_width:
            current = proposed
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]
