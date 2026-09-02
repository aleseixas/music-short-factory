from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from pathlib import Path
import re

from .assets import OVERLAY_EXTENSIONS
from .models import (
    OVERLAY_ANIMATIONS,
    OVERLAY_POSITIONS,
    TEXT_FX_ANIMATIONS,
    TEXT_FX_POSITIONS,
    VISUAL_FX_TYPES,
    Episode,
    ResolvedTextFxCue,
    TimelinePlan,
)
from .music import MUSIC_CATALOG
from .sfx import SFX_CATALOG
from .timeline import resolve_text_fx_cues
from .utils import load_json, validate_schema


@dataclass(frozen=True)
class EditorialCatalogs:
    music_profiles: frozenset[str] = frozenset()
    sfx_types: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EditorialWarning:
    code: str
    message: str


@dataclass(frozen=True)
class EditorialReport:
    warnings: tuple[EditorialWarning, ...] = ()

    @property
    def warning_codes(self) -> frozenset[str]:
        return frozenset(warning.code for warning in self.warnings)


def load_editorial_catalogs(
    project_root: Path,
    *,
    require_music: bool = True,
    require_sfx: bool = True,
) -> EditorialCatalogs:
    """Read the choices an external editorial agent is allowed to use."""
    project_root = project_root.resolve()
    profiles = (
        _catalog_names(project_root / MUSIC_CATALOG, "profiles", "background music")
        if require_music
        else frozenset()
    )
    sfx_types = (
        _catalog_names(project_root / SFX_CATALOG, "types", "SFX")
        if require_sfx
        else frozenset()
    )
    return EditorialCatalogs(profiles, sfx_types)


