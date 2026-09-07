from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from pathlib import Path

from .models import (
    DEFAULT_VISUAL_FX_INTENSITY,
    DEFAULT_TEXT_FX_INTENSITY,
    DEFAULT_OVERLAY_OPACITY,
    DEFAULT_OVERLAY_SCALE,
    DEFAULT_VIDEO_SPEED,
    MAX_FREEZE_DURATION_SECONDS,
    MAX_VIDEO_SPEED,
    MIN_FREEZE_DURATION_SECONDS,
    MIN_VIDEO_SPEED,
    MOTIONS,
    TRANSITIONS,
    TEXT_FX_ANIMATIONS,
    TEXT_FX_POSITIONS,
    OVERLAY_ANIMATIONS,
    OVERLAY_POSITIONS,
    VISUAL_FX_TYPES,
    AssetSpec,
    BackgroundMusicSpec,
    FreezeFrameSpec,
    HighlightSpec,
    RelativeTextFxCue,
    ResolvedTextFxCue,
    ResolvedFreezeFrame,
    ResolvedVisualFxCue,
    SfxCue,
    ShotSpec,
    Story,
    TimelinePlan,
    TimelineScene,
    TimelineSpec,
    TextFxCue,
    TextFxCueSpec,
    OverlayCue,
    VisualFxCue,
    WordTiming,
)
from .utils import load_json, validate_schema


def load_shots(path: Path, story: Story, assets: dict[str, AssetSpec]) -> tuple[ShotSpec, ...]:
    return load_timeline(path, story, assets).shots


def load_timeline(
    path: Path,
    story: Story,
    assets: dict[str, AssetSpec],
) -> TimelineSpec:
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

        source_start = _parse_non_negative_seconds(
            raw.get("source_start_seconds", 0),
            f"Plano {shot_id!r}.source_start_seconds",
        )
        raw_source_end = raw.get("source_end_seconds")
        source_end = (
            _parse_non_negative_seconds(
                raw_source_end,
                f"Plano {shot_id!r}.source_end_seconds",
            )
            if raw_source_end is not None
            else None
        )
        if source_end is not None and source_end <= source_start:
            raise RuntimeError(
                f"Plano {shot_id!r}.source_end_seconds precisa ser maior que "
                "source_start_seconds."
            )
        speed = _parse_number(
            raw.get("speed", DEFAULT_VIDEO_SPEED),
            f"Plano {shot_id!r}.speed",
        )
        if not MIN_VIDEO_SPEED <= speed <= MAX_VIDEO_SPEED:
            raise RuntimeError(
                f"Plano {shot_id!r}.speed precisa ficar entre "
                f"{MIN_VIDEO_SPEED:.1f} e {MAX_VIDEO_SPEED:.1f}."
            )
        freeze_frame = _parse_freeze_frame(raw.get("freeze_frame"), shot_id)
        if not assets[asset_id].is_video and (source_start != 0 or source_end is not None):
            raise RuntimeError(
                f"Plano {shot_id!r} usa recorte de fonte, mas o asset "
                f"{asset_id!r} nao e video."
            )
        if not assets[asset_id].is_video and speed != DEFAULT_VIDEO_SPEED:
            raise RuntimeError(
                f"Plano {shot_id!r} usa speed={speed:g}, mas o asset "
                f"{asset_id!r} nao e video."
            )
        if not assets[asset_id].is_video and freeze_frame is not None:
            raise RuntimeError(
                f"Plano {shot_id!r} usa freeze_frame, mas o asset "
                f"{asset_id!r} nao e video."
            )

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
                source_start_seconds=source_start,
                source_end_seconds=source_end,
                speed=speed,
                freeze_frame=freeze_frame,
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
    return TimelineSpec(
        shots=tuple(shots),
        background_music=_parse_background_music(data.get("background_music")),
        sfx_cues=_parse_sfx_cues(data.get("sfx_cues")),
        visual_fx_cues=_parse_visual_fx_cues(data.get("visual_fx_cues")),
        text_fx_cues=_parse_text_fx_cues(data.get("text_fx_cues")),
        overlay_cues=_parse_overlay_cues(data.get("overlay_cues"), assets),
    )


