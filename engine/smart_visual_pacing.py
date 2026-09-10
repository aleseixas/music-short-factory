from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path

from .ffmpeg import VideoStreamInfo
from .models import (
    ResolvedVisualFxCue,
    ScriptSegment,
    Story,
    TimelinePlan,
    TimelineScene,
)
from .timeline import resolve_freeze_frame
from .visual_search import MotionAnalysis, analyze_video_motion


# These are safety rails, not editorial schema limits.  The authored boundary
# always remains reachable and each pass can move it only a small distance.
MIN_TAKE_SECONDS = 1.25
MAX_BOUNDARY_SHIFT_SECONDS = 1.0
MAX_BOUNDARY_SHIFT_FRACTION = 0.35


@dataclass(frozen=True)
class SmartVisualPacingSceneReport:
    shot_id: str
    asset_id: str
    media_type: str
    original_start_frame: int
    original_end_frame: int
    adjusted_start_frame: int
    adjusted_end_frame: int
    target_duration_seconds: float
    motion_score: float | None
    practically_static: bool | None
    signals: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return (
            self.original_start_frame != self.adjusted_start_frame
            or self.original_end_frame != self.adjusted_end_frame
        )

    def as_dict(self, fps: int) -> dict[str, object]:
        return {
            "shot": self.shot_id,
            "asset": self.asset_id,
            "media_type": self.media_type,
            "original": {
                "start_frame": self.original_start_frame,
                "end_frame": self.original_end_frame,
                "duration_seconds": round(
                    (self.original_end_frame - self.original_start_frame) / fps,
                    6,
                ),
            },
            "adjusted": {
                "start_frame": self.adjusted_start_frame,
                "end_frame": self.adjusted_end_frame,
                "duration_seconds": round(
                    (self.adjusted_end_frame - self.adjusted_start_frame) / fps,
                    6,
                ),
            },
            "target_duration_seconds": round(self.target_duration_seconds, 6),
            "motion_score": self.motion_score,
            "practically_static": self.practically_static,
            "signals": list(self.signals),
            "changed": self.changed,
        }


@dataclass(frozen=True)
class SmartVisualPacingReport:
    enabled: bool
    applied: bool
    reason: str
    original_total_frames: int
    adjusted_total_frames: int
    scenes: tuple[SmartVisualPacingSceneReport, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self, fps: int) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "applied": self.applied,
            "reason": self.reason,
            "original_total_frames": self.original_total_frames,
            "adjusted_total_frames": self.adjusted_total_frames,
            "duration_preserved": (
                self.original_total_frames == self.adjusted_total_frames
            ),
            "warnings": list(self.warnings),
            "scenes": [scene.as_dict(fps) for scene in self.scenes],
        }


@dataclass(frozen=True)
class _SceneSignal:
    target_seconds: float
    motion_score: float | None
    practically_static: bool | None
    labels: tuple[str, ...]


def analyze_timeline_motion_safely(
    plan: TimelinePlan,
    asset_paths: Mapping[str, Path],
    video_infos: Mapping[str, VideoStreamInfo],
    *,
    analyzer: Callable[..., MotionAnalysis] | None = None,
) -> tuple[dict[str, MotionAnalysis], tuple[str, ...]]:
    """Analyze only the source ranges actually used by video scenes.

    Results are keyed by shot id because the same source asset may be trimmed
    differently in separate shots.  Failures remain advisory: pacing can use a
    neutral video signal and rendering still performs its normal preflight.
    """
    analyses: dict[str, MotionAnalysis] = {}
    warnings: list[str] = []
    motion_analyzer = analyzer or analyze_video_motion
    for scene in plan.scenes:
        if not scene.asset.is_video:
            continue
        path = asset_paths.get(scene.asset.id)
        info = video_infos.get(scene.asset.id)
        if path is None or info is None:
            warnings.append(
                f"shot {scene.shot.id!r}: analise de movimento indisponivel; "
                "sinal neutro utilizado."
            )
            continue
        source_start = scene.shot.source_start_seconds
        source_end = min(
            scene.shot.source_end_seconds or info.duration,
            source_start + scene.required_source_duration(plan.fps),
            info.duration,
        )
        try:
            analyses[scene.shot.id] = motion_analyzer(
                path,
                info.duration,
                source_start_seconds=source_start,
                source_end_seconds=source_end,
            )
        except Exception:
            # Deliberately omit the exception and source path.  The analysis is
            # optional, and lower-level errors may contain remote/cache details.
            warnings.append(
                f"shot {scene.shot.id!r}: analise de movimento falhou; "
                "sinal neutro utilizado."
            )
    return analyses, tuple(warnings)


