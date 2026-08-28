from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable


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


def ffmpeg_output(arguments: Iterable[object]) -> str:
    result = subprocess.run(
        [str(ffmpeg_executable()), *[str(value) for value in arguments]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout + result.stderr


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


def preflight() -> None:
    filters = ffmpeg_output(["-hide_banner", "-filters"])
    encoders = ffmpeg_output(["-hide_banner", "-encoders"])
    missing_filters = [
        name
        for name in ("perspective", "xfade", "subtitles", "overlay")
        if name not in filters
    ]
    if missing_filters:
        raise RuntimeError(f"Build do FFmpeg sem filtros obrigatorios: {', '.join(missing_filters)}")
    if "libx264" not in encoders:
        raise RuntimeError("Build do FFmpeg sem encoder libx264.")
