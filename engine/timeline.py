from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .models import (
    MOTIONS,
    TRANSITIONS,
    AssetSpec,
    HighlightSpec,
    ShotSpec,
    Story,
    TimelinePlan,
    TimelineScene,
    WordTiming,
)
from .utils import load_json, validate_schema


def load_shots(path: Path, story: Story, assets: dict[str, AssetSpec]) -> tuple[ShotSpec, ...]:
    data = load_json(path)
    validate_schema(data, path)
    raw_shots = data.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raise RuntimeError("timeline.json precisa ter uma lista nao vazia em 'shots'.")

    shots: list[ShotSpec] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_shots, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Plano {index} da timeline e invalido.")
        shot_id = str(raw.get("id", "")).strip()
        segment_id = str(raw.get("segment", "")).strip()
        asset_id = str(raw.get("asset", "")).strip()
        motion = str(raw.get("motion", "hold")).strip()
        transition = str(raw.get("transition_out", "cut")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", shot_id) or shot_id in seen_ids:
            raise RuntimeError(f"ID de plano ausente ou duplicado: {shot_id!r}")
        if asset_id not in assets:
            raise RuntimeError(f"Asset desconhecido no plano {shot_id!r}: {asset_id!r}")
        if motion not in MOTIONS:
            raise RuntimeError(f"Movimento desconhecido no plano {shot_id!r}: {motion!r}")
        if transition not in TRANSITIONS:
            raise RuntimeError(f"Transicao desconhecida no plano {shot_id!r}: {transition!r}")

        highlight = _parse_highlight(raw.get("highlight"), shot_id)
        focus = raw.get("focus")
        focus_x = focus_y = None
        if focus is not None:
            if not isinstance(focus, dict):
                raise RuntimeError(f"Foco invalido no plano {shot_id!r}.")
            try:
                focus_x = float(focus.get("x", assets[asset_id].focus_x))
                focus_y = float(focus.get("y", assets[asset_id].focus_y))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Foco invalido no plano {shot_id!r}.") from exc
            if not 0 <= focus_x <= 1 or not 0 <= focus_y <= 1:
                raise RuntimeError(f"Foco do plano {shot_id!r} precisa ficar entre 0 e 1.")

        seen_ids.add(shot_id)
        shots.append(
            ShotSpec(
                id=shot_id,
                segment_id=segment_id,
                asset_id=asset_id,
                motion=motion,
                transition_out=transition,
                highlight=highlight,
                focus_x=focus_x,
                focus_y=focus_y,
            )
        )

    expected_segments = [segment.id for segment in story.segments]
    actual_segments = [shot.segment_id for shot in shots]
    if actual_segments != expected_segments:
        raise RuntimeError(
            "A timeline precisa ter exatamente um plano por segmento e na mesma ordem. "
            f"Esperado: {expected_segments}; recebido: {actual_segments}"
        )
    if shots[-1].transition_out != "cut":
        raise RuntimeError("O ultimo plano precisa terminar com transition_out='cut'.")
    return tuple(shots)


def build_timeline(
    story: Story,
    shots: tuple[ShotSpec, ...],
    assets: dict[str, AssetSpec],
    words: tuple[WordTiming, ...],
    audio_duration: float,
    fps: int,
    crossfade_seconds: float,
) -> TimelinePlan:
    if not words:
        raise RuntimeError("A timeline precisa de timestamps de palavras.")
    if len(words) < len(shots):
        raise RuntimeError(
            "O sidecar de voz tem menos palavras que a quantidade de planos da timeline."
        )
    if audio_duration <= 0 or fps <= 0:
        raise RuntimeError("Duracao de audio e FPS precisam ser positivos.")

    expected_counts = [len(re.findall(r"\S+", segment.text)) for segment in story.segments]
    expected_total = sum(expected_counts)
    total_frames = max(len(shots), math.ceil(audio_duration * fps))

    boundary_frames: list[int] = [0]
    cumulative = 0
    for scene_index, count in enumerate(expected_counts[:-1], start=1):
        cumulative += count
        word_index = round(cumulative / expected_total * len(words)) - 1
        word_index = max(0, min(word_index, len(words) - 2))
        boundary_seconds = (words[word_index].end + words[word_index + 1].start) / 2
        proposed = round(boundary_seconds * fps)
        remaining_scenes = len(shots) - scene_index
        lower = boundary_frames[-1] + 1
        upper = total_frames - remaining_scenes
        boundary_frames.append(max(lower, min(proposed, upper)))
    boundary_frames.append(total_frames)

    scenes: list[TimelineScene] = []
    default_transition_frames = max(0, round(crossfade_seconds * fps))
    for index, shot in enumerate(shots):
        start_frame = boundary_frames[index]
        end_frame = boundary_frames[index + 1]
        frame_count = end_frame - start_frame
        if (
            shot.highlight is not None
            and shot.highlight.start_seconds >= frame_count / fps
        ):
            raise RuntimeError(
                f"O destaque do plano {shot.id!r} comeca em "
                f"{shot.highlight.start_seconds:.2f}s, depois do fim da cena "
                f"({frame_count / fps:.2f}s)."
            )
        transition_frames = 0
        if shot.transition_out == "crossfade" and default_transition_frames > 0:
            next_frame_count = boundary_frames[index + 2] - end_frame
            transition_frames = min(
                default_transition_frames,
                frame_count // 3,
                next_frame_count // 3,
            )
        scenes.append(
            TimelineScene(
                index=index + 1,
                shot=shot,
                asset=assets[shot.asset_id],
                start_frame=start_frame,
                end_frame=end_frame,
                render_frames=frame_count + transition_frames,
                transition_frames=transition_frames,
            )
        )
    return TimelinePlan(
        fps=fps,
        total_frames=total_frames,
        audio_duration=audio_duration,
        scenes=tuple(scenes),
    )


def write_timeline_plan(plan: TimelinePlan, path: Path) -> None:
    data = {
        "fps": plan.fps,
        "audio_duration": round(plan.audio_duration, 6),
        "video_duration": round(plan.duration, 6),
        "total_frames": plan.total_frames,
        "scenes": [
            {
                "id": scene.shot.id,
                "segment": scene.shot.segment_id,
                "asset": scene.asset.id,
                "motion": scene.shot.motion,
                "transition_out": scene.shot.transition_out,
                "start_frame": scene.start_frame,
                "end_frame": scene.end_frame,
                "render_frames": scene.render_frames,
                "start": round(scene.start_frame / plan.fps, 6),
                "end": round(scene.end_frame / plan.fps, 6),
            }
            for scene in plan.scenes
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_highlight(raw: object, shot_id: str) -> HighlightSpec | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        return HighlightSpec(text=text) if text else None
    if not isinstance(raw, dict):
        raise RuntimeError(f"Destaque invalido no plano {shot_id!r}.")
    text = str(raw.get("text", "")).strip()
    if not text:
        return None
    duration = raw.get("duration_seconds")
    try:
        start = float(raw.get("start_seconds", 0.18))
        parsed_duration = float(duration) if duration is not None else None
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Tempos do destaque invalidos no plano {shot_id!r}.") from exc
    if (
        not math.isfinite(start)
        or start < 0
        or (
            parsed_duration is not None
            and (not math.isfinite(parsed_duration) or parsed_duration <= 0)
        )
    ):
        raise RuntimeError(f"Tempos do destaque invalidos no plano {shot_id!r}.")
    return HighlightSpec(
        text=text,
        start_seconds=start,
        duration_seconds=parsed_duration,
    )
