from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from statistics import fmean, median, pstdev
from tempfile import TemporaryDirectory

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from .ffmpeg import VideoStreamInfo, run_ffmpeg
from .models import TimelinePlan
from .visual_repetition import (
    VisualFingerprint,
    VisualHistoryEntry,
    assess_repetition,
    build_visual_fingerprint,
    load_visual_history,
)


SAMPLE_FRAME_COUNT = 7
SAMPLE_END_FRACTION = 0.98
MAX_CANDIDATE_WINDOWS = 7
MIN_CLEAR_GAIN = 12.0
MIN_CANDIDATE_SCORE = 65.0
MIN_ANALYSIS_CONFIDENCE = 0.75
MIN_COMPONENT_IMPROVEMENT = 3.0
MAX_CRITICAL_REGRESSION = 12.0
STATIC_PAIR_DIFFERENCE = 1.15
STATIC_PAIR_RATIO = 0.80
ANALYSIS_WIDTH = 180
ANALYSIS_HEIGHT = 320
RECENT_HISTORY_EPISODES = 12
STRONG_FRAME_SIMILARITY = 0.90
STRONG_SOURCE_OVERLAP = 0.50


@dataclass(frozen=True)
class SegmentWindowMetrics:
    start_seconds: float
    end_seconds: float
    motion_score: float
    sharpness_score: float
    exposure_score: float
    subject_visibility_score: float
    stability_score: float
    scene_change_score: float
    sample_count: int
    expected_sample_count: int = SAMPLE_FRAME_COUNT
    practically_static: bool = False
    face_or_person_score: float | None = None
    temporal_consistency: float = 1.0
    unique_frame_ratio: float = 1.0

    @property
    def confidence(self) -> float:
        if self.expected_sample_count <= 0:
            return 0.0
        coverage = min(1.0, self.sample_count / self.expected_sample_count)
        # Subject visibility is a deterministic saliency proxy. A real
        # face/person detector is optional and its absence must not invalidate
        # an otherwise complete analysis.
        signal_completeness = 1.0 if self.face_or_person_score is not None else 0.94
        reliability = (
            0.65
            + 0.25 * _clamp(self.temporal_consistency, 0.0, 1.0)
            + 0.10 * _clamp(self.unique_frame_ratio, 0.0, 1.0)
        )
        return round(coverage * signal_completeness * reliability, 4)

    def as_dict(self) -> dict[str, object]:
        scored = score_segment_window(self)
        return {
            "start_seconds": round(self.start_seconds, 6),
            "end_seconds": round(self.end_seconds, 6),
            "score": scored.score,
            "confidence": self.confidence,
            "practically_static": self.practically_static,
            "sample_count": self.sample_count,
            "expected_sample_count": self.expected_sample_count,
            "temporal_consistency": round(self.temporal_consistency, 4),
            "unique_frame_ratio": round(self.unique_frame_ratio, 4),
            "components": scored.components,
            "face_or_person_score": self.face_or_person_score,
        }


@dataclass(frozen=True)
class SegmentWindowScore:
    metrics: SegmentWindowMetrics
    score: float
    components: dict[str, float]

    @property
    def confidence(self) -> float:
        return self.metrics.confidence

    def as_dict(self) -> dict[str, object]:
        return self.metrics.as_dict()


@dataclass(frozen=True)
class BestSegmentDecision:
    original_start_seconds: float
    original_end_seconds: float | None
    consumed_original_end_seconds: float
    selected_start_seconds: float
    selected_end_seconds: float | None
    consumed_selected_end_seconds: float
    original_score: float | None
    new_score: float | None
    selected_score: float | None
    gain: float | None
    confidence: float
    changed: bool
    reason: str
    windows: tuple[SegmentWindowScore, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "original_trim": {
                "source_start_seconds": round(self.original_start_seconds, 6),
                "source_end_seconds": (
                    round(self.original_end_seconds, 6)
                    if self.original_end_seconds is not None
                    else None
                ),
                "consumed_end_seconds": round(
                    self.consumed_original_end_seconds, 6
                ),
            },
            "selected_trim": {
                "source_start_seconds": round(self.selected_start_seconds, 6),
                "source_end_seconds": (
                    round(self.selected_end_seconds, 6)
                    if self.selected_end_seconds is not None
                    else None
                ),
                "consumed_end_seconds": round(
                    self.consumed_selected_end_seconds, 6
                ),
            },
            "original_score": self.original_score,
            "new_score": self.new_score,
            "selected_score": self.selected_score,
            "gain": self.gain,
            "confidence": self.confidence,
            "changed": self.changed,
            "reason": self.reason,
            "windows": [window.as_dict() for window in self.windows],
        }


WindowAnalyzer = Callable[..., SegmentWindowMetrics]
FingerprintBuilder = Callable[..., VisualFingerprint]