def build_timeline(
    story: Story,
    shots: tuple[ShotSpec, ...],
    assets: dict[str, AssetSpec],
    words: tuple[WordTiming, ...],
    audio_duration: float,
    fps: int,
    crossfade_seconds: float,
    visual_fx_cues: tuple[VisualFxCue, ...] = (),
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
        freeze_frame = resolve_freeze_frame(
            shot.freeze_frame,
            frame_count,
            fps,
            f"Plano {shot.id!r}.freeze_frame",
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
                freeze_frame=freeze_frame,
            )
        )
    if visual_fx_cues:
        scenes = _attach_visual_fx_cues(
            scenes,
            visual_fx_cues,
            total_frames,
            fps,
        )
    return TimelinePlan(
        fps=fps,
        total_frames=total_frames,
        audio_duration=audio_duration,
        scenes=tuple(scenes),
    )


def resolve_text_fx_cues(
    cues: tuple[TextFxCueSpec, ...],
    plan: TimelinePlan,
) -> tuple[ResolvedTextFxCue, ...]:
    """Resolve declarative text FX against the real, frame-based timeline."""
    if not cues:
        return ()
    if (
        isinstance(plan.fps, bool)
        or not isinstance(plan.fps, (int, float))
        or not math.isfinite(plan.fps)
        or plan.fps <= 0
    ):
        raise RuntimeError("FPS invalido ao resolver text_fx_cues.")

    scenes_by_segment: dict[str, list[TimelineScene]] = {}
    for scene in plan.scenes:
        scenes_by_segment.setdefault(scene.shot.segment_id, []).append(scene)

    resolved: list[ResolvedTextFxCue] = []
    for index, cue in enumerate(cues, start=1):
        label = f"text_fx_cues[{index}]"
        start_limit = None
        end_limit = None
        if isinstance(cue, TextFxCue):
            start = _parse_non_negative_seconds(
                cue.start_seconds,
                f"{label}.start_seconds",
            )
            end = _parse_non_negative_seconds(
                cue.end_seconds,
                f"{label}.end_seconds",
            )
            if end <= start:
                raise RuntimeError(
                    f"{label}.end_seconds precisa ser maior que start_seconds."
                )
        elif isinstance(cue, RelativeTextFxCue):
            if not isinstance(cue.segment_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]+",
                cue.segment_id,
            ):
                raise RuntimeError(f"{label}.segment invalido: {cue.segment_id!r}.")
            offset = _parse_non_negative_seconds(
                cue.offset_seconds,
                f"{label}.offset_seconds",
            )
            duration = _parse_number(
                cue.duration_seconds,
                f"{label}.duration_seconds",
            )
            if duration <= 0:
                raise RuntimeError(
                    f"{label}.duration_seconds precisa ser maior que zero."
                )

            matching_scenes = scenes_by_segment.get(cue.segment_id, [])
            if not matching_scenes:
                raise RuntimeError(
                    f"{label}.segment referencia segmento inexistente na timeline "
                    f"resolvida: {cue.segment_id!r}."
                )
            if len(matching_scenes) != 1:
                raise RuntimeError(
                    f"{label}.segment tem ancora ambigua na timeline resolvida: "
                    f"{cue.segment_id!r} corresponde a {len(matching_scenes)} shots."
                )

            scene = matching_scenes[0]
            scene_start = scene.start_frame / plan.fps
            scene_end = scene.end_frame / plan.fps
            available = scene_end - scene_start
            if offset >= available:
                raise RuntimeError(
                    f"{label} comeca fora do segmento {cue.segment_id!r}: "
                    f"offset={offset:.6f}s; duracao do segmento={available:.6f}s."
                )
            if offset + duration > available + 1e-9:
                raise RuntimeError(
                    f"{label} ultrapassa o segmento {cue.segment_id!r}: "
                    f"offset + duration={offset + duration:.6f}s; "
                    f"disponivel={available:.6f}s."
                )
            start = scene_start + offset
            end = start + duration
            if abs(end - scene_end) <= 1e-9:
                end = scene_end
            start_limit = scene_start
            end_limit = scene_end
        else:
            raise RuntimeError(f"{label} usa um formato de timing desconhecido.")

        resolved.append(
            ResolvedTextFxCue(
                start_seconds=start,
                end_seconds=end,
                text=cue.text,
                animation=cue.animation,
                position=cue.position,
                intensity=cue.intensity,
                accent_text=cue.accent_text,
                start_limit_seconds=start_limit,
                end_limit_seconds=end_limit,
            )
        )

    _check_text_fx_overlaps(tuple(resolved))
    return tuple(resolved)