def validate_editorial_direction(
    episode: Episode,
    plan: TimelinePlan,
    catalogs: EditorialCatalogs,
    *,
    highlight_default_duration: float | None = None,
    resolved_text_fx_cues: tuple[ResolvedTextFxCue, ...] | None = None,
) -> EditorialReport:
    """Validate renderer constraints and report non-blocking editorial excesses.

    This function never chooses an effect. It only checks decisions already
    written into timeline.json. Schema/rendering conflicts are errors; style and
    density observations are warnings.
    """
    video_duration = plan.duration
    text_fx_cues = (
        resolve_text_fx_cues(episode.text_fx_cues, plan)
        if resolved_text_fx_cues is None
        else resolved_text_fx_cues
    )
    errors: list[str] = []
    if not math.isfinite(video_duration) or video_duration <= 0:
        errors.append("video_duration precisa ser positivo e finito")
    if highlight_default_duration is not None and (
        not math.isfinite(highlight_default_duration)
        or highlight_default_duration <= 0
    ):
        errors.append("highlight_default_duration precisa ser positivo e finito")

    if episode.background_music is not None:
        if episode.background_music.profile not in catalogs.music_profiles:
            errors.append(
                f"profile de background music inexistente: {episode.background_music.profile!r}"
            )
        _check_unit_interval(
            episode.background_music.volume,
            "background_music.volume",
            errors,
        )

    for index, cue in enumerate(episode.sfx_cues, start=1):
        label = f"sfx_cues[{index}]"
        if cue.type not in catalogs.sfx_types:
            errors.append(f"{label}.type inexistente no catalogo: {cue.type!r}")
        _check_unit_interval(cue.volume, f"{label}.volume", errors)
        if not math.isfinite(cue.time_seconds) or not 0 <= cue.time_seconds < video_duration:
            errors.append(f"{label}.time_seconds precisa ficar dentro do video")
        if (
            not math.isfinite(cue.source_start_seconds)
            or cue.source_start_seconds < 0
        ):
            errors.append(
                f"{label}.source_start_seconds precisa ser maior ou igual a zero"
            )
        if cue.duration_seconds is not None and (
            not math.isfinite(cue.duration_seconds) or cue.duration_seconds <= 0
        ):
            errors.append(f"{label}.duration_seconds precisa ser maior que zero")

    for index, cue in enumerate(episode.visual_fx_cues, start=1):
        label = f"visual_fx_cues[{index}]"
        if cue.type not in VISUAL_FX_TYPES:
            errors.append(f"{label}.type desconhecido: {cue.type!r}")
        _check_visual_interval(
            cue.start_seconds,
            cue.end_seconds,
            video_duration,
            label,
            errors,
        )
        _check_unit_interval(cue.intensity, f"{label}.intensity", errors)

    if len(text_fx_cues) != len(episode.text_fx_cues):
        errors.append(
            "a quantidade de text_fx_cues resolvidas difere da especificacao "
            "declarativa"
        )

    for index, cue in enumerate(text_fx_cues, start=1):
        label = f"text_fx_cues[{index}]"
        if cue.animation not in TEXT_FX_ANIMATIONS:
            errors.append(f"{label}.animation desconhecida: {cue.animation!r}")
        if cue.position not in TEXT_FX_POSITIONS:
            errors.append(f"{label}.position desconhecida: {cue.position!r}")
        _check_interval(cue.start_seconds, cue.end_seconds, video_duration, label, errors)
        _check_unit_interval(cue.intensity, f"{label}.intensity", errors)
        if not cue.text.strip():
            errors.append(f"{label}.text precisa ser nao vazio")
        if cue.accent_text and cue.accent_text.casefold() not in cue.text.casefold():
            errors.append(f"{label}.accent_text precisa aparecer em text")

    for index, cue in enumerate(episode.overlay_cues, start=1):
        label = f"overlay_cues[{index}]"
        asset = episode.assets.get(cue.asset_id)
        if asset is None:
            errors.append(f"{label}.asset inexistente: {cue.asset_id!r}")
        elif Path(asset.file).suffix.lower() not in OVERLAY_EXTENSIONS:
            errors.append(f"{label}.asset nao usa formato de overlay suportado: {asset.file!r}")
        if cue.animation not in OVERLAY_ANIMATIONS:
            errors.append(f"{label}.animation desconhecida: {cue.animation!r}")
        if cue.position not in OVERLAY_POSITIONS:
            errors.append(f"{label}.position desconhecida: {cue.position!r}")
        _check_interval(cue.start_seconds, cue.end_seconds, video_duration, label, errors)
        if not math.isfinite(cue.scale) or not 0.10 <= cue.scale <= 0.80:
            errors.append(f"{label}.scale precisa ficar entre 0.10 e 0.80")
        _check_unit_interval(cue.opacity, f"{label}.opacity", errors)

    for shot in episode.shots:
        if shot.asset_id not in episode.assets:
            errors.append(f"shot {shot.id!r} referencia asset inexistente: {shot.asset_id!r}")

    _check_overlaps(episode.visual_fx_cues, "visual_fx_cues", errors)
    _check_overlaps(text_fx_cues, "text_fx_cues", errors)
    _check_overlaps(episode.overlay_cues, "overlay_cues", errors)
    for scene in plan.scenes:
        if len(scene.visual_fx_cues) > 1:
            errors.append(
                f"shot {scene.shot.id!r} recebe mais de uma visual_fx_cue"
            )

    if errors:
        raise RuntimeError(
            "Direcao editorial invalida:\n- " + "\n- ".join(dict.fromkeys(errors))
        )

    warnings = _editorial_warnings(
        episode,
        video_duration,
        plan,
        highlight_default_duration,
        text_fx_cues,
    )
    return EditorialReport(tuple(warnings))


def _catalog_names(path: Path, key: str, label: str) -> frozenset[str]:
    if not path.is_file():
        raise RuntimeError(f"Catalogo de {label} ausente: {path}")
    data = load_json(path)
    validate_schema(data, path)
    values = data.get(key)
    if not isinstance(values, dict):
        raise RuntimeError(f"Catalogo de {label} precisa conter um objeto em {key!r}.")
    return frozenset(str(name) for name, files in values.items() if isinstance(files, list) and files)