def score_segment_window(metrics: SegmentWindowMetrics) -> SegmentWindowScore:
    """Score one source window from independent objective signals."""
    components = {
        "motion": _score_value(metrics.motion_score),
        "sharpness": _score_value(metrics.sharpness_score),
        "exposure": _score_value(metrics.exposure_score),
        "subject_visibility": _score_value(metrics.subject_visibility_score),
        "stability": _score_value(metrics.stability_score),
        "scene_changes": _score_value(metrics.scene_change_score),
    }
    if metrics.face_or_person_score is not None:
        face_score = _score_value(metrics.face_or_person_score)
        components["face_or_person"] = face_score
        components["subject_visibility"] = round(
            0.60 * components["subject_visibility"] + 0.40 * face_score,
            2,
        )

    score = (
        0.18 * components["motion"]
        + 0.22 * components["sharpness"]
        + 0.18 * components["exposure"]
        + 0.14 * components["subject_visibility"]
        + 0.16 * components["stability"]
        + 0.12 * components["scene_changes"]
    )
    if metrics.practically_static:
        score = min(score, 42.0)
    if components["exposure"] < 25.0 or components["sharpness"] < 20.0:
        score = min(score, 50.0)
    return SegmentWindowScore(
        metrics=metrics,
        score=round(_clamp(score), 2),
        components=components,
    )


def choose_best_segment(
    original_start_seconds: float,
    original_end_seconds: float | None,
    required_seconds: float,
    windows: Sequence[SegmentWindowScore],
) -> BestSegmentDecision:
    """Choose only a clearly superior multi-signal alternative."""
    baseline = next(
        (
            window
            for window in windows
            if math.isclose(
                window.metrics.start_seconds,
                original_start_seconds,
                abs_tol=1e-5,
            )
        ),
        None,
    )
    original_consumed_end = original_start_seconds + required_seconds
    if baseline is None:
        return _kept_decision(
            original_start_seconds,
            original_end_seconds,
            original_consumed_end,
            reason="analysis_unavailable",
            windows=tuple(windows),
        )

    alternatives = sorted(
        (
            window
            for window in windows
            if not math.isclose(
                window.metrics.start_seconds,
                original_start_seconds,
                abs_tol=1e-5,
            )
        ),
        key=lambda item: (-item.score, -item.confidence, item.metrics.start_seconds),
    )
    if not alternatives:
        return _kept_decision(
            original_start_seconds,
            original_end_seconds,
            original_consumed_end,
            baseline=baseline,
            reason="no_alternative_window",
            windows=tuple(windows),
        )

    accepted: list[tuple[SegmentWindowScore, float]] = []
    rejection_reasons: list[str] = []
    for candidate in alternatives:
        reason = _candidate_rejection_reason(
            baseline,
            candidate,
            required_seconds,
        )
        if reason is None:
            accepted.append((candidate, candidate.score - baseline.score))
        else:
            rejection_reasons.append(reason)

    best_examined = alternatives[0]
    gain = round(best_examined.score - baseline.score, 2)
    confidence = round(min(baseline.confidence, best_examined.confidence), 4)
    if accepted:
        selected, selected_gain = accepted[0]
        selected_start = selected.metrics.start_seconds
        selected_end = selected_start + required_seconds
        return BestSegmentDecision(
            original_start_seconds=original_start_seconds,
            original_end_seconds=original_end_seconds,
            consumed_original_end_seconds=original_consumed_end,
            selected_start_seconds=selected_start,
            selected_end_seconds=selected_end,
            consumed_selected_end_seconds=selected_end,
            original_score=baseline.score,
            new_score=selected.score,
            selected_score=selected.score,
            gain=round(selected_gain, 2),
            confidence=round(min(baseline.confidence, selected.confidence), 4),
            changed=True,
            reason="clear_multidimensional_gain",
            windows=tuple(windows),
        )

    reason = _summarize_rejections(rejection_reasons, baseline, alternatives)
    return BestSegmentDecision(
        original_start_seconds=original_start_seconds,
        original_end_seconds=original_end_seconds,
        consumed_original_end_seconds=original_consumed_end,
        selected_start_seconds=original_start_seconds,
        selected_end_seconds=original_end_seconds,
        consumed_selected_end_seconds=original_consumed_end,
        original_score=baseline.score,
        new_score=best_examined.score,
        selected_score=baseline.score,
        gain=gain,
        confidence=confidence,
        changed=False,
        reason=reason,
        windows=tuple(windows),
    )