def apply_smart_visual_pacing(
    plan: TimelinePlan,
    story: Story,
    *,
    enabled: bool,
    crossfade_seconds: float,
    video_motion_by_asset: Mapping[str, object] | None = None,
    video_infos: Mapping[str, VideoStreamInfo] | None = None,
) -> tuple[TimelinePlan, SmartVisualPacingReport]:
    """Return a conservatively retimed plan without changing its content.

    The pass only moves existing boundaries.  It never creates, removes or
    reorders scenes/assets, and it keeps the exact final frame count.  Any
    unsafe result returns the original authored plan instead of blocking the
    episode.
    """
    if not enabled:
        return plan, _report_without_change(plan, False, "disabled")
    if len(plan.scenes) < 2:
        return plan, _report_without_change(plan, True, "not_enough_scenes")

    try:
        _validate_plan_shape(plan)
        story_segments = {segment.id: segment for segment in story.segments}
        motion = video_motion_by_asset or {}
        infos = video_infos or {}
        static_runs = _image_run_lengths(plan.scenes)
        asset_counts: dict[str, int] = {}
        for scene in plan.scenes:
            asset_counts[scene.asset.id] = asset_counts.get(scene.asset.id, 0) + 1
        signals = tuple(
            _scene_signal(
                scene,
                story_segments.get(scene.shot.segment_id),
                static_runs[index],
                asset_counts[scene.asset.id] > 1,
                _motion_value(motion, scene),
                plan.fps,
            )
            for index, scene in enumerate(plan.scenes)
        )
        boundaries = _choose_boundaries(plan, signals)
        if boundaries == _original_boundaries(plan):
            return plan, _build_report(
                plan,
                plan,
                signals,
                enabled=True,
                applied=False,
                reason="no_clear_improvement",
            )

        adjusted = _rebuild_plan(plan, boundaries, crossfade_seconds)
        _validate_adjusted_plan(plan, adjusted, infos)
        return adjusted, _build_report(
            plan,
            adjusted,
            signals,
            enabled=True,
            applied=True,
            reason="boundaries_rebalanced",
        )
    except Exception:
        # Smart pacing is an optional authorship/render aid.  It must never turn
        # a renderable authored timeline into a pipeline failure.
        return plan, _report_without_change(
            plan,
            True,
            "unsafe_adjustment_preserved_original",
            warnings=(
                "O ajuste proposto nao passou pelas garantias de duracao/trim; "
                "a timeline original foi preservada.",
            ),
        )


