from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class VideoStreamInfo:
    duration: float
    width: int
    height: int
    fps: float


@dataclass(frozen=True)
class LoudnormMeasurement:
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float


def probe_audio_duration(path: Path) -> float:
    """Validate the first audio stream and return its positive duration."""
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(f"Audio ausente: {path}")
    raw = ffprobe_output(
        [
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type,duration:format=duration",
            "-of",
            "json",
            path,
        ]
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe retornou JSON invalido para {path}") from exc

    streams = data.get("streams")
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise RuntimeError(f"Arquivo de audio sem stream de audio: {path}")
    stream = streams[0]
    if stream.get("codec_type") != "audio":
        raise RuntimeError(f"Arquivo de audio sem stream de audio: {path}")

    duration = _parse_probe_positive_float(stream.get("duration"))
    if duration is None:
        format_data = data.get("format")
        if isinstance(format_data, dict):
            duration = _parse_probe_positive_float(format_data.get("duration"))
    if duration is None:
        raise RuntimeError(f"Duracao de audio invalida em {path}")
    return duration


@lru_cache(maxsize=1)
def ffmpeg_executable() -> Path:
    try:
        import imageio_ffmpeg
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Dependencia imageio-ffmpeg ausente. Execute: "
            "python -m pip install -r requirements.txt"
        ) from exc
    return Path(imageio_ffmpeg.get_ffmpeg_exe())


@lru_cache(maxsize=1)
def ffprobe_executable() -> Path:
    configured = os.environ.get("FFPROBE_BINARY", "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if not candidate.is_file():
            raise RuntimeError(f"FFPROBE_BINARY nao aponta para um arquivo: {candidate}")
        return candidate

    discovered = shutil.which("ffprobe")
    if discovered:
        return Path(discovered).resolve()

    ffmpeg_path = ffmpeg_executable().resolve()
    sibling_names = ("ffprobe.exe", "ffprobe") if os.name == "nt" else ("ffprobe", "ffprobe.exe")
    for name in sibling_names:
        candidate = ffmpeg_path.with_name(name)
        if candidate.is_file():
            return candidate

    raise RuntimeError(
        "ffprobe nao encontrado. Instale o pacote oficial FFmpeg com ffprobe "
        "ou configure FFPROBE_BINARY antes de validar video ou audio remoto."
    )


def run_ffmpeg(arguments: Iterable[object], cwd: Path | None = None) -> None:
    command = [str(ffmpeg_executable()), *[str(value) for value in arguments]]
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-30:])
        raise RuntimeError(f"FFmpeg falhou ({result.returncode}):\n{tail}")


def run_ffmpeg_capture(
    arguments: Iterable[object], cwd: Path | None = None
) -> str:
    command = [str(ffmpeg_executable()), *[str(value) for value in arguments]]
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = result.stdout + result.stderr
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-30:])
        raise RuntimeError(f"FFmpeg falhou ({result.returncode}):\n{tail}")
    return output