def select_best_segment(
    path: Path,
    media_duration_seconds: float,
    *,
    original_start_seconds: float,
    original_end_seconds: float | None,
    required_seconds: float,
    source_fps: float,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    freeze_source_offset_seconds: float | None = None,
    freeze_weight: float = 0.0,
    analyzer: WindowAnalyzer | None = None,
) -> BestSegmentDecision:
    """Analyze deterministic compatible windows, retaining authored trim on doubt."""
    original_consumed_end = original_start_seconds + required_seconds
    if (
        not _finite_positive(media_duration_seconds)
        or not _finite_positive(required_seconds)
        or not _finite_non_negative(original_start_seconds)
        or original_consumed_end > media_duration_seconds + 1e-6
        or (
            original_end_seconds is not None
            and (
                not _finite_positive(original_end_seconds)
                or original_end_seconds + 1e-6 < original_consumed_end
                or original_end_seconds > media_duration_seconds + 1e-6
            )
        )
    ):
        return _kept_decision(
            original_start_seconds,
            original_end_seconds,
            original_consumed_end,
            reason="baseline_trim_invalid",
        )

    starts = candidate_window_starts(
        media_duration_seconds,
        required_seconds,
        original_start_seconds,
        source_fps=source_fps,
    )
    if len(starts) <= 1:
        return _kept_decision(
            original_start_seconds,
            original_end_seconds,
            original_consumed_end,
            reason="no_alternative_window",
        )

    analyze = analyzer or analyze_segment_window
    windows: list[SegmentWindowScore] = []
    for start in starts:
        try:
            metrics = analyze(
                path,
                start,
                required_seconds,
                focus_x=focus_x,
                focus_y=focus_y,
                emphasis_offset_seconds=freeze_source_offset_seconds,
                emphasis_weight=freeze_weight,
            )
            if not isinstance(metrics, SegmentWindowMetrics):
                continue
            windows.append(score_segment_window(metrics))
        except Exception:
            # Segment analysis is advisory; technical preflight remains the
            # authority and will still validate the authored trim.
            continue
    return choose_best_segment(
        original_start_seconds,
        original_end_seconds,
        required_seconds,
        windows,
    )


def select_best_segments_safely(
    plan: TimelinePlan,
    asset_paths: Mapping[str, Path],
    video_infos: Mapping[str, VideoStreamInfo],
    report_path: Path,
    *,
    analyzer: WindowAnalyzer | None = None,
    project_root: Path | None = None,
    exclude_episode: str | None = None,
    visual_history: Sequence[VisualHistoryEntry] | None = None,
    fingerprint_builder: FingerprintBuilder | None = None,
) -> TimelinePlan:
    """Apply advisory decisions in memory and always preserve a renderable fallback."""
    history_available = True
    history_warnings: tuple[str, ...] = ()
    history: tuple[VisualHistoryEntry, ...]
    has_video_scenes = any(scene.asset.is_video for scene in plan.scenes)
    if not has_video_scenes:
        history = ()
    elif visual_history is not None:
        history = tuple(visual_history)
    elif project_root is None:
        history = ()
    else:
        try:
            history, history_warnings = load_visual_history(
                project_root,
                exclude_episode=exclude_episode,
                recent_episode_limit=RECENT_HISTORY_EPISODES,
            )
        except Exception as exc:
            history = ()
            history_available = False
            print(
                "[best-segment] aviso: historico visual indisponivel "
                f"({type(exc).__name__}); trims originais serao mantidos."
            )
    try:
        return _select_best_segments(
            plan,
            asset_paths,
            video_infos,
            report_path,
            analyzer=analyzer,
            visual_history=history,
            history_available=history_available,
            history_warnings=history_warnings,
            fingerprint_builder=fingerprint_builder or build_visual_fingerprint,
        )
    except Exception as exc:
        print(
            "[best-segment] aviso: analise indisponivel "
            f"({type(exc).__name__}); trims originais mantidos."
        )
        return plan


def candidate_window_starts(
    media_duration_seconds: float,
    required_seconds: float,
    original_start_seconds: float,
    *,
    source_fps: float,
) -> tuple[float, ...]:
    max_start = max(0.0, media_duration_seconds - required_seconds)
    if max_start <= 1e-6:
        return (round(original_start_seconds, 6),)
    # Nearby windows preserve the GPT's semantic intent; file boundaries are
    # still inspected for reporting but may not auto-replace a distant baseline.
    raw = [
        original_start_seconds - required_seconds * 1.5,
        original_start_seconds - required_seconds * 0.75,
        original_start_seconds + required_seconds * 0.75,
        original_start_seconds + required_seconds * 1.5,
        0.0,
        max_start,
    ]
    fps = source_fps if _finite_positive(source_fps) else 30.0
    tail_margin = min(max_start, max(2.0 / fps, 0.05))
    safe_max_start = max(0.0, max_start - tail_margin)
    raw[-1] = safe_max_start
    starts: list[float] = []
    for value in raw:
        aligned = min(
            safe_max_start,
            max(0.0, round(value * fps) / fps),
        )
        if not any(math.isclose(aligned, old, abs_tol=1e-5) for old in starts):
            starts.append(aligned)
    # The authored baseline is never snapped or normalized: fallback means
    # retaining exactly the original value supplied by the GPT.
    if not any(
        math.isclose(original_start_seconds, old, abs_tol=1e-7)
        for old in starts
    ):
        starts.append(original_start_seconds)
    starts.sort()
    return tuple(round(value, 6) for value in starts)


