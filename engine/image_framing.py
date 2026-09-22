from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageChops, ImageFilter, ImageOps


FramingMode = Literal["smart_crop", "contain_neutral"]

ANALYSIS_MAX_SIDE = 320
MAX_DOWNSTREAM_ZOOM = 1.15
MIN_GENTLE_CROP_FRACTION = 0.84
MIN_RETAINED_IMPORTANCE = 0.82
MIN_SAFE_RETAINED_IMPORTANCE = 0.72
CONTAIN_FOREGROUND_SCALE = 0.84
MIN_CROP_FRACTION_FOR_COVER = 0.58
NEUTRAL_BACKGROUND_RGB = (11, 15, 20)
NEUTRAL_BACKGROUND_HEX = "0x0b0f14"


@dataclass(frozen=True)
class VerticalFramingDecision:
    mode: FramingMode
    crop_box: tuple[float, float, float, float] | None
    retained_importance: float
    safe_retained_importance: float
    crop_fraction: float


@dataclass(frozen=True)
class _CropCandidate:
    start_ratio: float
    retained_importance: float
    safe_retained_importance: float
    score: float


def prepare_vertical_image(
    image: Image.Image,
    target_size: tuple[int, int],
    focus_x: float,
    focus_y: float,
) -> tuple[Image.Image, VerticalFramingDecision]:
    """Frame one still image for a vertical canvas without stretching it."""
    target_width, target_height = target_size
    _validate_inputs(target_width, target_height, focus_x, focus_y)

    normalized = ImageOps.exif_transpose(image).convert("RGB")
    decision = _analyze_normalized_vertical_framing(
        normalized,
        target_size,
        focus_x,
        focus_y,
    )
    if decision.mode == "smart_crop":
        if decision.crop_box is None:  # Defensive: mode and geometry are inseparable.
            raise RuntimeError("Crop inteligente sem geometria valida.")
        framed = normalized.resize(
            target_size,
            Image.Resampling.LANCZOS,
            box=decision.crop_box,
        )
        return framed, decision

    background = _neutral_background(target_size)

    foreground_bounds = (
        max(1, round(target_width * CONTAIN_FOREGROUND_SCALE)),
        max(1, round(target_height * CONTAIN_FOREGROUND_SCALE)),
    )
    foreground = ImageOps.contain(
        normalized,
        foreground_bounds,
        method=Image.Resampling.LANCZOS,
    )
    position = (
        (target_width - foreground.width) // 2,
        (target_height - foreground.height) // 2,
    )
    background.paste(foreground, position)
    return background, decision


def analyze_vertical_framing(
    image: Image.Image,
    target_size: tuple[int, int],
    focus_x: float,
    focus_y: float,
) -> VerticalFramingDecision:
    """Choose a conservative content-aware crop or a full-image fallback."""
    target_width, target_height = target_size
    _validate_inputs(target_width, target_height, focus_x, focus_y)
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    return _analyze_normalized_vertical_framing(
        normalized,
        target_size,
        focus_x,
        focus_y,
    )


def _analyze_normalized_vertical_framing(
    normalized: Image.Image,
    target_size: tuple[int, int],
    focus_x: float,
    focus_y: float,
) -> VerticalFramingDecision:
    target_width, target_height = target_size
    source_width, source_height = normalized.size
    if source_width < 1 or source_height < 1:
        raise RuntimeError("Imagem sem dimensoes validas para enquadramento vertical.")

    crop_width, crop_height = _largest_crop(
        source_width,
        source_height,
        target_width,
        target_height,
    )
    crop_fraction = crop_fraction_for_target(
        (source_width, source_height),
        target_size,
    )
    importance = _build_importance_map(normalized)
    analysis_width, analysis_height = importance.size
    target_ratio = target_width / target_height
    horizontal_crop = analysis_width / analysis_height > target_ratio
    if horizontal_crop:
        analysis_crop_length = max(1, round(analysis_height * target_ratio))
        full_length = analysis_width
        crop_length = min(analysis_crop_length, full_length)
        focus_axis = focus_x
    else:
        analysis_crop_length = max(1, round(analysis_width / target_ratio))
        full_length = analysis_height
        crop_length = min(analysis_crop_length, full_length)
        focus_axis = focus_y

    candidate = _choose_crop_candidate(
        importance,
        horizontal_crop=horizontal_crop,
        crop_length=crop_length,
        focus_axis=focus_axis,
        focus_x=focus_x,
        focus_y=focus_y,
    )
    low_information = (
        _importance_mass(importance) < importance.width * importance.height * 0.5
    )
    safe_crop = (
        crop_fraction >= 0.995
        or (low_information and crop_fraction >= MIN_GENTLE_CROP_FRACTION)
        or (
            not low_information
            and candidate.retained_importance >= MIN_RETAINED_IMPORTANCE
            and candidate.safe_retained_importance
            >= MIN_SAFE_RETAINED_IMPORTANCE
        )
    )
    if not safe_crop:
        return VerticalFramingDecision(
            mode="contain_neutral",
            crop_box=None,
            retained_importance=candidate.retained_importance,
            safe_retained_importance=candidate.safe_retained_importance,
            crop_fraction=crop_fraction,
        )

    if horizontal_crop:
        source_travel = source_width - crop_width
        left = candidate.start_ratio * source_travel
        top = 0.0
    else:
        source_travel = source_height - crop_height
        left = 0.0
        top = candidate.start_ratio * source_travel
    crop_box = (left, top, left + crop_width, top + crop_height)
    return VerticalFramingDecision(
        mode="smart_crop",
        crop_box=crop_box,
        retained_importance=candidate.retained_importance,
        safe_retained_importance=candidate.safe_retained_importance,
        crop_fraction=crop_fraction,
    )


