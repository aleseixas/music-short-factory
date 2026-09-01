from __future__ import annotations

import math

from .config import MotionStyle
from .models import MOTIONS, VISUAL_FX_TYPES, ResolvedVisualFxCue


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
    visual_fx_cues: tuple[ResolvedVisualFxCue, ...] = (),
    scene_start_frame: int = 0,
    input_fps_normalized: bool = False,
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

    if visual_fx_cues:
        return _build_visual_fx_filter(
            visual_fx_cues=visual_fx_cues,
            frames=frames,
            fps=fps,
            output_width=output_width,
            output_height=output_height,
            working_scale=working_scale,
            focus_x=focus_x,
            focus_y=focus_y,
            style=style,
            scene_start_frame=scene_start_frame,
            input_fps_normalized=input_fps_normalized,
        )

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
    fps_filter = "" if input_fps_normalized else f"fps={fps},"
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
        f"{fps_filter}setsar=1,format=yuv420p,"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )


def _build_visual_fx_filter(
    visual_fx_cues: tuple[ResolvedVisualFxCue, ...],
    frames: int,
    fps: int,
    output_width: int,
    output_height: int,
    working_scale: int,
    focus_x: float,
    focus_y: float,
    style: MotionStyle,
    scene_start_frame: int,
    input_fps_normalized: bool,
) -> str:
    if len(visual_fx_cues) != 1:
        raise RuntimeError(
            "Cada plano pode receber no maximo uma visual_fx_cue nesta etapa."
        )
    cue = visual_fx_cues[0]
    if cue.type not in VISUAL_FX_TYPES:
        raise RuntimeError(f"Efeito visual desconhecido: {cue.type}")
    if not math.isfinite(cue.intensity) or not 0 <= cue.intensity <= 1:
        raise RuntimeError("A intensidade do efeito visual precisa ficar entre 0 e 1.")
    if (
        scene_start_frame < 0
        or cue.start_frame < 0
        or cue.end_frame <= cue.start_frame
        or cue.local_start_frame < 0
        or cue.local_end_frame <= cue.local_start_frame
        or cue.local_end_frame > frames
    ):
        raise RuntimeError("Intervalo resolvido de visual_fx_cue invalido.")

    # Perspective exposes on starting at 1. Clamp the global frame to this
    # shot's semantic slice: hold entry before the cue and terminal framing
    # after it, including the outgoing crossfade handle.
    segment_start = scene_start_frame + cue.local_start_frame
    segment_end = scene_start_frame + cue.local_end_frame
    global_frame = f"({scene_start_frame}+on-1)"
    clamped_frame = (
        f"min(max({global_frame},{segment_start}),{segment_end})"
    )
    cue_frames = cue.end_frame - cue.start_frame
    progress = (
        f"min(max((({clamped_frame})-{cue.start_frame})/"
        f"{cue_frames:.6f},0),1)"
    )
    eased = f"(0.5-0.5*cos(PI*{progress}))"
    focus_x = max(0.0, min(1.0, focus_x))
    focus_y = max(0.0, min(1.0, focus_y))
    zoom, bias_x, bias_y = _visual_fx_components(
        cue.type,
        cue.intensity,
        progress,
        eased,
        focus_x,
        focus_y,
    )

    left = f"(W-W/({zoom}))*({bias_x})"
    right = f"({left})+W/({zoom})"
    top = f"(H-H/({zoom}))*({bias_y})"
    bottom = f"({top})+H/({zoom})"
    transform = (
        f"perspective=x0='{left}':y0='{top}':x1='{right}':y1='{top}':"
        f"x2='{left}':y2='{bottom}':x3='{right}':y3='{bottom}':"
        "sense=source:eval=frame:interpolation=cubic,"
    )
    work_width = output_width * working_scale
    work_height = output_height * working_scale
    fps_filter = "" if input_fps_normalized else f"fps={fps},"
    return transform + (
        f"scale={work_width}:{work_height}:flags=lanczos,"
        f"scale={output_width}:{output_height}:flags=lanczos:"
        "in_range=pc:out_range=tv:out_color_matrix=bt709,"
        f"eq=contrast={style.contrast:.4f}:saturation={style.saturation:.4f}:"
        f"brightness={style.brightness:.4f},"
        f"{fps_filter}setsar=1,format=yuv420p,"
        "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )


def _visual_fx_components(
    effect_type: str,
    intensity: float,
    progress: str,
    eased: str,
    focus_x: float,
    focus_y: float,
) -> tuple[str, str, str]:
    if effect_type in {"slow_zoom_in", "slow_zoom_out"}:
        amount = 0.03 + 0.09 * intensity
        if effect_type == "slow_zoom_in":
            zoom = f"1+{amount:.6f}*{eased}"
        else:
            zoom = f"1+{amount:.6f}*(1-{eased})"
        return zoom, f"{focus_x:.6f}", f"{focus_y:.6f}"

    if effect_type == "punch_zoom":
        peak = 0.08 + 0.07 * intensity
        settle = 0.04 + 0.06 * intensity
        rise_progress = f"min({progress}/0.450000,1)"
        fall_progress = f"min(max(({progress}-0.450000)/0.550000,0),1)"
        rise_eased = f"(0.5-0.5*cos(PI*{rise_progress}))"
        fall_eased = f"(0.5-0.5*cos(PI*{fall_progress}))"
        zoom = (
            f"if(lt({progress},0.450000),"
            f"1+{peak:.6f}*{rise_eased},"
            f"1+{peak:.6f}+({settle - peak:.6f})*{fall_eased})"
        )
        return zoom, f"{focus_x:.6f}", f"{focus_y:.6f}"

    pan_zoom = 1.04 + 0.04 * intensity
    travel = 0.20 + 0.40 * intensity
    if effect_type in {"pan_left", "pan_right"}:
        low, high = _pan_limits(focus_x, travel)
        span = high - low
        if effect_type == "pan_right":
            bias_x = f"{low:.6f}+{span:.6f}*{eased}"
        else:
            bias_x = f"{high:.6f}-{span:.6f}*{eased}"
        bias_y = f"{focus_y:.6f}"
    else:
        low, high = _pan_limits(focus_y, travel)
        span = high - low
        bias_x = f"{focus_x:.6f}"
        if effect_type == "pan_down":
            bias_y = f"{low:.6f}+{span:.6f}*{eased}"
        else:
            bias_y = f"{high:.6f}-{span:.6f}*{eased}"
    return f"{pan_zoom:.6f}", bias_x, bias_y


def _pan_limits(focus: float, travel: float) -> tuple[float, float]:
    half_travel = travel / 2
    return max(0.0, focus - half_travel), min(1.0, focus + half_travel)