def analyze_segment_window(
    path: Path,
    start_seconds: float,
    duration_seconds: float,
    *,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    emphasis_offset_seconds: float | None = None,
    emphasis_weight: float = 0.0,
) -> SegmentWindowMetrics:
    """Extract sparse frames with FFmpeg and derive deterministic quality signals."""
    source = Path(path).resolve()
    if not source.is_file() or not _finite_non_negative(start_seconds):
        raise RuntimeError("Fonte ou inicio invalido para analise de segmento.")
    if not _finite_positive(duration_seconds):
        raise RuntimeError("Duracao invalida para analise de segmento.")
    if not 0.0 <= focus_x <= 1.0 or not 0.0 <= focus_y <= 1.0:
        raise RuntimeError("Foco invalido para analise de segmento.")

    with TemporaryDirectory(prefix="msf-best-segment-") as temporary:
        frame_pattern = Path(temporary) / "frame-%03d.png"
        sampling_span = duration_seconds * SAMPLE_END_FRACTION
        sampling_fps = (SAMPLE_FRAME_COUNT - 1) / sampling_span
        run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start_seconds:.6f}",
                "-i",
                source,
                "-an",
                "-vf",
                (
                    "settb=AVTB,setpts=PTS-STARTPTS,"
                    f"fps={sampling_fps:.8f}:start_time=0,"
                    f"scale={ANALYSIS_WIDTH}:{ANALYSIS_HEIGHT}:"
                    "force_original_aspect_ratio=increase:flags=fast_bilinear,"
                    f"crop={ANALYSIS_WIDTH}:{ANALYSIS_HEIGHT}:"
                    "(iw-ow)/2:(ih-oh)/2,format=rgb24"
                ),
                "-frames:v",
                SAMPLE_FRAME_COUNT,
                frame_pattern,
            ]
        )
        frame_paths = sorted(Path(temporary).glob("frame-*.png"))
        frames = []
        for frame_path in frame_paths[:SAMPLE_FRAME_COUNT]:
            with Image.open(frame_path) as opened:
                frames.append(ImageOps.grayscale(opened).copy())

    if len(frames) < 2:
        raise RuntimeError("Amostras insuficientes para analisar o segmento.")

    sharpness_values = [_sharpness_score(frame) for frame in frames]
    exposure_values = [_exposure_score(frame) for frame in frames]
    subject_values: list[float] = []
    centroids: list[tuple[float, float]] = []
    for frame in frames:
        subject, centroid = _subject_visibility(frame, focus_x, focus_y)
        subject_values.append(subject)
        centroids.append(centroid)

    differences = [_frame_difference(left, right) for left, right in zip(frames, frames[1:])]
    static_ratio = sum(value <= STATIC_PAIR_DIFFERENCE for value in differences) / len(differences)
    motion_score = _motion_quality_score(_robust_temporal_value(differences))
    stability_score = _stability_score(centroids, differences)
    scene_change_score = _scene_change_score(differences)

    sharpness = _robust_temporal_score(sharpness_values)
    exposure = _robust_temporal_score(exposure_values)
    subject = _robust_temporal_score(subject_values)
    if emphasis_offset_seconds is not None and frames:
        relative = min(
            duration_seconds,
            max(0.0, float(emphasis_offset_seconds)),
        )
        index = min(
            len(frames) - 1,
            round(relative / sampling_span * (len(frames) - 1)),
        )
        weight = min(0.35, max(0.0, float(emphasis_weight)))
        sharpness = _blend(sharpness, sharpness_values[index], weight)
        exposure = _blend(exposure, exposure_values[index], weight)
        subject = _blend(subject, subject_values[index], weight)

    return SegmentWindowMetrics(
        start_seconds=round(start_seconds, 6),
        end_seconds=round(start_seconds + duration_seconds, 6),
        motion_score=round(motion_score, 2),
        sharpness_score=round(sharpness, 2),
        exposure_score=round(exposure, 2),
        subject_visibility_score=round(subject, 2),
        stability_score=round(stability_score, 2),
        scene_change_score=round(scene_change_score, 2),
        sample_count=len(frames),
        practically_static=static_ratio >= STATIC_PAIR_RATIO,
        # No heavy detector is introduced in the runtime. The saliency-based
        # subject score above remains available; semantic face/person presence
        # is explicitly unknown rather than guessed.
        face_or_person_score=None,
        temporal_consistency=round(
            _temporal_consistency(
                sharpness_values,
                exposure_values,
                subject_values,
                differences,
            ),
            4,
        ),
        unique_frame_ratio=round(
            sum(value > STATIC_PAIR_DIFFERENCE for value in differences)
            / len(differences),
            4,
        ),
    )