def crop_fraction_for_target(
    source_size: tuple[int, int],
    target_size: tuple[int, int],
) -> float:
    """Return the fraction of source area retained by a cover crop."""
    source_width, source_height = source_size
    target_width, target_height = target_size
    if min(source_width, source_height, target_width, target_height) < 1:
        raise RuntimeError("Dimensoes invalidas para calcular enquadramento.")
    crop_width, crop_height = _largest_crop(
        source_width,
        source_height,
        target_width,
        target_height,
    )
    return (crop_width * crop_height) / (source_width * source_height)


def _neutral_background(target_size: tuple[int, int]) -> Image.Image:
    """Build the shared dark neutral contain background without blur."""
    return Image.new("RGB", target_size, NEUTRAL_BACKGROUND_RGB)


def _validate_inputs(
    target_width: int,
    target_height: int,
    focus_x: float,
    focus_y: float,
) -> None:
    if target_width < 1 or target_height < 1:
        raise RuntimeError("Resolucao de enquadramento precisa ser positiva.")
    if not 0 <= focus_x <= 1 or not 0 <= focus_y <= 1:
        raise RuntimeError("Foco da imagem precisa ficar entre 0 e 1.")


def _largest_crop(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
) -> tuple[float, float]:
    target_ratio = target_width / target_height
    source_ratio = source_width / source_height
    if source_ratio > target_ratio:
        return source_height * target_ratio, float(source_height)
    return float(source_width), source_width / target_ratio


