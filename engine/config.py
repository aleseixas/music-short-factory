from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .utils import load_json


@dataclass(frozen=True)
class PathSettings:
    episodes_dir: str
    work_dir: str
    output_dir: str
    cache_dir: str


@dataclass(frozen=True)
class RenderSettings:
    width: int
    height: int
    fps: int
    working_scale: int
    intermediate_crf: int
    intermediate_preset: str
    crf: int
    preset: str


@dataclass(frozen=True)
class DurationSettings:
    target_tolerance_seconds: float


@dataclass(frozen=True)
class TTSSettings:
    custom_audio: str
    custom_timings: str
    provider: str
    fallback_provider: str | None
    reuse_generated_audio: bool
    allow_estimated_custom_timings: bool
    edge_voice: str
    edge_rate: str


@dataclass(frozen=True)
class MixSettings:
    voice_volume: float
    music_file: str
    music_volume: float
    music_fade_out_seconds: float
    limiter: float
    sample_rate: int
    bitrate: str


@dataclass(frozen=True)
class ProjectConfig:
    paths: PathSettings
    render: RenderSettings
    duration: DurationSettings
    tts: TTSSettings
    mix: MixSettings


@dataclass(frozen=True)
class MotionStyle:
    zoom_amount: float
    pan_zoom: float
    contrast: float
    saturation: float
    brightness: float
    easing: str = "cosine"
    pan_start: float = 0.10
    pan_end: float = 0.90


@dataclass(frozen=True)
class TransitionStyle:
    crossfade_seconds: float


@dataclass(frozen=True)
class CaptionStyle:
    font_name: str
    font_size: int
    text_color: str
    active_color: str
    outline_color: str
    outline_size: int
    shadow_size: int
    margin_left: int
    margin_right: int
    margin_bottom: int
    max_words: int
    max_chars: int
    max_duration: float
    long_gap_seconds: float = 0.38
    balanced_split_threshold: int = 18
    bold: int = -1
    alignment: int = 2


@dataclass(frozen=True)
class HighlightStyle:
    font_file: str
    font_size: int
    text_color: str
    accent_color: str
    background_color: str
    max_width: int
    top: int
    default_duration: float
    fade_seconds: float
    min_font_size: int = 24
    horizontal_padding: int = 44
    vertical_padding: int = 28
    border_radius: int = 10
    accent_width: int = 7
    line_gap: int = 6
    minimum_visible_seconds: float = 0.12
    end_margin_seconds: float = 0.08


@dataclass(frozen=True)
class StyleConfig:
    motion: MotionStyle
    transitions: TransitionStyle
    captions: CaptionStyle
    highlights: HighlightStyle


def load_project_config(path: Path) -> ProjectConfig:
    try:
        return _load_project_config(path)
    except RuntimeError:
        raise
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Valor invalido em {path}: {exc}") from exc


def _load_project_config(path: Path) -> ProjectConfig:
    data = load_json(path)
    paths = _section(data, "paths")
    render = _section(data, "render")
    duration = _section(data, "duration")
    tts = _section(data, "tts")
    mix = _section(data, "mix")

    settings = ProjectConfig(
        paths=PathSettings(
            episodes_dir=str(paths.get("episodes_dir", "episodes")),
            work_dir=str(paths.get("work_dir", "work")),
            output_dir=str(paths.get("output_dir", "output")),
            cache_dir=str(paths.get("cache_dir", "cache")),
        ),
        render=RenderSettings(
            width=int(render.get("width", 720)),
            height=int(render.get("height", 1280)),
            fps=int(render.get("fps", 30)),
            working_scale=int(render.get("working_scale", 2)),
            intermediate_crf=int(render.get("intermediate_crf", 10)),
            intermediate_preset=str(render.get("intermediate_preset", "veryfast")),
            crf=int(render.get("crf", 18)),
            preset=str(render.get("preset", "medium")),
        ),
        duration=DurationSettings(
            target_tolerance_seconds=float(duration.get("target_tolerance_seconds", 15.0)),
        ),
        tts=TTSSettings(
            custom_audio=str(tts.get("custom_audio", "assets/custom_voice.mp3")),
            custom_timings=str(tts.get("custom_timings", "assets/custom_voice.srt")),
            provider=str(tts.get("provider", "edge")).strip(),
            fallback_provider=_optional_string(tts.get("fallback_provider")),
            reuse_generated_audio=bool(tts.get("reuse_generated_audio", True)),
            allow_estimated_custom_timings=bool(
                tts.get("allow_estimated_custom_timings", False)
            ),
            edge_voice=str(tts.get("edge_voice", "pt-BR-AntonioNeural")),
            edge_rate=str(tts.get("edge_rate", "+6%")),
        ),
        mix=MixSettings(
            voice_volume=float(mix.get("voice_volume", 1.0)),
            music_file=str(mix.get("music_file", "")),
            music_volume=float(mix.get("music_volume", 0.06)),
            music_fade_out_seconds=float(mix.get("music_fade_out_seconds", 0.35)),
            limiter=float(mix.get("limiter", 0.95)),
            sample_rate=int(mix.get("sample_rate", 48000)),
            bitrate=str(mix.get("bitrate", "192k")),
        ),
    )
    _validate_project(settings)
    return settings