def _select_best_segments(
    plan: TimelinePlan,
    asset_paths: Mapping[str, Path],
    video_infos: Mapping[str, VideoStreamInfo],
    report_path: Path,
    *,
    analyzer: WindowAnalyzer | None,
    visual_history: Sequence[VisualHistoryEntry],
    history_available: bool,
    history_warnings: Sequence[str],
    fingerprint_builder: FingerprintBuilder,
) -> TimelinePlan:
    scenes = []
    records: list[dict[str, object]] = []
    changed_count = 0
    analysis_cache: dict[tuple[object, ...], SegmentWindowMetrics] = {}
    fingerprint_cache: dict[tuple[object, ...], VisualFingerprint] = {}
    base_analyzer = analyzer or analyze_segment_window

    def cached_analyzer(
        path: Path,
        start: float,
        duration: float,
        **kwargs: object,
    ) -> SegmentWindowMetrics:
        key = (
            str(Path(path).resolve()),
            round(start, 6),
            round(duration, 6),
            *(round(float(kwargs.get(name, 0.0) or 0.0), 6) for name in (
                "focus_x",
                "focus_y",
                "emphasis_offset_seconds",
                "emphasis_weight",
            )),
        )
        if key not in analysis_cache:
            analysis_cache[key] = base_analyzer(path, start, duration, **kwargs)
        return analysis_cache[key]

    def source_identity(asset_id: str) -> str:
        source = asset_paths.get(asset_id)
        return str(Path(source).resolve()) if source is not None else asset_id

    reservations = {
        scene.index: (
            source_identity(scene.asset.id),
            scene.shot.source_start_seconds,
            scene.shot.source_start_seconds
            + scene.required_source_duration(plan.fps),
        )
        for scene in plan.scenes
        if scene.asset.is_video
    }
    for scene in plan.scenes:
        if not scene.asset.is_video:
            scenes.append(scene)
            continue
        source = asset_paths.get(scene.asset.id)
        info = video_infos.get(scene.asset.id)
        required = scene.required_source_duration(plan.fps)
        if source is None or info is None:
            decision = _kept_decision(
                scene.shot.source_start_seconds,
                scene.shot.source_end_seconds,
                scene.shot.source_start_seconds + required,
                reason="analysis_unavailable",
            )
        else:
            focus_x = (
                scene.asset.focus_x
                if scene.shot.focus_x is None
                else scene.shot.focus_x
            )
            focus_y = (
                scene.asset.focus_y
                if scene.shot.focus_y is None
                else scene.shot.focus_y
            )
            freeze_offset = None
            freeze_weight = 0.0
            if scene.freeze_frame is not None:
                freeze_offset = (
                    scene.freeze_frame.start_frame / plan.fps * scene.shot.speed
                )
                freeze_weight = scene.freeze_frame.added_frames / max(
                    1, scene.render_frames
                )
            decision = select_best_segment(
                source,
                info.duration,
                original_start_seconds=scene.shot.source_start_seconds,
                original_end_seconds=scene.shot.source_end_seconds,
                required_seconds=required,
                source_fps=info.fps,
                focus_x=focus_x,
                focus_y=focus_y,
                freeze_source_offset_seconds=freeze_offset,
                freeze_weight=freeze_weight,
                analyzer=cached_analyzer,
            )

        repetition_check: dict[str, object] = {
            "available": history_available,
            "checked": False,
            "strong_match": False,
            "reason": "not_needed",
            "matches": [],
        }
        if decision.changed and not history_available:
            decision = _retain_original(
                decision,
                reason="repetition_history_unavailable",
            )
            repetition_check["reason"] = "history_unavailable"
        elif decision.changed and visual_history:
            try:
                urls = (scene.asset.url,) if scene.asset.url else ()
                fingerprint_key = (
                    str(Path(source).resolve()),
                    round(decision.selected_start_seconds, 6),
                    round(required, 6),
                    urls,
                )
                candidate_fingerprint = fingerprint_cache.get(fingerprint_key)
                if candidate_fingerprint is None:
                    candidate_fingerprint = fingerprint_builder(
                        Path(source),
                        "video",
                        urls=urls,
                        source_start_seconds=decision.selected_start_seconds,
                        source_duration_seconds=required,
                    )
                    fingerprint_cache[fingerprint_key] = candidate_fingerprint
                strong_match, matches = _strong_recent_segment_matches(
                    candidate_fingerprint,
                    visual_history,
                )
                repetition_check.update(
                    {
                        "checked": True,
                        "strong_match": strong_match,
                        "reason": (
                            "strong_recent_match" if strong_match else "no_strong_match"
                        ),
                        "matches": matches,
                    }
                )
                if strong_match:
                    decision = _retain_original(
                        decision,
                        reason="repeated_recent_segment",
                    )
            except Exception:
                decision = _retain_original(
                    decision,
                    reason="repetition_check_unavailable",
                )
                repetition_check.update(
                    {
                        "available": False,
                        "reason": "check_unavailable",
                    }
                )
        elif decision.changed:
            repetition_check["reason"] = "no_history"

        if decision.changed and _overlaps_reserved_window(
            scene.index,
            source_identity(scene.asset.id),
            decision.selected_start_seconds,
            decision.consumed_selected_end_seconds,
            reservations,
        ):
            decision = replace(
                decision,
                selected_start_seconds=decision.original_start_seconds,
                selected_end_seconds=decision.original_end_seconds,
                consumed_selected_end_seconds=(
                    decision.consumed_original_end_seconds
                ),
                selected_score=decision.original_score,
                changed=False,
                reason="overlaps_another_shot",
            )

        selected_scene = scene
        if decision.changed:
            selected_scene = replace(
                scene,
                shot=replace(
                    scene.shot,
                    source_start_seconds=decision.selected_start_seconds,
                    source_end_seconds=decision.selected_end_seconds,
                ),
            )
            changed_count += 1
            print(
                f"[best-segment] shot={scene.shot.id} | "
                f"{decision.original_start_seconds:.3f}s -> "
                f"{decision.selected_start_seconds:.3f}s | "
                f"ganho={decision.gain:.2f}"
            )
        else:
            print(
                f"[best-segment] shot={scene.shot.id} | trim original mantido | "
                f"motivo={decision.reason}"
            )
        scenes.append(selected_scene)
        reservations[scene.index] = (
            source_identity(scene.asset.id),
            decision.selected_start_seconds,
            decision.consumed_selected_end_seconds,
        )
        records.append(
            {
                "shot_id": scene.shot.id,
                "asset_id": scene.asset.id,
                "required_source_seconds": round(required, 6),
                "speed": scene.shot.speed,
                "crossfade_seconds": round(scene.transition_frames / plan.fps, 6),
                "freeze_frame": (
                    {
                        "start_frame": scene.freeze_frame.start_frame,
                        "duration_frames": scene.freeze_frame.duration_frames,
                    }
                    if scene.freeze_frame is not None
                    else None
                ),
                **decision.as_dict(),
                "historical_repetition_check": repetition_check,
            }
        )

    selected_plan = replace(plan, scenes=tuple(scenes))
    payload = {
        "schema_version": 1,
        "mode": "conservative_best_segment",
        "minimum_clear_gain": MIN_CLEAR_GAIN,
        "minimum_confidence": MIN_ANALYSIS_CONFIDENCE,
        "recent_history_episode_limit": RECENT_HISTORY_EPISODES,
        "changed_shots": changed_count,
        "shots": records,
    }
    if history_warnings:
        payload["warnings"] = list(dict.fromkeys(history_warnings))
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(
            "[best-segment] aviso: relatorio nao pode ser salvo "
            f"({type(exc).__name__}); render continuara."
        )
    return selected_plan