def _build_importance_map(image: Image.Image) -> Image.Image:
    preview = image.copy()
    preview.thumbnail(
        (ANALYSIS_MAX_SIDE, ANALYSIS_MAX_SIDE),
        Image.Resampling.LANCZOS,
    )
    if min(preview.size) < 3:
        return Image.new("L", preview.size, 0)
    grayscale = ImageOps.grayscale(preview)
    radius = max(1.0, min(preview.size) / 36)
    smooth_gray = grayscale.filter(ImageFilter.GaussianBlur(radius))
    smooth_color = preview.filter(ImageFilter.GaussianBlur(radius))

    local_luma = ImageChops.difference(grayscale, smooth_gray)
    local_color = ImageChops.difference(preview, smooth_color).convert("L")
    edges = grayscale.filter(ImageFilter.FIND_EDGES)
    border = min(2, max(0, min(preview.size) // 4))
    if border:
        edges.paste(0, (0, 0, edges.width, border))
        edges.paste(0, (0, edges.height - border, edges.width, edges.height))
        edges.paste(0, (0, 0, border, edges.height))
        edges.paste(0, (edges.width - border, 0, edges.width, edges.height))

    corner_pixels = (
        preview.getpixel((0, 0)),
        preview.getpixel((preview.width - 1, 0)),
        preview.getpixel((0, preview.height - 1)),
        preview.getpixel((preview.width - 1, preview.height - 1)),
    )
    background_color = tuple(
        round(sum(pixel[channel] for pixel in corner_pixels) / len(corner_pixels))
        for channel in range(3)
    )
    background = Image.new("RGB", preview.size, background_color)
    foreground_difference = ImageChops.difference(preview, background).convert("L")

    combined = ImageChops.add(
        edges.point(_scaled_lut(0.65)),
        local_luma.point(_scaled_lut(1.2)),
    )
    combined = ImageChops.add(
        combined,
        local_color.point(_scaled_lut(0.8)),
    )
    combined = ImageChops.add(
        combined,
        foreground_difference.point(_scaled_lut(0.35)),
    )

    threshold = _importance_threshold(combined)
    return combined.point(
        [
            0 if value < threshold else min(255, (value - threshold) * 3 + 24)
            for value in range(256)
        ]
    )


def _scaled_lut(scale: float) -> list[int]:
    return [min(255, round(value * scale)) for value in range(256)]


def _importance_threshold(image: Image.Image) -> int:
    histogram = image.histogram()
    nonzero = sum(histogram[1:])
    if nonzero == 0:
        return 255
    target = nonzero * 0.62
    cumulative = 0
    for value in range(1, 256):
        cumulative += histogram[value]
        if cumulative >= target:
            return max(8, value)
    return 255


def _choose_crop_candidate(
    importance: Image.Image,
    *,
    horizontal_crop: bool,
    crop_length: int,
    focus_axis: float,
    focus_x: float,
    focus_y: float,
) -> _CropCandidate:
    full_length = importance.width if horizontal_crop else importance.height
    travel = max(0, full_length - crop_length)
    anchor = round(
        min(
            max(0.0, focus_axis * full_length - crop_length / 2),
            float(travel),
        )
    )
    if travel == 0:
        starts = [0]
    else:
        step = max(1, travel // 32)
        starts = list(range(0, travel + 1, step))
        starts.extend((anchor, travel))

    margin = max(1, round(crop_length * 0.10))
    focus_position = focus_axis * max(0, full_length - 1)
    protected_focus = min(
        max(focus_position, margin),
        max(margin, full_length - margin),
    )
    eligible = sorted(
        {
            min(max(0, start), travel)
            for start in starts
            if start + margin <= protected_focus <= start + crop_length - margin
        }
    )
    if not eligible:
        eligible = [min(max(0, anchor), travel)]

    total_mass = _importance_mass(importance)
    if total_mass <= 0:
        return _CropCandidate(
            start_ratio=anchor / travel if travel else 0.0,
            retained_importance=1.0,
            safe_retained_importance=1.0,
            score=1.0,
        )

    best: _CropCandidate | None = None
    for start in eligible:
        crop_box = _axis_crop_box(
            importance.size,
            horizontal_crop,
            start,
            crop_length,
        )
        retained = _importance_mass(importance.crop(crop_box)) / total_mass
        safe_box = _safe_box(
            crop_box,
            importance.size,
            focus_x,
            focus_y,
        )
        safe_retained = _importance_mass(importance.crop(safe_box)) / total_mass
        anchor_distance = abs(start - anchor) / travel if travel else 0.0
        score = 0.68 * retained + 0.32 * safe_retained - 0.06 * anchor_distance
        candidate = _CropCandidate(
            start_ratio=start / travel if travel else 0.0,
            retained_importance=retained,
            safe_retained_importance=safe_retained,
            score=score,
        )
        if best is None or candidate.score > best.score:
            best = candidate

    if best is None:  # Defensive; eligible always contains at least one item.
        raise RuntimeError("Nao foi possivel avaliar o crop vertical.")
    return best


def _axis_crop_box(
    size: tuple[int, int],
    horizontal_crop: bool,
    start: int,
    crop_length: int,
) -> tuple[int, int, int, int]:
    width, height = size
    if horizontal_crop:
        return start, 0, start + crop_length, height
    return 0, start, width, start + crop_length


def _safe_box(
    crop_box: tuple[int, int, int, int],
    size: tuple[int, int],
    focus_x: float,
    focus_y: float,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = crop_box
    crop_width = right - left
    crop_height = bottom - top
    safe_width = max(1, round(crop_width / MAX_DOWNSTREAM_ZOOM))
    safe_height = max(1, round(crop_height / MAX_DOWNSTREAM_ZOOM))
    focus_pixel_x = focus_x * max(0, size[0] - 1)
    focus_pixel_y = focus_y * max(0, size[1] - 1)
    safe_left = round(focus_pixel_x - safe_width / 2)
    safe_top = round(focus_pixel_y - safe_height / 2)
    safe_left = min(max(left, safe_left), right - safe_width)
    safe_top = min(max(top, safe_top), bottom - safe_height)
    return safe_left, safe_top, safe_left + safe_width, safe_top + safe_height


def _importance_mass(image: Image.Image) -> float:
    return float(sum(value * count for value, count in enumerate(image.histogram())))