def parse_loudnorm_measurement(output: str) -> LoudnormMeasurement:
    matches = re.findall(r'\{\s*"input_i"\s*:.*?\}', output, flags=re.DOTALL)
    if not matches:
        raise RuntimeError("FFmpeg loudnorm nao retornou as medicoes esperadas.")
    try:
        data = json.loads(matches[-1])
        measurement = LoudnormMeasurement(
            input_i=float(data["input_i"]),
            input_tp=float(data["input_tp"]),
            input_lra=float(data["input_lra"]),
            input_thresh=float(data["input_thresh"]),
            target_offset=float(data["target_offset"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Medicoes invalidas retornadas pelo FFmpeg loudnorm.") from exc
    if not all(
        math.isfinite(value)
        for value in (
            measurement.input_i,
            measurement.input_tp,
            measurement.input_lra,
            measurement.input_thresh,
            measurement.target_offset,
        )
    ):
        raise RuntimeError("Medicoes nao finitas retornadas pelo FFmpeg loudnorm.")
    return measurement


def ffmpeg_output(arguments: Iterable[object]) -> str:
    result = subprocess.run(
        [str(ffmpeg_executable()), *[str(value) for value in arguments]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout + result.stderr


def ffprobe_output(arguments: Iterable[object]) -> str:
    result = subprocess.run(
        [str(ffprobe_executable()), *[str(value) for value in arguments]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-20:])
        raise RuntimeError(f"ffprobe falhou ({result.returncode}):\n{tail}")
    return result.stdout


def probe_video_stream(path: Path) -> VideoStreamInfo:
    """Validate and return metadata for the first video stream using ffprobe."""
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(f"Video ausente: {path}")
    raw = ffprobe_output(
        [
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration:format=duration",
            "-of",
            "json",
            path,
        ]
    )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe retornou JSON invalido para {path}") from exc

    streams = data.get("streams")
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        raise RuntimeError(f"Asset de video sem stream de video: {path}")
    stream = streams[0]
    if stream.get("codec_type") != "video":
        raise RuntimeError(f"Asset de video sem stream de video: {path}")
    try:
        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Resolucao de video invalida em {path}") from exc
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Resolucao de video invalida em {path}: {width}x{height}")

    fps = _parse_probe_frame_rate(stream.get("avg_frame_rate"))
    if fps is None:
        fps = _parse_probe_frame_rate(stream.get("r_frame_rate"))
    if fps is None:
        raise RuntimeError(f"FPS de video invalido em {path}")

    duration = _parse_probe_positive_float(stream.get("duration"))
    if duration is None:
        format_data = data.get("format")
        if isinstance(format_data, dict):
            duration = _parse_probe_positive_float(format_data.get("duration"))
    if duration is None:
        raise RuntimeError(f"Duracao de video invalida em {path}")
    return VideoStreamInfo(duration=duration, width=width, height=height, fps=fps)


def _parse_probe_frame_rate(raw: object) -> float | None:
    if not isinstance(raw, str) or not raw.strip() or raw.strip() == "0/0":
        return None
    try:
        value = float(Fraction(raw.strip()))
    except (ValueError, ZeroDivisionError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _parse_probe_positive_float(raw: object) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def probe_duration(path: Path) -> float:
    if not path.exists():
        raise RuntimeError(f"Midia ausente: {path}")
    output = ffmpeg_output(["-hide_banner", "-i", path])
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
    if not match:
        raise RuntimeError(f"Nao foi possivel detectar a duracao de {path}")
    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration <= 0:
        raise RuntimeError(f"Duracao invalida em {path}: {duration}")
    return duration


def probe_video_frame_count(path: Path) -> int:
    if not path.exists():
        raise RuntimeError(f"Video ausente: {path}")
    output = ffmpeg_output(
        ["-hide_banner", "-i", path, "-map", "0:v:0", "-an", "-f", "null", "-"]
    )
    matches = re.findall(r"frame=\s*(\d+)", output)
    if not matches:
        raise RuntimeError(f"Nao foi possivel contar os frames de {path}")
    return int(matches[-1])


def preflight(
    require_background_music: bool = False,
    require_sfx: bool = False,
    require_video_assets: bool = False,
) -> None:
    filters = ffmpeg_output(["-hide_banner", "-filters"])
    encoders = ffmpeg_output(["-hide_banner", "-encoders"])
    missing_filters = [
        name
        for name in ("perspective", "xfade", "subtitles", "overlay", "loudnorm")
        if name not in filters
    ]
    if missing_filters:
        raise RuntimeError(f"Build do FFmpeg sem filtros obrigatorios: {', '.join(missing_filters)}")
    if require_background_music:
        required_audio_filters = (
            "afade",
            "aformat",
            "alimiter",
            "amix",
            "apad",
            "aresample",
            "asplit",
            "atrim",
            "sidechaincompress",
        )
        missing_audio_filters = [
            name for name in required_audio_filters if name not in filters
        ]
        if missing_audio_filters:
            raise RuntimeError(
                "Build do FFmpeg sem filtros de background music: "
                f"{', '.join(missing_audio_filters)}"
            )
    if require_sfx:
        required_sfx_filters = (
            "aformat",
            "alimiter",
            "amix",
            "anullsrc",
            "apad",
            "aresample",
            "asetpts",
            "atrim",
            "concat",
            "volume",
        )
        missing_sfx_filters = [
            name for name in required_sfx_filters if name not in filters
        ]
        if missing_sfx_filters:
            raise RuntimeError(
                "Build do FFmpeg sem filtros de SFX: "
                f"{', '.join(missing_sfx_filters)}"
            )
    if require_video_assets:
        required_video_filters = ("crop", "fps", "pad", "scale", "setpts", "trim")
        missing_video_filters = [
            name for name in required_video_filters if name not in filters
        ]
        if missing_video_filters:
            raise RuntimeError(
                "Build do FFmpeg sem filtros para assets de video: "
                f"{', '.join(missing_video_filters)}"
            )
        ffprobe_executable()
    if "libx264" not in encoders:
        raise RuntimeError("Build do FFmpeg sem encoder libx264.")