def _overlaps_reserved_window(
    scene_index: int,
    source_id: str,
    start: float,
    end: float,
    reservations: Mapping[int, tuple[str, float, float]],
) -> bool:
    duration = max(0.0, end - start)
    if duration <= 0:
        return True
    for other_index, (other_asset, other_start, other_end) in reservations.items():
        if other_index == scene_index or other_asset != source_id:
            continue
        overlap = max(0.0, min(end, other_end) - max(start, other_start))
        shorter = min(duration, max(0.0, other_end - other_start))
        if shorter > 0 and overlap / shorter >= 0.35:
            return True
    return False


def _retain_original(
    decision: BestSegmentDecision,
    *,
    reason: str,
) -> BestSegmentDecision:
    return replace(
        decision,
        selected_start_seconds=decision.original_start_seconds,
        selected_end_seconds=decision.original_end_seconds,
        consumed_selected_end_seconds=decision.consumed_original_end_seconds,
        selected_score=decision.original_score,
        changed=False,
        reason=reason,
    )


def _strong_recent_segment_matches(
    candidate: VisualFingerprint,
    history: Sequence[VisualHistoryEntry],
) -> tuple[bool, list[dict[str, object]]]:
    """Find only strong trim-level matches, ignoring broad same-source matches."""
    recent = tuple(
        entry
        for entry in history
        if isinstance(entry, VisualHistoryEntry)
        and entry.recency_rank < RECENT_HISTORY_EPISODES
        and entry.fingerprint.kind == "video"
    )
    if not recent:
        return False, []

    assessment = assess_repetition(candidate, recent)
    matches: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()

    def add_match(
        entry: VisualHistoryEntry,
        method: str,
        similarity: float,
    ) -> None:
        key = (entry.episode, entry.shot_id, entry.asset_id, method)
        if key in seen:
            return
        seen.add(key)
        matches.append(
            {
                "episode": entry.episode,
                "shot_id": entry.shot_id,
                "asset_id": entry.asset_id,
                "method": method,
                "similarity": round(similarity, 4),
                "recency_rank": entry.recency_rank,
            }
        )

    entries_by_usage = {
        (entry.episode, entry.shot_id, entry.asset_id): entry
        for entry in recent
    }
    for match in assessment.matches:
        if (
            match.method == "video_frames"
            and match.similarity >= STRONG_FRAME_SIMILARITY
        ):
            entry = entries_by_usage.get(
                (match.episode, match.shot_id, match.asset_id)
            )
            if entry is not None:
                add_match(entry, "video_frames", match.similarity)

    for entry in recent:
        previous = entry.fingerprint
        same_source = bool(
            (candidate.sha256 and candidate.sha256 == previous.sha256)
            or set(candidate.url_hashes).intersection(previous.url_hashes)
        )
        overlap = _known_source_overlap(candidate, previous)
        if same_source and overlap is not None and overlap >= STRONG_SOURCE_OVERLAP:
            add_match(entry, "source_range_overlap", overlap)

    matches.sort(
        key=lambda item: (
            int(item["recency_rank"]),
            -float(item["similarity"]),
            str(item["episode"]),
            str(item["shot_id"]),
        )
    )
    return bool(matches), matches