def load_style_config(path: Path) -> StyleConfig:
    try:
        return _load_style_config(path)
    except RuntimeError:
        raise
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Valor invalido em {path}: {exc}") from exc


def _load_style_config(path: Path) -> StyleConfig:
    data = load_json(path)
    motion = _section(data, "motion")
    transitions = _section(data, "transitions")
    captions = _section(data, "captions")
    highlights = _section(data, "highlights")

    style = StyleConfig(
        motion=MotionStyle(
            zoom_amount=float(motion.get("zoom_amount", 0.035)),
            pan_zoom=float(motion.get("pan_zoom", 1.055)),
            contrast=float(motion.get("contrast", 1.02)),
            saturation=float(motion.get("saturation", 1.03)),
            brightness=float(motion.get("brightness", -0.005)),
            easing=str(motion.get("easing", "cosine")),
            pan_start=float(motion.get("pan_start", 0.10)),
            pan_end=float(motion.get("pan_end", 0.90)),
        ),
        transitions=TransitionStyle(
            crossfade_seconds=float(transitions.get("crossfade_seconds", 0.14)),
        ),
        captions=CaptionStyle(
            font_name=str(captions.get("font_name", "Arial")),
            font_size=int(captions.get("font_size", 52)),
            text_color=str(captions.get("text_color", "#FFFFFF")),
            active_color=str(captions.get("active_color", "#FFD43B")),
            outline_color=str(captions.get("outline_color", "#090909")),
            outline_size=int(captions.get("outline_size", 5)),
            shadow_size=int(captions.get("shadow_size", 1)),
            margin_left=int(captions.get("margin_left", 54)),
            margin_right=int(captions.get("margin_right", 54)),
            margin_bottom=int(captions.get("margin_bottom", 185)),
            max_words=int(captions.get("max_words", 4)),
            max_chars=int(captions.get("max_chars", 25)),
            max_duration=float(captions.get("max_duration", 1.7)),
            long_gap_seconds=float(captions.get("long_gap_seconds", 0.38)),
            balanced_split_threshold=int(captions.get("balanced_split_threshold", 18)),
            bold=int(captions.get("bold", -1)),
            alignment=int(captions.get("alignment", 2)),
        ),
        highlights=HighlightStyle(
            font_file=str(highlights.get("font_file", "")),
            font_size=int(highlights.get("font_size", 48)),
            text_color=str(highlights.get("text_color", "#FFFFFF")),
            accent_color=str(highlights.get("accent_color", "#FFD43B")),
            background_color=str(highlights.get("background_color", "#101010DC")),
            max_width=int(highlights.get("max_width", 600)),
            top=int(highlights.get("top", 108)),
            default_duration=float(highlights.get("default_duration", 1.65)),
            fade_seconds=float(highlights.get("fade_seconds", 0.10)),
            min_font_size=int(highlights.get("min_font_size", 24)),
            horizontal_padding=int(highlights.get("horizontal_padding", 44)),
            vertical_padding=int(highlights.get("vertical_padding", 28)),
            border_radius=int(highlights.get("border_radius", 10)),
            accent_width=int(highlights.get("accent_width", 7)),
            line_gap=int(highlights.get("line_gap", 6)),
            minimum_visible_seconds=float(highlights.get("minimum_visible_seconds", 0.12)),
            end_margin_seconds=float(highlights.get("end_margin_seconds", 0.08)),
        ),
    )
    _validate_style(style)
    return style