def _attach_visual_fx_cues(
    scenes: list[TimelineScene],
    cues: tuple[VisualFxCue, ...],
    total_frames: int,
    fps: int,
) -> list[TimelineScene]:
    resolved_by_scene: list[list[ResolvedVisualFxCue]] = [
        [] for _ in scenes
    ]
    video_duration = total_frames / fps

    for cue_index, cue in enumerate(cues, start=1):
        if cue.start_seconds >= video_duration:
            print(
                f"[visual_fx] aviso: cue {cue_index} ({cue.type}) ignorada; "
                f"start_seconds={cue.start_seconds:.3f}s esta no ou apos o fim "
                f"do video ({video_duration:.3f}s)."
            )
            continue

        # Treat cues as half-open intervals [start, end): never start on a
        # frame whose timestamp precedes start_seconds. The epsilon avoids a
        # floating-point artifact pushing exact frame boundaries forward.
        start_frame = max(0, math.ceil(cue.start_seconds * fps - 1e-9))
        end_frame = min(total_frames, math.ceil(cue.end_seconds * fps - 1e-9))
        if end_frame <= start_frame:
            print(
                f"[visual_fx] aviso: cue {cue_index} ({cue.type}) ignorada; "
                f"o intervalo {cue.start_seconds:.3f}s-{cue.end_seconds:.3f}s "
                f"nao contem frames em {fps} FPS."
            )
            continue

        for scene_position, scene in enumerate(scenes):
            segment_start = max(start_frame, scene.start_frame)
            segment_end = min(end_frame, scene.end_frame)
            if segment_start >= segment_end:
                continue
            if resolved_by_scene[scene_position]:
                previous = resolved_by_scene[scene_position][0]
                raise RuntimeError(
                    f"O plano {scene.shot.id!r} recebe mais de uma visual_fx_cue "
                    f"(cues {previous.index} e {cue_index}). Nesta etapa, use no "
                    "maximo uma cue visual por plano."
                )
            resolved_by_scene[scene_position].append(
                ResolvedVisualFxCue(
                    index=cue_index,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    local_start_frame=segment_start - scene.start_frame,
                    local_end_frame=segment_end - scene.start_frame,
                    type=cue.type,
                    intensity=cue.intensity,
                )
            )

    return [
        replace(scene, visual_fx_cues=tuple(resolved))
        if resolved
        else scene
        for scene, resolved in zip(scenes, resolved_by_scene)
    ]


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
                "speed": scene.shot.speed,
                **(
                    {
                        "freeze_frame": {
                            "start_frame": scene.freeze_frame.start_frame,
                            "duration_frames": scene.freeze_frame.duration_frames,
                        }
                    }
                    if scene.freeze_frame is not None
                    else {}
                ),
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


def _parse_freeze_frame(raw: object, shot_id: str) -> FreezeFrameSpec | None:
    if raw is None:
        return None
    label = f"Plano {shot_id!r}.freeze_frame"
    if not isinstance(raw, dict):
        raise RuntimeError(f"{label} precisa ser um objeto ou null.")
    if "start_seconds" not in raw or "duration_seconds" not in raw:
        raise RuntimeError(
            f"{label} precisa definir start_seconds e duration_seconds."
        )
    start = _parse_non_negative_seconds(
        raw.get("start_seconds"),
        f"{label}.start_seconds",
    )
    duration = _parse_number(
        raw.get("duration_seconds"),
        f"{label}.duration_seconds",
    )
    if not MIN_FREEZE_DURATION_SECONDS <= duration <= MAX_FREEZE_DURATION_SECONDS:
        raise RuntimeError(
            f"{label}.duration_seconds precisa ficar entre "
            f"{MIN_FREEZE_DURATION_SECONDS:.2f} e "
            f"{MAX_FREEZE_DURATION_SECONDS:.2f}."
        )
    return FreezeFrameSpec(start_seconds=start, duration_seconds=duration)


def resolve_freeze_frame(
    freeze_frame: FreezeFrameSpec | None,
    shot_frames: int,
    fps: int,
    label: str = "freeze_frame",
) -> ResolvedFreezeFrame | None:
    """Resolve one output-relative freeze to deterministic project frames."""
    if freeze_frame is None:
        return None
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise RuntimeError(f"{label} nao pode ser resolvido com FPS invalido.")
    if isinstance(shot_frames, bool) or not isinstance(shot_frames, int) or shot_frames <= 0:
        raise RuntimeError(f"{label} nao pode ser resolvido em shot sem frames.")

    start_frame = max(
        0,
        math.ceil(freeze_frame.start_seconds * fps - 1e-9),
    )
    duration_frames = math.ceil(freeze_frame.duration_seconds * fps - 1e-9)
    if duration_frames < 2:
        raise RuntimeError(
            f"{label}.duration_seconds precisa representar pelo menos dois "
            f"frames no FPS atual ({fps})."
        )
    if start_frame >= shot_frames:
        raise RuntimeError(
            f"{label}.start_seconds fica fora da duracao real do shot."
        )
    if start_frame + duration_frames > shot_frames:
        raise RuntimeError(
            f"{label} ultrapassa a duracao real do shot; o freeze nao pode "
            "invadir o handle de crossfade."
        )
    return ResolvedFreezeFrame(
        start_frame=start_frame,
        duration_frames=duration_frames,
    )


