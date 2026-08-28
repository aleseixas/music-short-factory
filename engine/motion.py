from __future__ import annotations

import math

from .config import MotionStyle
from .models import MOTIONS


def cosine_ease(progress: float) -> float:
    progress = max(0.0, min(1.0, progress))
    return 0.5 - 0.5 * math.cos(math.pi * progress)


def build_motion_filter(
    motion: str,
    frames: int,
    fps: int,
    output_width: int,
    output_height: int,
    working_scale: int,
    focus_x: float,
    focus_y: float,
    style: MotionStyle,
) -> str:
    if motion not in MOTIONS:
        raise RuntimeError(f"Movimento desconhecido: {motion}")
    if frames < 1:
        raise RuntimeError("Uma cena precisa ter ao menos um frame.")

    denominator = max(1, frames - 1)
    # The perspective filter exposes `on` starting at 1.
    progress = f"min((on-1)/{denominator},1)"
    if style.easing != "cosine":
        raise RuntimeError(f"Easing desconhecido: {style.easing}")
    eased = f"(0.5-0.5*cos(PI*{progress}))"
    zoom_amount = style.zoom_amount

    if motion == "push_in":
        zoom = f"1+{zoom_amount:.6f}*{eased}"
        bias_x = f"{focus_x:.6f}"
    elif motion == "pull_out":
        zoom = f"1+{zoom_amount:.6f}*(1-{eased})"
        bias_x = f"{focus_x:.6f}"
    elif motion in {"pan_left", "pan_right"}:
        zoom = f"{style.pan_zoom:.6f}"
        span = style.pan_end - style.pan_start
        if motion == "pan_left":
            bias_x = f"{style.pan_start:.2f}+{span:.2f}*{eased}"
        else:
            bias_x = f"{style.pan_end:.2f}-{span:.2f}*{eased}"
    else:
        zoom = "1"
        bias_x = "0"

    work_width = output_width * working_scale
    work_height = output_height * working_scale
    if motion == "hold":
        transform = ""
    else:
        left = f"(W-W/({zoom}))*({bias_x})"
        right = f"({left})+W/({zoom})"
        top = f"(H-H/({zoom}))*{focus_y:.6f}"
        bottom = f"({top})+H/({zoom})"
        transform = (
            f"perspective=x0='{left}':y0='{top}':x1='{right}':y1='{top}':"
            f"x2='{left}':y2='{bottom}':x3='{right}':y3='{bottom}':"
            "sense=source:eval=frame:interpolation=cubic,"
        )
    return transform + (
        f"scale={work_width}:{work_height}:flags=lanczos,"
        f"scale={output_width}:{output_height}:flags=lanczos:"
        "in_range=pc:out_range=tv:out_color_matrix=bt709,"
        f"eq=contrast={style.contrast:.4f}:saturation={style.saturation:.4f}:"
        f"brightness={style.brightness:.4f},"
        f"fps={fps},setsar=1,format=yuv420p,"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