def _validate_project(config: ProjectConfig) -> None:
    for label, value in (
        ("episodes_dir", config.paths.episodes_dir),
        ("work_dir", config.paths.work_dir),
        ("output_dir", config.paths.output_dir),
        ("cache_dir", config.paths.cache_dir),
    ):
        _validate_relative_path(value, label)
    configured_roots = {
        "episodes_dir": Path(config.paths.episodes_dir),
        "work_dir": Path(config.paths.work_dir),
        "output_dir": Path(config.paths.output_dir),
        "cache_dir": Path(config.paths.cache_dir),
    }
    for left_name, left in configured_roots.items():
        for right_name, right in configured_roots.items():
            if left_name >= right_name:
                continue
            if left == right or left in right.parents or right in left.parents:
                raise RuntimeError(
                    f"Diretorios globais nao podem se sobrepor: "
                    f"{left_name}={str(left)!r} e {right_name}={str(right)!r}."
                )

    render = config.render
    if render.width <= 0 or render.height <= 0 or render.fps <= 0:
        raise RuntimeError("Resolucao e FPS precisam ser positivos.")
    if render.width % 2 or render.height % 2:
        raise RuntimeError("A resolucao precisa usar dimensoes pares para yuv420p.")
    if render.width * 16 != render.height * 9:
        raise RuntimeError("A resolucao precisa ter proporcao vertical 9:16.")
    if render.working_scale not in {1, 2, 3, 4}:
        raise RuntimeError("working_scale precisa estar entre 1 e 4.")
    if not 0 <= render.intermediate_crf <= 30 or not 0 <= render.crf <= 51:
        raise RuntimeError("Valores de CRF invalidos.")
    if not render.intermediate_preset.strip() or not render.preset.strip():
        raise RuntimeError("Presets FFmpeg nao podem ficar vazios.")
    if config.duration.target_tolerance_seconds < 0:
        raise RuntimeError("target_tolerance_seconds nao pode ser negativo.")
    if not re.fullmatch(r"[a-z0-9_-]+", config.tts.provider):
        raise RuntimeError(f"Provider de TTS invalido: {config.tts.provider!r}")
    if config.tts.fallback_provider and not re.fullmatch(
        r"[a-z0-9_-]+", config.tts.fallback_provider
    ):
        raise RuntimeError(f"Provider fallback invalido: {config.tts.fallback_provider!r}")
    _validate_relative_path(config.tts.custom_audio, "tts.custom_audio", allow_file=True)
    _validate_relative_path(config.tts.custom_timings, "tts.custom_timings", allow_file=True)
    mix = config.mix
    if mix.voice_volume <= 0 or mix.music_volume < 0:
        raise RuntimeError("Volumes de audio invalidos.")
    if mix.music_fade_out_seconds < 0 or not 0 < mix.limiter <= 1:
        raise RuntimeError("Fade ou limiter de audio invalido.")
    if mix.sample_rate <= 0 or not re.fullmatch(r"\d+[kKmM]?", mix.bitrate):
        raise RuntimeError("Sample rate ou bitrate de audio invalido.")


def _validate_style(style: StyleConfig) -> None:
    if style.motion.easing != "cosine":
        raise RuntimeError("Easing desconhecido. Valor suportado: 'cosine'.")
    if not 0 < style.motion.zoom_amount <= 0.10:
        raise RuntimeError("zoom_amount precisa estar entre 0 e 0.10.")
    if not 1.0 < style.motion.pan_zoom <= 1.15:
        raise RuntimeError("pan_zoom precisa estar entre 1.0 e 1.15.")
    if not 0 <= style.motion.pan_start < style.motion.pan_end <= 1:
        raise RuntimeError("pan_start/pan_end precisam formar um intervalo entre 0 e 1.")
    if not 0 <= style.transitions.crossfade_seconds <= 0.40:
        raise RuntimeError("crossfade_seconds precisa estar entre 0 e 0.40.")
    if style.captions.max_words < 1 or style.captions.max_chars < 5:
        raise RuntimeError("Limites de legenda invalidos.")
    if style.captions.font_size <= 0 or style.captions.max_duration <= 0:
        raise RuntimeError("Fonte ou duracao das legendas invalida.")
    if style.captions.long_gap_seconds < 0 or style.captions.alignment not in range(1, 10):
        raise RuntimeError("Gap ou alinhamento das legendas invalido.")
    if style.highlights.font_size <= 0 or style.highlights.min_font_size <= 0:
        raise RuntimeError("Tamanho da fonte dos destaques invalido.")
    if style.highlights.default_duration <= 0 or style.highlights.fade_seconds < 0:
        raise RuntimeError("Duracao dos destaques invalida.")
    for value in (
        style.captions.text_color,
        style.captions.active_color,
        style.captions.outline_color,
        style.highlights.text_color,
        style.highlights.accent_color,
        style.highlights.background_color,
    ):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", value):
            raise RuntimeError(f"Cor hexadecimal invalida: {value}")


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise RuntimeError(f"A secao {name!r} precisa ser um objeto JSON.")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _validate_relative_path(value: str, label: str, allow_file: bool = False) -> None:
    path = Path(value)
    if (
        not value.strip()
        or value.strip() == "."
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise RuntimeError(f"Caminho relativo invalido em {label}: {value!r}")
    if not allow_file and path.suffix:
        raise RuntimeError(f"Diretorio esperado em {label}: {value!r}")