def write_smart_visual_pacing_report(
    report: SmartVisualPacingReport,
    path: Path,
    fps: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.as_dict(fps), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _scene_signal(
    scene: TimelineScene,
    segment: ScriptSegment | None,
    image_run_length: int,
    repeated_asset: bool,
    motion_value: tuple[float | None, bool | None, float | None],
    fps: int,
) -> _SceneSignal:
    score, practically_static, subject_score = motion_value
    labels: list[str] = []
    if scene.asset.is_video:
        if practically_static is True:
            target = 2.6
            labels.append("video_practically_static")
        elif score is not None and score >= 65.0:
            target = 4.8
            labels.append("video_high_motion")
        elif score is not None and score >= 30.0:
            target = 4.0
            labels.append("video_visible_motion")
        elif score is not None:
            target = 3.0
            labels.append("video_low_motion")
        else:
            target = 3.6
            labels.append("video_motion_unknown")
        if subject_score is not None and subject_score >= 70.0:
            target += 0.4
            labels.append("subject_visible")
    else:
        target = 2.9
        labels.append("image")
        if image_run_length >= 3:
            target = 2.35
            labels.append("static_image_run")
        if scene.shot.motion != "hold" or scene.visual_fx_cues:
            target += 0.3
            labels.append("authored_visual_motion")

    if scene.shot.focus_x is not None or scene.shot.focus_y is not None:
        target += 0.3
        labels.append("authored_subject_focus")
    if repeated_asset:
        target = min(target, 2.4)
        labels.append("repeated_asset")

    delivery = segment.effective_delivery if segment is not None else "neutral"
    text = segment.text if segment is not None else ""
    is_hook = scene.start_frame < round(3.0 * fps)
    if delivery == "hook" or scene.shot.segment_id.casefold().startswith("hook"):
        is_hook = True
    if is_hook:
        target = min(target, 2.5)
        labels.append("hook_fast_pacing")
    elif delivery == "reveal":
        target = min(target, 2.85)
        labels.append("reveal_fast_pacing")
    elif delivery == "dramatic":
        target = min(target, 3.15)
        labels.append("dramatic_beat")
    elif delivery in {"emotional", "payoff"}:
        target = max(target, 3.7)
        labels.append("sustained_payoff_or_emotion")

    if (
        scene.shot.highlight is not None
        or any(character.isdigit() for character in text)
        or "!" in text
        or "?" in text
    ):
        target = min(target, 3.1)
        labels.append("important_phrase")
    return _SceneSignal(
        target_seconds=max(MIN_TAKE_SECONDS, target),
        motion_score=round(score, 2) if score is not None else None,
        practically_static=practically_static,
        labels=tuple(dict.fromkeys(labels)),
    )


def _motion_value(
    values: Mapping[str, object],
    scene: TimelineScene,
) -> tuple[float | None, bool | None, float | None]:
    raw = values.get(scene.shot.id, values.get(scene.asset.id))
    if raw is None:
        return None, None, None
    if isinstance(raw, bool):
        return None, None, None
    if isinstance(raw, (int, float)):
        score = float(raw)
        if not math.isfinite(score):
            return None, None, None
        return _clamp_score(score), score <= 5.0, None
    score = _finite_attribute(raw, "motion_score")
    static_raw = getattr(raw, "is_practically_static", None)
    if static_raw is None:
        static_raw = getattr(raw, "practically_static", None)
    practically_static = static_raw if isinstance(static_raw, bool) else None
    subject_score = _finite_attribute(raw, "face_or_person_score")
    if subject_score is None:
        subject_score = _finite_attribute(raw, "subject_visibility_score")
    return (
        _clamp_score(score) if score is not None else None,
        practically_static,
        _clamp_score(subject_score) if subject_score is not None else None,
    )


def _finite_attribute(value: object, name: str) -> float | None:
    raw = getattr(value, name, None)
    if isinstance(raw, bool):
        return None
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _image_run_lengths(scenes: tuple[TimelineScene, ...]) -> tuple[int, ...]:
    lengths = [0] * len(scenes)
    start = 0
    while start < len(scenes):
        if scenes[start].asset.is_video:
            start += 1
            continue
        end = start + 1
        while end < len(scenes) and not scenes[end].asset.is_video:
            end += 1
        run_length = end - start
        for index in range(start, end):
            lengths[index] = run_length
        start = end
    return tuple(lengths)


def _choose_boundaries(
    plan: TimelinePlan,
    signals: tuple[_SceneSignal, ...],
) -> tuple[int, ...]:
    originals = _original_boundaries(plan)
    minimums = tuple(_minimum_scene_frames(scene, plan.fps) for scene in plan.scenes)
    target_frames = tuple(max(1.0, signal.target_seconds * plan.fps) for signal in signals)
    target_total = sum(target_frames)
    desired = [0]
    cumulative = 0.0
    for target in target_frames[:-1]:
        cumulative += target
        desired.append(round(plan.total_frames * cumulative / target_total))
    desired.append(plan.total_frames)

    lower_bounds = [0]
    upper_bounds = [0]
    prefix_minimum = 0
    suffix_minimums = [0] * (len(minimums) + 1)
    for index in range(len(minimums) - 1, -1, -1):
        suffix_minimums[index] = suffix_minimums[index + 1] + minimums[index]

    for boundary_index in range(1, len(originals) - 1):
        left_original = plan.scenes[boundary_index - 1].frame_count
        right_original = plan.scenes[boundary_index].frame_count
        shift = min(
            max(1, round(MAX_BOUNDARY_SHIFT_SECONDS * plan.fps)),
            max(1, round(min(left_original, right_original) * MAX_BOUNDARY_SHIFT_FRACTION)),
        )
        prefix_minimum += minimums[boundary_index - 1]
        lower = max(originals[boundary_index] - shift, prefix_minimum)
        upper = min(
            originals[boundary_index] + shift,
            plan.total_frames - suffix_minimums[boundary_index],
        )
        cue_lower, cue_upper = _visual_cue_boundary_limits(
            plan,
            boundary_index,
        )
        lower_bounds.append(max(lower, cue_lower))
        upper_bounds.append(min(upper, cue_upper))
    lower_bounds.append(plan.total_frames)
    upper_bounds.append(plan.total_frames)

    chosen = list(originals)
    # A few deterministic forward/backward passes handle neighboring minimums
    # without an opaque optimizer.  The original boundaries always remain a
    # feasible fallback.
    for _ in range(3):
        for index in range(1, len(chosen) - 1):
            lower = max(lower_bounds[index], chosen[index - 1] + minimums[index - 1])
            upper = min(upper_bounds[index], chosen[index + 1] - minimums[index])
            if lower <= upper:
                chosen[index] = max(lower, min(desired[index], upper))
        for index in range(len(chosen) - 2, 0, -1):
            lower = max(lower_bounds[index], chosen[index - 1] + minimums[index - 1])
            upper = min(upper_bounds[index], chosen[index + 1] - minimums[index])
            if lower <= upper:
                chosen[index] = max(lower, min(desired[index], upper))

    return tuple(chosen)


def _minimum_scene_frames(scene: TimelineScene, fps: int) -> int:
    minimum = min(scene.frame_count, max(1, math.ceil(MIN_TAKE_SECONDS * fps)))
    if scene.shot.highlight is not None:
        # Highlight timing is relative to its authored shot and its default
        # duration lives in style.json, outside this pure transform.  Never
        # shorten such a scene: this preserves both explicit and style-default
        # highlight visibility without duplicating style policy here.
        minimum = scene.frame_count
    if scene.shot.freeze_frame is not None:
        freeze_end = (
            math.ceil(scene.shot.freeze_frame.start_seconds * fps - 1e-9)
            + math.ceil(scene.shot.freeze_frame.duration_seconds * fps - 1e-9)
        )
        minimum = max(minimum, freeze_end)
    return min(scene.frame_count, minimum)


def _visual_cue_boundary_limits(
    plan: TimelinePlan,
    boundary_index: int,
) -> tuple[int, int]:
    original = plan.scenes[boundary_index - 1].end_frame
    lower = 0
    upper = plan.total_frames
    for cue in _unique_visual_cues(plan):
        if cue.end_frame <= original:
            lower = max(lower, cue.end_frame)
        elif cue.start_frame >= original:
            upper = min(upper, cue.start_frame)
        else:
            # The cue already crosses this cut. Keep it attached to the same
            # pair of scenes instead of moving the cut beyond the cue.
            lower = max(lower, cue.start_frame + 1)
            upper = min(upper, cue.end_frame - 1)
    return lower, upper


def _rebuild_plan(
    original: TimelinePlan,
    boundaries: tuple[int, ...],
    crossfade_seconds: float,
) -> TimelinePlan:
    default_transition = max(0, round(crossfade_seconds * original.fps))
    scenes: list[TimelineScene] = []
    for index, old_scene in enumerate(original.scenes):
        start = boundaries[index]
        end = boundaries[index + 1]
        frames = end - start
        transition = 0
        if (
            index + 1 < len(original.scenes)
            and old_scene.shot.transition_out == "crossfade"
            and default_transition > 0
        ):
            next_frames = boundaries[index + 2] - end
            transition = min(default_transition, frames // 3, next_frames // 3)
        freeze = resolve_freeze_frame(
            old_scene.shot.freeze_frame,
            frames,
            original.fps,
            f"Plano {old_scene.shot.id!r}.freeze_frame",
        )
        scenes.append(
            replace(
                old_scene,
                start_frame=start,
                end_frame=end,
                render_frames=frames + transition,
                transition_frames=transition,
                visual_fx_cues=(),
                freeze_frame=freeze,
            )
        )
    scenes = _reattach_visual_cues(scenes, _unique_visual_cues(original))
    return replace(original, scenes=tuple(scenes))


def _unique_visual_cues(plan: TimelinePlan) -> tuple[ResolvedVisualFxCue, ...]:
    unique: dict[
        tuple[int, int, int, str, float], ResolvedVisualFxCue
    ] = {}
    for scene in plan.scenes:
        for cue in scene.visual_fx_cues:
            key = (
                cue.index,
                cue.start_frame,
                cue.end_frame,
                cue.type,
                cue.intensity,
            )
            unique.setdefault(key, cue)
    return tuple(sorted(unique.values(), key=lambda cue: (cue.start_frame, cue.index)))


def _reattach_visual_cues(
    scenes: list[TimelineScene],
    cues: tuple[ResolvedVisualFxCue, ...],
) -> list[TimelineScene]:
    result: list[TimelineScene] = []
    for scene in scenes:
        attached: list[ResolvedVisualFxCue] = []
        for cue in cues:
            start = max(cue.start_frame, scene.start_frame)
            end = min(cue.end_frame, scene.end_frame)
            if start >= end:
                continue
            if attached:
                raise RuntimeError(
                    f"O pacing faria o plano {scene.shot.id!r} receber mais de "
                    "uma visual_fx_cue."
                )
            attached.append(
                replace(
                    cue,
                    local_start_frame=start - scene.start_frame,
                    local_end_frame=end - scene.start_frame,
                )
            )
        result.append(replace(scene, visual_fx_cues=tuple(attached)))
    return result


def _validate_plan_shape(plan: TimelinePlan) -> None:
    if (
        isinstance(plan.fps, bool)
        or not isinstance(plan.fps, int)
        or plan.fps <= 0
        or plan.total_frames <= 0
        or not plan.scenes
    ):
        raise RuntimeError("Plano invalido para Smart Visual Pacing.")
    if plan.scenes[0].start_frame != 0 or plan.scenes[-1].end_frame != plan.total_frames:
        raise RuntimeError("Plano nao cobre a duracao total.")
    for left, right in zip(plan.scenes, plan.scenes[1:]):
        if left.end_frame != right.start_frame:
            raise RuntimeError("Plano possui cenas descontiguas.")


def _validate_adjusted_plan(
    original: TimelinePlan,
    adjusted: TimelinePlan,
    video_infos: Mapping[str, VideoStreamInfo],
) -> None:
    if adjusted.total_frames != original.total_frames:
        raise RuntimeError("Smart pacing alterou a duracao total.")
    if len(adjusted.scenes) != len(original.scenes):
        raise RuntimeError("Smart pacing alterou a quantidade de planos.")
    if tuple(scene.asset.id for scene in adjusted.scenes) != tuple(
        scene.asset.id for scene in original.scenes
    ):
        raise RuntimeError("Smart pacing alterou ou reordenou assets.")
    if tuple(scene.shot.id for scene in adjusted.scenes) != tuple(
        scene.shot.id for scene in original.scenes
    ):
        raise RuntimeError("Smart pacing alterou ou reordenou shots.")
    _validate_plan_shape(adjusted)
    if (
        sum(scene.render_frames for scene in adjusted.scenes)
        - sum(scene.transition_frames for scene in adjusted.scenes)
        != adjusted.total_frames
    ):
        raise RuntimeError("Smart pacing quebrou a aritmetica de crossfade.")

    tolerance = 1e-6
    for scene in adjusted.scenes:
        if not scene.asset.is_video:
            continue
        info = video_infos.get(scene.asset.id)
        authored_end = scene.shot.source_end_seconds
        if info is None and authored_end is None:
            continue
        source_end = min(
            authored_end if authored_end is not None else info.duration,
            info.duration if info is not None else authored_end,
        )
        available = source_end - scene.shot.source_start_seconds
        if available + tolerance < scene.required_source_duration(adjusted.fps):
            raise RuntimeError("Smart pacing excederia o trim de um video.")


def _original_boundaries(plan: TimelinePlan) -> tuple[int, ...]:
    return (plan.scenes[0].start_frame,) + tuple(
        scene.end_frame for scene in plan.scenes
    )


def _build_report(
    original: TimelinePlan,
    adjusted: TimelinePlan,
    signals: tuple[_SceneSignal, ...],
    *,
    enabled: bool,
    applied: bool,
    reason: str,
    warnings: tuple[str, ...] = (),
) -> SmartVisualPacingReport:
    scene_reports = tuple(
        SmartVisualPacingSceneReport(
            shot_id=before.shot.id,
            asset_id=before.asset.id,
            media_type=before.asset.media_type,
            original_start_frame=before.start_frame,
            original_end_frame=before.end_frame,
            adjusted_start_frame=after.start_frame,
            adjusted_end_frame=after.end_frame,
            target_duration_seconds=signal.target_seconds,
            motion_score=signal.motion_score,
            practically_static=signal.practically_static,
            signals=signal.labels,
        )
        for before, after, signal in zip(original.scenes, adjusted.scenes, signals)
    )
    return SmartVisualPacingReport(
        enabled=enabled,
        applied=applied,
        reason=reason,
        original_total_frames=original.total_frames,
        adjusted_total_frames=adjusted.total_frames,
        scenes=scene_reports,
        warnings=warnings,
    )


def _report_without_change(
    plan: TimelinePlan,
    enabled: bool,
    reason: str,
    *,
    warnings: tuple[str, ...] = (),
) -> SmartVisualPacingReport:
    return SmartVisualPacingReport(
        enabled=enabled,
        applied=False,
        reason=reason,
        original_total_frames=plan.total_frames,
        adjusted_total_frames=plan.total_frames,
        warnings=warnings,
    )