def _known_source_overlap(
    first: VisualFingerprint,
    second: VisualFingerprint,
) -> float | None:
    if (
        first.source_duration_seconds is None
        or second.source_duration_seconds is None
    ):
        return None
    shortest = min(
        first.source_duration_seconds,
        second.source_duration_seconds,
    )
    if shortest <= 0:
        return None
    overlap = min(
        first.source_start_seconds + first.source_duration_seconds,
        second.source_start_seconds + second.source_duration_seconds,
    ) - max(first.source_start_seconds, second.source_start_seconds)
    return max(0.0, min(1.0, overlap / shortest))


def _candidate_rejection_reason(
    baseline: SegmentWindowScore,
    candidate: SegmentWindowScore,
    required_seconds: float,
) -> str | None:
    confidence = min(baseline.confidence, candidate.confidence)
    if confidence < MIN_ANALYSIS_CONFIDENCE:
        return "low_confidence"
    if candidate.metrics.practically_static:
        return "candidate_practically_static"
    if candidate.score < MIN_CANDIDATE_SCORE:
        return "candidate_score_below_floor"
    if candidate.score - baseline.score < MIN_CLEAR_GAIN:
        return "gain_below_threshold"
    conservative_radius = max(8.0, required_seconds * 2.0)
    if (
        abs(candidate.metrics.start_seconds - baseline.metrics.start_seconds)
        > conservative_radius + 1e-6
    ):
        return "outside_conservative_neighborhood"

    deltas = {
        key: candidate.components[key] - baseline.components[key]
        for key in (
            "motion",
            "sharpness",
            "exposure",
            "subject_visibility",
            "stability",
            "scene_changes",
        )
    }
    improved = [
        key for key, delta in deltas.items() if delta >= MIN_COMPONENT_IMPROVEMENT
    ]
    non_motion = [key for key in improved if key != "motion"]
    if len(improved) < 2 or not non_motion:
        return "motion_only_gain"
    for key in ("sharpness", "exposure", "subject_visibility", "stability"):
        if deltas[key] < -MAX_CRITICAL_REGRESSION:
            return "critical_quality_regression"
    return None


def _summarize_rejections(
    reasons: Sequence[str],
    baseline: SegmentWindowScore,
    alternatives: Sequence[SegmentWindowScore],
) -> str:
    if alternatives and all(
        item.metrics.practically_static for item in (baseline, *alternatives)
    ):
        return "source_practically_static"
    priorities = (
        "low_confidence",
        "candidate_practically_static",
        "candidate_score_below_floor",
        "gain_below_threshold",
        "outside_conservative_neighborhood",
        "motion_only_gain",
        "critical_quality_regression",
    )
    for reason in priorities:
        if reason in reasons:
            return reason
    return "no_valid_alternative"


def _kept_decision(
    original_start: float,
    original_end: float | None,
    consumed_end: float,
    *,
    reason: str,
    baseline: SegmentWindowScore | None = None,
    windows: tuple[SegmentWindowScore, ...] = (),
) -> BestSegmentDecision:
    score = baseline.score if baseline is not None else None
    confidence = baseline.confidence if baseline is not None else 0.0
    return BestSegmentDecision(
        original_start_seconds=original_start,
        original_end_seconds=original_end,
        consumed_original_end_seconds=consumed_end,
        selected_start_seconds=original_start,
        selected_end_seconds=original_end,
        consumed_selected_end_seconds=consumed_end,
        original_score=score,
        new_score=None,
        selected_score=score,
        gain=None,
        confidence=confidence,
        changed=False,
        reason=reason,
        windows=windows,
    )


def _sharpness_score(frame: Image.Image) -> float:
    # A slight pre-blur makes sensor noise/grain less likely to masquerade as
    # useful detail in the simple edge-energy proxy.
    edges = frame.filter(ImageFilter.GaussianBlur(0.65)).filter(
        ImageFilter.FIND_EDGES
    )
    if min(edges.size) > 8:
        edges = edges.crop((3, 3, edges.width - 3, edges.height - 3))
    mean_edge = ImageStat.Stat(edges).mean[0]
    return _clamp(mean_edge * 5.0)