def _check_visual_interval(
    start: float,
    end: float,
    duration: float,
    label: str,
    errors: list[str],
) -> None:
    """Allow visual FX to overrun the tail; timeline rendering clips them."""
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or start >= duration
        or end <= start
    ):
        errors.append(f"{label} precisa comecar dentro do video e ter intervalo valido")


def _check_interval(
    start: float,
    end: float,
    duration: float,
    label: str,
    errors: list[str],
) -> None:
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
        or end > duration
    ):
        errors.append(f"{label} precisa usar um intervalo valido dentro do video")


def _check_unit_interval(value: float, label: str, errors: list[str]) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
        errors.append(f"{label} precisa ficar entre 0 e 1")


def _check_overlaps(cues: tuple[object, ...], label: str, errors: list[str]) -> None:
    ordered = sorted(
        enumerate(cues, start=1),
        key=lambda item: getattr(item[1], "start_seconds"),
    )
    for (left_index, left), (right_index, right) in zip(ordered, ordered[1:]):
        if getattr(right, "start_seconds") < getattr(left, "end_seconds"):
            errors.append(f"{label} sobrepostas: cues {left_index} e {right_index}")


def _editorial_warnings(
    episode: Episode,
    duration: float,
    plan: TimelinePlan,
    highlight_default_duration: float | None,
    text_fx_cues: tuple[ResolvedTextFxCue, ...],
) -> list[EditorialWarning]:
    warnings: list[EditorialWarning] = []

    def warn(condition: bool, code: str, message: str) -> None:
        if condition:
            warnings.append(EditorialWarning(code, message))

    limits = {
        "sfx": 25,
        "visual": _scaled_budget_limit(10, duration),
        "text": _scaled_budget_limit(10, duration),
        "overlay": _scaled_budget_limit(6, duration),
        "punch": _scaled_budget_limit(4, duration),
    }
    punch_count = sum(cue.type == "punch_zoom" for cue in episode.visual_fx_cues)
    warn(
        len(episode.sfx_cues) > limits["sfx"],
        "sfx_count_high",
        f"Mais de {limits['sfx']} SFX para esta duracao; revise ritmo, repeticao e clareza do mix.",
    )
    warn(
        len(episode.visual_fx_cues) > limits["visual"],
        "visual_fx_count_high",
        f"Mais de {limits['visual']} visual_fx explicitos para esta duracao.",
    )
    warn(
        len(text_fx_cues) > limits["text"],
        "text_fx_count_high",
        f"Mais de {limits['text']} text_fx editoriais para esta duracao.",
    )
    warn(
        len(episode.overlay_cues) > limits["overlay"],
        "overlay_count_high",
        f"Mais de {limits['overlay']} overlays graficos para esta duracao.",
    )
    warn(
        punch_count > limits["punch"],
        "punch_zoom_count_high",
        f"Mais de {limits['punch']} punch_zoom para esta duracao; reserve-os para reveals.",
    )

    ordered_sfx = sorted(episode.sfx_cues, key=lambda cue: cue.time_seconds)
    clustered_sfx = any(
        third.time_seconds - first.time_seconds <= 1.0
        for first, third in zip(ordered_sfx, ordered_sfx[2:])
    )
    warn(
        clustered_sfx,
        "sfx_clustered",
        "Tres ou mais SFX aparecem dentro de uma janela de 1 segundo.",
    )
    sfx_types = [cue.type for cue in ordered_sfx]
    repeated_sfx = _longest_run(sfx_types) >= 4
    if len(sfx_types) >= 5:
        repeated_sfx = repeated_sfx or max(Counter(sfx_types).values()) / len(sfx_types) >= 0.70
    warn(repeated_sfx, "sfx_repetition", "O mesmo SFX é repetido em excesso.")

    visual_types = [cue.type for cue in episode.visual_fx_cues]
    warn(
        len(visual_types) >= 6
        and max(Counter(visual_types).values(), default=0) / len(visual_types) >= 0.70,
        "visual_fx_repetition",
        "O mesmo visual_fx domina a edição.",
    )
    text_animations = [cue.animation for cue in text_fx_cues]
    warn(
        len(text_animations) >= 4
        and text_animations.count("scale_bounce") / len(text_animations) >= 0.75,
        "scale_bounce_repetition",
        "scale_bounce aparece em quase todos os text_fx.",
    )
    overlay_assets = [cue.asset_id for cue in episode.overlay_cues]
    warn(
        len(overlay_assets) >= 4
        and max(Counter(overlay_assets).values(), default=0) / len(overlay_assets) >= 0.75,
        "overlay_repetition",
        "O mesmo asset de overlay é repetido em excesso.",
    )

    for index, cue in enumerate(text_fx_cues, start=1):
        words = re.findall(r"\S+", cue.text)
        if len(words) > 6 or len(" ".join(words)) > 42:
            warnings.append(
                EditorialWarning(
                    "kinetic_text_long",
                    f"text_fx_cues[{index}] é longo; prefira 2–6 palavras ou uma estatística curta.",
                )
            )

    _append_timeline_warnings(
        episode,
        plan,
        warnings,
        highlight_default_duration,
        text_fx_cues,
    )
    return warnings


