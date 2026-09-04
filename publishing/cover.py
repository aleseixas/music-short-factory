from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


def generate_cover(
    project_root: Path,
    episode_dir: Path,
    post: Mapping[str, Any],
    output_path: Path,
) -> Path:
    config = _load_json(project_root / "config" / "config.json")
    style = _load_json(project_root / "config" / "style.json")
    render = config.get("render", {})
    try:
        width = int(render.get("width", 720))
        height = int(render.get("height", 1280))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Resolucao invalida em config/config.json.") from exc
    if width <= 0 or height <= 0:
        raise RuntimeError("Resolucao da capa precisa ser positiva.")

    cover = post["cover"]
    headline = str(cover["headline"]).strip()
    source = cover["source"]
    source_path, default_focus = _resolve_source(
        episode_dir, source, output_path.parent
    )
    focus = cover.get("focus", {})
    focus_x = float(focus.get("x", default_focus[0]))
    focus_y = float(focus.get("y", default_focus[1]))

    try:
        with Image.open(source_path) as opened:
            opened.verify()
        with Image.open(source_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            canvas = ImageOps.fit(
                image,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(focus_x, focus_y),
            )
    except (UnidentifiedImageError, OSError) as exc:
        raise RuntimeError(f"Fonte da capa nao e uma imagem valida: {source_path}") from exc
    finally:
        if source.get("type") == "video_frame":
            source_path.unlink(missing_ok=True)

    _draw_headline(canvas, headline, style, project_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    canvas.save(temporary, format="JPEG", quality=94, subsampling=0, optimize=True)
    if temporary.stat().st_size > 2 * 1024 * 1024:
        canvas.save(temporary, format="JPEG", quality=86, optimize=True)
    temporary.replace(output_path)
    return output_path


def _resolve_source(
    episode_dir: Path,
    source: Mapping[str, Any],
    temporary_dir: Path,
) -> tuple[Path, tuple[float, float]]:
    if source.get("type") == "asset":
        catalog = _load_json(episode_dir / "assets.json")
        raw_assets = catalog.get("assets")
        if not isinstance(raw_assets, list):
            raise RuntimeError("assets.json precisa conter uma lista 'assets'.")
        asset_id = str(source.get("asset_id", ""))
        match = next(
            (
                item
                for item in raw_assets
                if isinstance(item, dict) and item.get("id") == asset_id
            ),
            None,
        )
        if match is None:
            raise RuntimeError(
                f"Asset da capa {asset_id!r} nao existe em {episode_dir / 'assets.json'}."
            )
        file_name = str(match.get("file", ""))
        if not file_name or Path(file_name).name != file_name:
            raise RuntimeError(f"Nome de arquivo inseguro no asset da capa {asset_id!r}.")
        assets_dir = (episode_dir / "assets").resolve()
        path = (assets_dir / file_name).resolve()
        try:
            path.relative_to(assets_dir)
        except ValueError as exc:
            raise RuntimeError(f"Asset da capa fora da pasta permitida: {path}") from exc
        if not path.is_file():
            raise RuntimeError(
                f"Arquivo do asset da capa ausente: {path}. Gere o episodio ou obtenha o asset primeiro."
            )
        focus = match.get("focus", {})
        if not isinstance(focus, Mapping):
            focus = {}
        return path, (float(focus.get("x", 0.5)), float(focus.get("y", 0.5)))

    timestamp = float(source["timestamp_seconds"])
    output_root = temporary_dir.resolve()
    video_path = (output_root / f"{episode_dir.name}.mp4").resolve()
    try:
        video_path.relative_to(output_root)
    except ValueError as exc:
        raise RuntimeError(f"Video da capa fora da pasta permitida: {video_path}") from exc
    if not video_path.is_file():
        raise RuntimeError(
            f"Video necessario para cover.source=video_frame esta ausente: {video_path}"
        )
    temporary_dir.mkdir(parents=True, exist_ok=True)
    frame_path = temporary_dir / f".{episode_dir.name}_cover_frame.png"
    _extract_frame(video_path, timestamp, frame_path)
    return frame_path, (0.5, 0.5)


def _extract_frame(video_path: Path, timestamp: float, frame_path: Path) -> None:
    try:
        import imageio_ffmpeg
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Dependencia imageio-ffmpeg ausente. Execute: python -m pip install -r requirements.txt"
        ) from exc
    frame_path.unlink(missing_ok=True)
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-y",
        str(frame_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not frame_path.is_file():
        detail = "\n".join(result.stderr.splitlines()[-8:])
        raise RuntimeError(
            f"Nao foi possivel extrair o frame {timestamp:.3f}s de {video_path}: {detail}"
        )


def _draw_headline(
    image: Image.Image,
    headline: str,
    style: Mapping[str, Any],
    project_root: Path,
) -> None:
    width, height = image.size
    highlights = style.get("highlights", {})
    if not isinstance(highlights, Mapping):
        highlights = {}
    text_color = _rgba(str(highlights.get("text_color", "#FFFFFF")), 255)
    accent_color = _rgba(str(highlights.get("accent_color", "#FFD43B")), 255)
    background = _rgba(str(highlights.get("background_color", "#101010DC")), 220)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    gradient_start = int(height * 0.49)
    for y in range(gradient_start, height):
        progress = (y - gradient_start) / max(1, height - gradient_start)
        alpha = int(210 * (progress**0.7))
        draw.line((0, y, width, y), fill=(0, 0, 0, alpha))

    max_width = int(width * 0.80)
    font_path = _font_path(highlights, project_root)
    base_size = max(58, int(float(highlights.get("font_size", 48)) * 1.45))
    font, lines = _fit_text(draw, headline, font_path, base_size, max_width)
    line_gap = max(8, int(font.size * 0.16))
    boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=0) for line in lines]
    line_heights = [box[3] - box[1] for box in boxes]
    block_height = sum(line_heights) + line_gap * (len(lines) - 1)

    # Keep the whole headline panel in the visual center of a vertical short.
    # This avoids platform-specific top/bottom crops and UI overlays hiding the title.
    x = (width - max_width) // 2
    y = (height - block_height) // 2
    padding_x = int(width * 0.035)
    padding_y = int(height * 0.025)
    draw.rounded_rectangle(
        (
            x - padding_x,
            y - padding_y,
            x + max_width + padding_x,
            y + block_height + padding_y,
        ),
        radius=max(10, int(width * 0.018)),
        fill=background,
    )
    accent_width = max(7, int(width * 0.012))
    draw.rounded_rectangle(
        (
            x - padding_x,
            y - padding_y,
            x - padding_x + accent_width,
            y + block_height + padding_y,
        ),
        radius=accent_width // 2,
        fill=accent_color,
    )
    current_y = y
    stroke = max(1, int(font.size * 0.035))
    for line, line_height, box in zip(lines, line_heights, boxes):
        line_width = box[2] - box[0]
        line_x = x + (max_width - line_width) // 2 - box[0]
        draw.text(
            (line_x, current_y),
            line,
            font=font,
            fill=text_color,
            stroke_width=stroke,
            stroke_fill=(0, 0, 0, 210),
        )
        current_y += line_height + line_gap
    image.paste(overlay, (0, 0), overlay)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path | None,
    start_size: int,
    max_width: int,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str]]:
    for size in range(start_size, 39, -2):
        font = _load_font(font_path, size)
        lines = _wrap_words(draw, text, font, max_width)
        if len(lines) <= 3:
            return font, lines
    font = _load_font(font_path, 40)
    return font, _wrap_words(draw, text, font, max_width)


def _wrap_words(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font)
        if current and box[2] - box[0] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _font_path(highlights: Mapping[str, Any], project_root: Path) -> Path | None:
    configured = str(highlights.get("font_file", "")).strip()
    candidates: list[Path] = []
    if configured:
        path = Path(configured)
        candidates.append(path if path.is_absolute() else project_root / path)
    candidates.extend(
        [
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/Arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ]
    )
    return next((path for path in candidates if path.is_file()), None)


def _load_font(path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path is not None:
        return ImageFont.truetype(str(path), size=size)
    try:
        return ImageFont.truetype("Arial Bold", size=size)
    except OSError:
        return ImageFont.load_default(size=size)


def _rgba(value: str, default_alpha: int) -> tuple[int, int, int, int]:
    try:
        parsed = ImageColor.getrgb(value)
    except ValueError:
        parsed = (255, 255, 255)
    if len(parsed) == 4:
        return parsed
    return parsed[0], parsed[1], parsed[2], default_alpha


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Arquivo obrigatorio ausente: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"JSON invalido em {path} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"O arquivo {path} precisa conter um objeto JSON.")
    return data