def _exposure_score(frame: Image.Image) -> float:
    histogram = frame.histogram()
    pixels = max(1, frame.width * frame.height)
    mean_luma = ImageStat.Stat(frame).mean[0]
    dark_ratio = sum(histogram[:20]) / pixels
    clipped_ratio = sum(histogram[236:]) / pixels
    center_score = 100.0 - abs(mean_luma - 122.0) / 1.22
    return _clamp(center_score - 90.0 * dark_ratio - 70.0 * clipped_ratio)


def _subject_visibility(
    frame: Image.Image,
    focus_x: float,
    focus_y: float,
) -> tuple[float, tuple[float, float]]:
    local = ImageChops.difference(
        frame,
        frame.filter(ImageFilter.GaussianBlur(max(1.0, min(frame.size) / 40))),
    )
    edges = frame.filter(ImageFilter.FIND_EDGES)
    saliency = ImageChops.add(local, edges, scale=1.35)
    histogram = saliency.histogram()
    total = float(sum(value * count for value, count in enumerate(histogram)))
    if total <= 1e-6:
        return 35.0, (focus_x, focus_y)

    box_width = max(1, round(frame.width * 0.56))
    box_height = max(1, round(frame.height * 0.72))
    left = min(
        max(0, round(focus_x * frame.width - box_width / 2)),
        frame.width - box_width,
    )
    top = min(
        max(0, round(focus_y * frame.height - box_height / 2)),
        frame.height - box_height,
    )
    central = saliency.crop((left, top, left + box_width, top + box_height))
    central_mass = float(
        sum(value * count for value, count in enumerate(central.histogram()))
    )
    area_fraction = box_width * box_height / (frame.width * frame.height)
    retained = central_mass / total
    enrichment = retained / max(area_fraction, 1e-6)
    visibility = _clamp(48.0 + (enrichment - 1.0) * 75.0)

    # Weighted saliency centroid supports a simple stability measure without
    # pretending to recognize a face or semantic object.
    preview = saliency.resize((40, 40), Image.Resampling.BILINEAR)
    pixels = preview.load()
    mass = 0.0
    weighted_x = 0.0
    weighted_y = 0.0
    for y in range(preview.height):
        for x in range(preview.width):
            value = float(pixels[x, y])
            mass += value
            weighted_x += value * x
            weighted_y += value * y
    if mass <= 1e-6:
        centroid = (focus_x, focus_y)
    else:
        centroid = (
            weighted_x / mass / max(1, preview.width - 1),
            weighted_y / mass / max(1, preview.height - 1),
        )
    return visibility, centroid


def _frame_difference(left: Image.Image, right: Image.Image) -> float:
    return ImageStat.Stat(ImageChops.difference(left, right)).mean[0]


def _motion_quality_score(value: float) -> float:
    # A perceptible but controlled amount of motion scores best. Extremely high
    # differences are more likely to be shake or frantic cuts than useful B-roll.
    if value <= 12.0:
        return _clamp(value / 12.0 * 100.0)
    return _clamp(100.0 - (value - 12.0) * 2.2)


def _stability_score(
    centroids: Sequence[tuple[float, float]],
    differences: Sequence[float],
) -> float:
    if len(centroids) < 3:
        return 50.0
    velocities = [
        (right[0] - left[0], right[1] - left[1])
        for left, right in zip(centroids, centroids[1:])
    ]
    acceleration = [
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(velocities, velocities[1:])
    ]
    jitter = fmean(acceleration) if acceleration else 0.0
    difference_variability = pstdev(differences) if len(differences) > 1 else 0.0
    return _clamp(100.0 - jitter * 520.0 - difference_variability * 2.0)


def _scene_change_score(differences: Sequence[float]) -> float:
    if not differences:
        return 50.0
    typical = median(differences)
    threshold = max(18.0, typical * 2.4)
    changes = sum(value >= threshold for value in differences)
    if changes == 0:
        return 100.0
    if changes == 1:
        return 90.0
    return _clamp(90.0 - (changes - 1) * 24.0)


def _temporal_consistency(
    sharpness: Sequence[float],
    exposure: Sequence[float],
    subject: Sequence[float],
    differences: Sequence[float],
) -> float:
    variations = []
    for values in (sharpness, exposure, subject):
        if len(values) > 1:
            variations.append(min(1.0, pstdev(values) / 35.0))
    if len(differences) > 1:
        variations.append(min(1.0, pstdev(differences) / 18.0))
    return _clamp(1.0 - (fmean(variations) if variations else 0.5), 0.0, 1.0)


def _robust_temporal_value(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    lower_index = max(0, round((len(ordered) - 1) * 0.20))
    return 0.70 * median(ordered) + 0.30 * ordered[lower_index]


def _robust_temporal_score(values: Sequence[float]) -> float:
    return _clamp(_robust_temporal_value(values))


def _blend(base: float, emphasized: float, weight: float) -> float:
    return _clamp(base * (1.0 - weight) + emphasized * weight)


def _score_value(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(_clamp(float(value)), 2) if math.isfinite(float(value)) else 0.0


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _finite_positive(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _finite_non_negative(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0
    )