def _scaled_budget_limit(base_limit: int, duration: float) -> int:
    """Keep the 60-90s guideline and scale it outside that duration band."""
    if 60 <= duration <= 90:
        return base_limit
    reference = 60 if duration < 60 else 90
    return max(1, math.ceil(base_limit * duration / reference))


def _append_timeline_warnings(
    episode: Episode,
    plan: TimelinePlan,
    warnings: list[EditorialWarning],
    highlight_default_duration: float | None,
    text_fx_cues: tuple[ResolvedTextFxCue, ...],
) -> None:
    punch_shots: list[tuple[int, int]] = []
    for scene in plan.scenes:
        for cue in scene.visual_fx_cues:
            if cue.type == "punch_zoom":
                punch_shots.append((cue.index, scene.index))
    first_shot_by_cue: dict[int, int] = {}
    for cue_index, shot_index in punch_shots:
        first_shot_by_cue.setdefault(cue_index, shot_index)
    ordered = sorted(first_shot_by_cue.items(), key=lambda item: item[1])
    if any(right_shot == left_shot + 1 for (_, left_shot), (_, right_shot) in zip(ordered, ordered[1:])):
        warnings.append(
            EditorialWarning(
                "consecutive_punch_zoom",
                "punch_zoom aparece em shots consecutivos.",
            )
        )

    seen_duplicates: set[tuple[int, int]] = set()
    for scene in plan.scenes:
        highlight = scene.shot.highlight
        if highlight is None:
            continue
        start = scene.start_frame / plan.fps + highlight.start_seconds
        highlight_duration = highlight.duration_seconds or highlight_default_duration
        end = (
            min(scene.end_frame / plan.fps, start + highlight_duration)
            if highlight_duration is not None
            else start
        )
        normalized_highlight = _normalize_editorial_text(highlight.text)
        for text_index, cue in enumerate(text_fx_cues, start=1):
            overlaps = (
                cue.start_seconds < end and start < cue.end_seconds
                if end > start
                else cue.start_seconds <= start < cue.end_seconds
            )
            if overlaps:
                if normalized_highlight == _normalize_editorial_text(cue.text):
                    key = (scene.index, text_index)
                    if key not in seen_duplicates:
                        seen_duplicates.add(key)
                        warnings.append(
                            EditorialWarning(
                                "duplicate_editorial_text",
                                f"highlight do shot {scene.shot.id!r} duplica text_fx_cues[{text_index}] no mesmo momento.",
                            )
                        )


def _normalize_editorial_text(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _longest_run(values: list[str]) -> int:
    longest = current = 0
    previous: str | None = None
    for value in values:
        current = current + 1 if value == previous else 1
        longest = max(longest, current)
        previous = value
    return longest