def _parse_background_music(raw: object) -> BackgroundMusicSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeError("background_music precisa ser um objeto ou null.")
    return BackgroundMusicSpec(
        profile=_parse_effect_name(raw.get("profile"), "background_music.profile"),
        volume=_parse_volume(raw.get("volume"), "background_music.volume"),
    )


def _parse_sfx_cues(raw: object) -> tuple[SfxCue, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RuntimeError("sfx_cues precisa ser uma lista.")
    cues: list[SfxCue] = []
    for index, cue in enumerate(raw, start=1):
        label = f"sfx_cues[{index}]"
        if not isinstance(cue, dict):
            raise RuntimeError(f"{label} precisa ser um objeto.")
        source_start = _parse_non_negative_seconds(
            cue.get("source_start_seconds", 0),
            f"{label}.source_start_seconds",
        )
        raw_duration = cue.get("duration_seconds")
        duration = (
            _parse_number(raw_duration, f"{label}.duration_seconds")
            if raw_duration is not None
            else None
        )
        if duration is not None and duration <= 0:
            raise RuntimeError(f"{label}.duration_seconds precisa ser maior que zero.")
        cues.append(
            SfxCue(
                time_seconds=_parse_non_negative_seconds(
                    cue.get("time_seconds"), f"{label}.time_seconds"
                ),
                type=_parse_effect_name(cue.get("type"), f"{label}.type"),
                volume=_parse_volume(cue.get("volume"), f"{label}.volume"),
                source_start_seconds=source_start,
                duration_seconds=duration,
            )
        )
    return tuple(cues)


def _parse_visual_fx_cues(raw: object) -> tuple[VisualFxCue, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RuntimeError("visual_fx_cues precisa ser uma lista.")
    cues: list[VisualFxCue] = []
    for index, cue in enumerate(raw, start=1):
        label = f"visual_fx_cues[{index}]"
        if not isinstance(cue, dict):
            raise RuntimeError(f"{label} precisa ser um objeto.")
        start = _parse_non_negative_seconds(
            cue.get("start_seconds"), f"{label}.start_seconds"
        )
        end = _parse_non_negative_seconds(
            cue.get("end_seconds"), f"{label}.end_seconds"
        )
        if end <= start:
            raise RuntimeError(f"{label}.end_seconds precisa ser maior que start_seconds.")
        effect_type = _parse_effect_name(cue.get("type"), f"{label}.type")
        if effect_type not in VISUAL_FX_TYPES:
            supported = ", ".join(sorted(VISUAL_FX_TYPES))
            raise RuntimeError(
                f"{label}.type desconhecido: {effect_type!r}. "
                f"Tipos suportados: {supported}."
            )
        cues.append(
            VisualFxCue(
                start_seconds=start,
                end_seconds=end,
                type=effect_type,
                intensity=_parse_volume(
                    cue.get("intensity", DEFAULT_VISUAL_FX_INTENSITY),
                    f"{label}.intensity",
                ),
            )
        )

    ordered = sorted(enumerate(cues, start=1), key=lambda item: item[1].start_seconds)
    for (left_index, left), (right_index, right) in zip(ordered, ordered[1:]):
        if right.start_seconds < left.end_seconds:
            raise RuntimeError(
                "visual_fx_cues sobrepostas nao sao suportadas nesta etapa: "
                f"cue {left_index} ({left.type}) e cue {right_index} ({right.type})."
            )
    return tuple(cues)


def _parse_text_fx_cues(raw: object) -> tuple[TextFxCueSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RuntimeError("text_fx_cues precisa ser uma lista.")
    cues: list[TextFxCueSpec] = []
    for index, cue in enumerate(raw, start=1):
        label = f"text_fx_cues[{index}]"
        if not isinstance(cue, dict):
            raise RuntimeError(f"{label} precisa ser um objeto.")
        absolute_fields = {"start_seconds", "end_seconds"}
        relative_fields = {"segment", "offset_seconds", "duration_seconds"}
        present_absolute = absolute_fields.intersection(cue)
        present_relative = relative_fields.intersection(cue)
        if present_absolute and present_relative:
            raise RuntimeError(
                f"{label} mistura timing absoluto e relativo; use somente "
                "start_seconds + end_seconds ou segment + offset_seconds + "
                "duration_seconds."
            )
        if present_absolute:
            missing = absolute_fields.difference(cue)
            if missing:
                raise RuntimeError(
                    f"{label} no modo absoluto precisa definir start_seconds e "
                    "end_seconds."
                )
            start = _parse_non_negative_seconds(
                cue.get("start_seconds"),
                f"{label}.start_seconds",
            )
            end = _parse_non_negative_seconds(
                cue.get("end_seconds"),
                f"{label}.end_seconds",
            )
            if end <= start:
                raise RuntimeError(
                    f"{label}.end_seconds precisa ser maior que start_seconds."
                )
            text, animation, position, intensity, accent_text = (
                _parse_text_fx_content(cue, label)
            )
            cues.append(
                TextFxCue(
                    start,
                    end,
                    text,
                    animation,
                    position,
                    intensity,
                    accent_text,
                )
            )
            continue
        if present_relative:
            missing = relative_fields.difference(cue)
            if missing:
                raise RuntimeError(
                    f"{label} no modo relativo precisa definir segment, "
                    "offset_seconds e duration_seconds."
                )
            raw_segment = cue.get("segment")
            segment_id = raw_segment.strip() if isinstance(raw_segment, str) else ""
            if not re.fullmatch(r"[A-Za-z0-9_-]+", segment_id):
                raise RuntimeError(f"{label}.segment invalido: {raw_segment!r}.")
            offset = _parse_non_negative_seconds(
                cue.get("offset_seconds"),
                f"{label}.offset_seconds",
            )
            duration = _parse_number(
                cue.get("duration_seconds"),
                f"{label}.duration_seconds",
            )
            if duration <= 0:
                raise RuntimeError(
                    f"{label}.duration_seconds precisa ser maior que zero."
                )
            text, animation, position, intensity, accent_text = (
                _parse_text_fx_content(cue, label)
            )
            cues.append(
                RelativeTextFxCue(
                    segment_id,
                    offset,
                    duration,
                    text,
                    animation,
                    position,
                    intensity,
                    accent_text,
                )
            )
            continue
        raise RuntimeError(
            f"{label} precisa definir timing absoluto com start_seconds + "
            "end_seconds ou timing relativo com segment + offset_seconds + "
            "duration_seconds."
        )

    absolute_cues = tuple(
        (index, cue)
        for index, cue in enumerate(cues, start=1)
        if isinstance(cue, TextFxCue)
    )
    _check_text_fx_overlaps(
        tuple(cue for _, cue in absolute_cues),
        tuple(index for index, _ in absolute_cues),
    )
    return tuple(cues)


def _parse_text_fx_content(
    cue: dict[str, object],
    label: str,
) -> tuple[str, str, str, float, str | None]:
    text = str(cue.get("text", "")).strip()
    if not text:
        raise RuntimeError(f"{label}.text precisa ser um texto nao vazio.")
    animation = _parse_effect_name(cue.get("animation"), f"{label}.animation")
    if animation not in TEXT_FX_ANIMATIONS:
        supported = ", ".join(sorted(TEXT_FX_ANIMATIONS))
        raise RuntimeError(
            f"{label}.animation desconhecida: {animation!r}. "
            f"Animacoes suportadas: {supported}."
        )
    position = str(cue.get("position", "center")).strip()
    if position not in TEXT_FX_POSITIONS:
        supported = ", ".join(sorted(TEXT_FX_POSITIONS))
        raise RuntimeError(
            f"{label}.position desconhecida: {position!r}. "
            f"Posicoes suportadas: {supported}."
        )
    accent_raw = cue.get("accent_text")
    accent_text = str(accent_raw).strip() if accent_raw is not None else None
    if accent_text and accent_text.casefold() not in text.casefold():
        raise RuntimeError(f"{label}.accent_text precisa aparecer em text.")
    intensity = _parse_volume(
        cue.get("intensity", DEFAULT_TEXT_FX_INTENSITY),
        f"{label}.intensity",
    )
    return text, animation, position, intensity, accent_text or None


def _check_text_fx_overlaps(
    cues: tuple[TextFxCue | ResolvedTextFxCue, ...],
    source_indexes: tuple[int, ...] | None = None,
) -> None:
    indexes = source_indexes or tuple(range(1, len(cues) + 1))
    if len(indexes) != len(cues):
        raise RuntimeError("Indices invalidos ao validar text_fx_cues.")
    ordered = sorted(
        zip(indexes, cues),
        key=lambda item: item[1].start_seconds,
    )
    for (left_index, left), (right_index, right) in zip(ordered, ordered[1:]):
        if right.start_seconds < left.end_seconds:
            raise RuntimeError(
                "text_fx_cues sobrepostas nao sao suportadas nesta etapa: "
                f"cue {left_index} e cue {right_index}."
            )


def _parse_overlay_cues(
    raw: object,
    assets: dict[str, AssetSpec],
) -> tuple[OverlayCue, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RuntimeError("overlay_cues precisa ser uma lista.")
    cues: list[OverlayCue] = []
    for index, cue in enumerate(raw, start=1):
        label = f"overlay_cues[{index}]"
        if not isinstance(cue, dict):
            raise RuntimeError(f"{label} precisa ser um objeto.")
        start = _parse_non_negative_seconds(cue.get("start_seconds"), f"{label}.start_seconds")
        end = _parse_non_negative_seconds(cue.get("end_seconds"), f"{label}.end_seconds")
        if end <= start:
            raise RuntimeError(f"{label}.end_seconds precisa ser maior que start_seconds.")
        asset_id = str(cue.get("asset", "")).strip()
        if asset_id not in assets:
            raise RuntimeError(f"Asset de overlay desconhecido em {label}: {asset_id!r}.")
        if assets[asset_id].is_video:
            raise RuntimeError(
                f"Asset de video {asset_id!r} nao pode ser usado em overlay_cues; "
                "overlays de video nao sao suportados nesta etapa."
            )
        animation = _parse_effect_name(cue.get("animation"), f"{label}.animation")
        if animation not in OVERLAY_ANIMATIONS:
            supported = ", ".join(sorted(OVERLAY_ANIMATIONS))
            raise RuntimeError(f"{label}.animation desconhecida: {animation!r}. Animacoes suportadas: {supported}.")
        position = str(cue.get("position", "center")).strip()
        if position not in OVERLAY_POSITIONS:
            supported = ", ".join(sorted(OVERLAY_POSITIONS))
            raise RuntimeError(f"{label}.position desconhecida: {position!r}. Posicoes suportadas: {supported}.")
        scale = _parse_number(cue.get("scale", DEFAULT_OVERLAY_SCALE), f"{label}.scale")
        if not 0.10 <= scale <= 0.80:
            raise RuntimeError(f"{label}.scale precisa ficar entre 0.10 e 0.80.")
        opacity = _parse_volume(cue.get("opacity", DEFAULT_OVERLAY_OPACITY), f"{label}.opacity")
        cues.append(OverlayCue(start, end, asset_id, animation, position, scale, opacity))

    ordered = sorted(enumerate(cues, start=1), key=lambda item: item[1].start_seconds)
    for (left_index, left), (right_index, right) in zip(ordered, ordered[1:]):
        if right.start_seconds < left.end_seconds:
            raise RuntimeError(
                "overlay_cues simultaneos nao sao suportados nesta etapa: "
                f"cue {left_index} e cue {right_index}."
            )
    return tuple(cues)


def _parse_effect_name(raw: object, label: str) -> str:
    value = raw.strip() if isinstance(raw, str) else ""
    if not re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", value):
        raise RuntimeError(
            f"{label} invalido: use letras minusculas, numeros, '_' ou '-'."
        )
    return value


def _parse_volume(raw: object, label: str) -> float:
    value = _parse_number(raw, label)
    if not 0 <= value <= 1:
        raise RuntimeError(f"{label} precisa ficar entre 0 e 1.")
    return value


def _parse_non_negative_seconds(raw: object, label: str) -> float:
    value = _parse_number(raw, label)
    if value < 0:
        raise RuntimeError(f"{label} precisa ser maior ou igual a zero.")
    return value


def _parse_number(raw: object, label: str) -> float:
    if isinstance(raw, bool):
        raise RuntimeError(f"{label} precisa ser um numero finito.")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} precisa ser um numero finito.") from exc
    if not math.isfinite(value):
        raise RuntimeError(f"{label} precisa ser um numero finito.")
    return value
