from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .models import Episode, TimelinePlan


STATIC_IMAGE_MIN_SECONDS = 2.0
STATIC_IMAGE_MAX_SECONDS = 4.0
# Estimated timing runs before TTS, so leave a conservative margin and only
# block cases that are clearly incompatible with the resolved 4s hard cap.
ESTIMATED_STATIC_IMAGE_MAX_SECONDS = 4.75

OPENING_WINDOW_SECONDS = 10.0
OPENING_VIDEO_LONG_HOLD_SECONDS = 4.5
VIDEO_LONG_HOLD_SECONDS = 6.0
MIN_OPENING_TAKES = 3
LONG_SHORT_MIN_SECONDS = 60.0
MIN_TAKES_FOR_LONG_SHORT = 20

# Aim around the middle of the allowed 2-4s image window when suggesting how
# many narrative segments the editor should create.
IMAGE_REPAIR_TARGET_SECONDS = 3.25


@dataclass(frozen=True)
class VisualPacingIssue:
    code: str
    message: str
    blocking: bool = False


@dataclass(frozen=True)
class VisualPacingReport:
    issues: tuple[VisualPacingIssue, ...] = ()

    @property
    def blocking_issues(self) -> tuple[VisualPacingIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def warnings(self) -> tuple[VisualPacingIssue, ...]:
        return tuple(issue for issue in self.issues if not issue.blocking)

    @property
    def ok(self) -> bool:
        return not self.blocking_issues


def evaluate_estimated_visual_pacing(episode: Episode) -> VisualPacingReport:
    """Catch obvious pacing violations before queueing, without TTS timings.

    This stage deliberately blocks only clear static-image overages. The exact
    2-4 second contract is available for real resolved narration timings.
    """
    shots = episode.shots
    segments = episode.story.segments
    if not shots or not segments:
        return VisualPacingReport()

    total_words = sum(_word_count(segment.text) for segment in segments)
    if total_words <= 0:
        return VisualPacingReport()

    target_duration = episode.story.target_duration_seconds
    if not math.isfinite(target_duration) or target_duration <= 0:
        return VisualPacingReport()

    issues: list[VisualPacingIssue] = []
    segments_by_id = {segment.id: segment for segment in segments}
    estimated_starts: list[float] = []
    elapsed = 0.0

    for shot in shots:
        asset = episode.assets.get(shot.asset_id)
        segment = segments_by_id.get(shot.segment_id)
        if asset is None or segment is None:
            continue

        words = _word_count(segment.text)
        if words <= 0:
            continue

        estimated_seconds = target_duration * words / total_words
        estimated_starts.append(elapsed)
        elapsed += estimated_seconds

        if asset.is_video:
            if (
                estimated_starts[-1] < OPENING_WINDOW_SECONDS
                and estimated_seconds > OPENING_VIDEO_LONG_HOLD_SECONDS
            ):
                issues.append(
                    VisualPacingIssue(
                        code="ESTIMATED_OPENING_VIDEO_LONG_HOLD",
                        message=(
                            f"shot={shot.id!r} video={asset.id!r} tende a durar "
                            f"~{estimated_seconds:.2f}s no inicio. Videos podem respirar, "
                            "mas confirme que ha movimento/acao/contexto suficiente."
                        ),
                    )
                )
            elif estimated_seconds > VIDEO_LONG_HOLD_SECONDS:
                issues.append(
                    VisualPacingIssue(
                        code="ESTIMATED_VIDEO_LONG_HOLD",
                        message=(
                            f"shot={shot.id!r} video={asset.id!r} tende a durar "
                            f"~{estimated_seconds:.2f}s. Isso e aceitavel somente se "
                            "o conteudo do video sustentar o plano."
                        ),
                    )
                )
            continue

        if estimated_seconds > ESTIMATED_STATIC_IMAGE_MAX_SECONDS:
            suggested_parts = max(
                2,
                math.ceil(estimated_seconds / IMAGE_REPAIR_TARGET_SECONDS),
            )
            issues.append(
                VisualPacingIssue(
                    code="ESTIMATED_STATIC_HOLD_TOO_LONG",
                    blocking=True,
                    message=(
                        f"shot={shot.id!r} segment={shot.segment_id!r} asset={asset.id!r} "
                        f"e imagem estatica e tende a durar ~{estimated_seconds:.2f}s "
                        f"(alvo editorial: {STATIC_IMAGE_MIN_SECONDS:.1f}-"
                        f"{STATIC_IMAGE_MAX_SECONDS:.1f}s). Divida esse trecho em cerca "
                        f"de {suggested_parts} segmentos/shots narrativos menores e use "
                        "visuais principais diferentes e semanticamente corretos. "
                        "Motion/zoom/crop/FX sobre a mesma imagem nao reiniciam a contagem."
                    ),
                )
            )
        elif estimated_seconds < STATIC_IMAGE_MIN_SECONDS:
            issues.append(
                VisualPacingIssue(
                    code="ESTIMATED_STATIC_HOLD_TOO_SHORT",
                    message=(
                        f"shot={shot.id!r} imagem={asset.id!r} tende a durar "
                        f"~{estimated_seconds:.2f}s. Evite cortes artificiais abaixo "
                        f"de {STATIC_IMAGE_MIN_SECONDS:.1f}s; confirme com os timings reais."
                    ),
                )
            )

    if target_duration >= LONG_SHORT_MIN_SECONDS and len(shots) < MIN_TAKES_FOR_LONG_SHORT:
        issues.append(
            VisualPacingIssue(
                code="ESTIMATED_LOW_TAKE_COUNT",
                message=(
                    f"episodio alvo de {target_duration:.2f}s tem apenas {len(shots)} takes; "
                    f"mire normalmente em pelo menos {MIN_TAKES_FOR_LONG_SHORT} quando "
                    "houver visuais contextuais suficientes."
                ),
            )
        )

    opening_take_count = sum(
        1 for start in estimated_starts if start < OPENING_WINDOW_SECONDS
    )
    if target_duration >= OPENING_WINDOW_SECONDS and opening_take_count < MIN_OPENING_TAKES:
        issues.append(
            VisualPacingIssue(
                code="ESTIMATED_OPENING_LOW_CUT_DENSITY",
                message=(
                    f"os primeiros ~{OPENING_WINDOW_SECONDS:.0f}s teriam apenas "
                    f"{opening_take_count} take(s). Replaneje o hook para mais progressao "
                    "visual se houver assets relevantes."
                ),
            )
        )

    return VisualPacingReport(tuple(issues))


def assert_estimated_visual_pacing(episode: Episode) -> VisualPacingReport:
    report = evaluate_estimated_visual_pacing(episode)
    _raise_if_blocked(report, stage="estimated")
    return report


def evaluate_resolved_visual_pacing(plan: TimelinePlan) -> VisualPacingReport:
    """Validate pacing against the real word-timed timeline."""
    if not plan.scenes or plan.fps <= 0:
        return VisualPacingReport()

    issues: list[VisualPacingIssue] = []
    frame_tolerance = 1.0 / plan.fps + 1e-9

    for scene in plan.scenes:
        duration = scene.frame_count / plan.fps
        if scene.asset.is_video:
            start_seconds = scene.start_frame / plan.fps
            if (
                start_seconds < OPENING_WINDOW_SECONDS
                and duration > OPENING_VIDEO_LONG_HOLD_SECONDS + frame_tolerance
            ):
                issues.append(
                    VisualPacingIssue(
                        code="OPENING_VIDEO_LONG_HOLD",
                        message=(
                            f"shot={scene.shot.id!r} video={scene.asset.id!r} dura "
                            f"{duration:.2f}s dentro dos primeiros "
                            f"{OPENING_WINDOW_SECONDS:.0f}s. Considere um corte mais "
                            "cedo se o movimento/acao nao sustentar o plano."
                        ),
                    )
                )
            elif duration > VIDEO_LONG_HOLD_SECONDS + frame_tolerance:
                issues.append(
                    VisualPacingIssue(
                        code="VIDEO_LONG_HOLD",
                        message=(
                            f"shot={scene.shot.id!r} video={scene.asset.id!r} dura "
                            f"{duration:.2f}s. Isso e permitido somente quando ha "
                            "movimento, contexto ou acao que sustente o plano."
                        ),
                    )
                )
            continue

        if duration > STATIC_IMAGE_MAX_SECONDS + frame_tolerance:
            issues.append(
                VisualPacingIssue(
                    code="STATIC_HOLD_TOO_LONG",
                    blocking=True,
                    message=(
                        f"shot={scene.shot.id!r} imagem={scene.asset.id!r} dura "
                        f"{duration:.2f}s; imagem estatica deve ficar entre "
                        f"{STATIC_IMAGE_MIN_SECONDS:.1f}s e "
                        f"{STATIC_IMAGE_MAX_SECONDS:.1f}s. Divida a narracao em "
                        "segmentos/shots menores e use outro visual principal. "
                        "Motion/zoom/crop/FX nao contam como troca de take."
                    ),
                )
            )
        elif duration < STATIC_IMAGE_MIN_SECONDS - frame_tolerance:
            issues.append(
                VisualPacingIssue(
                    code="STATIC_HOLD_TOO_SHORT",
                    blocking=True,
                    message=(
                        f"shot={scene.shot.id!r} imagem={scene.asset.id!r} dura "
                        f"{duration:.2f}s; imagem estatica deve ficar entre "
                        f"{STATIC_IMAGE_MIN_SECONDS:.1f}s e "
                        f"{STATIC_IMAGE_MAX_SECONDS:.1f}s. Reequilibre a segmentacao "
                        "em vez de acelerar artificialmente o corte."
                    ),
                )
            )

    if plan.duration >= LONG_SHORT_MIN_SECONDS and len(plan.scenes) < MIN_TAKES_FOR_LONG_SHORT:
        issues.append(
            VisualPacingIssue(
                code="LOW_TAKE_COUNT",
                message=(
                    f"video de {plan.duration:.2f}s tem apenas {len(plan.scenes)} takes; "
                    f"para shorts longos, mire normalmente em pelo menos "
                    f"{MIN_TAKES_FOR_LONG_SHORT} quando houver visuais contextuais bons."
                ),
            )
        )

    opening_take_count = sum(
        1
        for scene in plan.scenes
        if scene.start_frame / plan.fps < OPENING_WINDOW_SECONDS
    )
    if plan.duration >= OPENING_WINDOW_SECONDS and opening_take_count < MIN_OPENING_TAKES:
        issues.append(
            VisualPacingIssue(
                code="OPENING_LOW_CUT_DENSITY",
                message=(
                    f"os primeiros {OPENING_WINDOW_SECONDS:.0f}s tem apenas "
                    f"{opening_take_count} take(s). O hook deve ter progressao visual "
                    "mais agressiva quando houver assets contextuais suficientes."
                ),
            )
        )

    return VisualPacingReport(tuple(issues))


def assert_resolved_visual_pacing(plan: TimelinePlan) -> VisualPacingReport:
    report = evaluate_resolved_visual_pacing(plan)
    _raise_if_blocked(report, stage="resolved")
    return report


def _raise_if_blocked(report: VisualPacingReport, *, stage: str) -> None:
    if report.ok:
        return

    details = "\n- ".join(issue.message for issue in report.blocking_issues)
    raise RuntimeError(
        f"VISUAL_PACING_BLOCKED stage={stage}:\n- {details}\n"
        "Repare ESTE MESMO episodio: preserve a historia, divida segmentos longos "
        "quando necessario, atribua visuais principais diferentes e relevantes e "
        "rode novamente o preflight. Nao tente contornar o gate apenas com "
        "motion/zoom/crop/FX."
    )


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))
