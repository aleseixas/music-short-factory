"""Small, opt-in visual direction shared by search, editing and publishing.

Profiles are editorial defaults, not claims about an artist or video analysis.
No network/LLM dependency; old stories do not even read the profile catalog.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import unicodedata


PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "artist_vibes.json"
VISUAL_ROLES = frozenset({"hook", "context", "intimacy", "build", "payoff"})
_DEFAULTS = {
    "mood": [],
    "pacing": {"hook_seconds": 2.8, "body_seconds": 3.5, "emotional_seconds": 4.5,
               "payoff_seconds": 4.8, "min_shot_seconds": 1.5, "max_shot_seconds": 8.0},
    "visual_motifs": [],
    "preferred_shot_types": [],
    "emotional_arc": {},
    "what_to_avoid": [],
    "search_terms": [],
    "text_guidance": [],
    "editing": {"motion": "push_in", "transition": "cut", "text_animation": "fade_pop",
                "text_intensity": 0.3, "crossfade_seconds": 0.14},
    "cover": {"text_color": "#FFFFFF", "accent_color": "#FFD43B",
              "background_color": "#101010", "font_name": "Arial"},
}


def profile_catalog_path(project_root: Path) -> Path:
    """Honor project profiles consistently; reusable callers can use shipped defaults."""
    local_path = project_root / "config" / "artist_vibes.json"
    return local_path if local_path.is_file() else PROFILE_PATH


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _merge(base: dict, overrides: Mapping) -> dict:
    result = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _catalog(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Nao foi possivel ler artist vibes em {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("profiles"), dict):
        raise RuntimeError("artist_vibes.json precisa de schema_version=1 e profiles como objeto.")
    return data["profiles"]


def resolve_visual_direction(raw_story: Mapping, profiles_path: Path | None = None) -> dict | None:
    """Resolve explicit profile/auto inference then deep-merge episode overrides.

    artist_vibe=false always opts out. Missing metadata preserves legacy behavior.
    Auto inference uses only known aliases (artist, then title if artist absent);
    unknown/ambiguous artists have no guessed profile. Inline direction still works.
    """
    requested = raw_story.get("artist_vibe")
    overrides = raw_story.get("visual_direction")
    if requested is False:
        return None
    if requested is None and overrides is None:
        return None
    if requested is not None and (not isinstance(requested, str) or not requested.strip()):
        raise RuntimeError("artist_vibe precisa ser um profile, 'auto' ou false.")
    if overrides is not None and not isinstance(overrides, dict):
        raise RuntimeError("visual_direction precisa ser um objeto.")
    overrides = overrides or {}
    unknown = set(overrides) - set(_DEFAULTS)
    if unknown:
        raise RuntimeError(f"Campos desconhecidos em visual_direction: {', '.join(sorted(unknown))}.")
    profile_name = None
    profile = {}
    if requested is not None:
        profiles = _catalog(profiles_path or PROFILE_PATH)
        if requested == "auto":
            identity = _normalized(raw_story.get("artist") or raw_story.get("title", ""))
            matches = []
            for name, candidate in profiles.items():
                if not isinstance(candidate, dict):
                    raise RuntimeError(f"Profile artist vibe invalido: {name}.")
                aliases = candidate.get("aliases", [name])
                _strings(aliases, f"profiles.{name}.aliases")
                if any(f" {_normalized(alias)} " in f" {identity} " for alias in aliases):
                    matches.append(name)
            if len(matches) == 1:
                profile_name = matches[0]
            elif not overrides:
                return None
        else:
            profile_name = requested.strip()
            if profile_name not in profiles:
                raise RuntimeError(f"Artist vibe desconhecida: {profile_name!r}. Profiles: {', '.join(sorted(profiles))}.")
        if profile_name is not None:
            profile = profiles[profile_name]
            if not isinstance(profile, dict):
                raise RuntimeError(f"Profile artist vibe invalido: {profile_name}.")
            profile = {key: value for key, value in profile.items() if key != "aliases"}
            if set(profile) - set(_DEFAULTS):
                raise RuntimeError(f"Profile {profile_name!r} tem campos de direcao desconhecidos.")
    direction = _merge(_merge(_DEFAULTS, profile), overrides)
    _validate(direction)
    return {"profile": profile_name or "custom", **direction}


def _strings(value: object, label: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise RuntimeError(f"{label} precisa ser uma lista de strings nao vazias.")


def _number(value: object, label: str, low: float, high: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise RuntimeError(f"{label} precisa ser numero finito entre {low} e {high}.")


def _validate(direction: dict) -> None:
    for key in ("mood", "visual_motifs", "preferred_shot_types", "what_to_avoid", "search_terms", "text_guidance"):
        _strings(direction[key], f"visual_direction.{key}")
    for key in ("pacing", "emotional_arc", "editing", "cover"):
        if not isinstance(direction[key], dict):
            raise RuntimeError(f"visual_direction.{key} precisa ser um objeto.")
    pacing = direction["pacing"]
    if set(pacing) - set(_DEFAULTS["pacing"]):
        raise RuntimeError("visual_direction.pacing tem campos desconhecidos.")
    for key, value in pacing.items():
        _number(value, f"visual_direction.pacing.{key}", 0.2, 30.0)
    if pacing["min_shot_seconds"] > pacing["max_shot_seconds"]:
        raise RuntimeError("visual_direction.pacing: min_shot_seconds excede max_shot_seconds.")
    for role, beat in direction["emotional_arc"].items():
        if role not in VISUAL_ROLES or not isinstance(beat, dict):
            raise RuntimeError(f"visual_direction.emotional_arc: role invalido {role!r}.")
        for key, value in beat.items():
            if key not in {"mood", "preferred_shot_types", "search_terms"}:
                raise RuntimeError(f"visual_direction.emotional_arc.{role}: campo desconhecido {key}.")
            _strings(value, f"visual_direction.emotional_arc.{role}.{key}")
    editing = direction["editing"]
    if set(editing) - set(_DEFAULTS["editing"]):
        raise RuntimeError("visual_direction.editing tem campos desconhecidos.")
    for key, choices in {
        "motion": {"push_in", "pull_out", "pan_left", "pan_right", "hold"},
        "transition": {"cut", "crossfade"},
        "text_animation": {"pop_in", "scale_bounce", "slide_up", "fade_pop"},
    }.items():
        if not isinstance(editing[key], str) or editing[key] not in choices:
            raise RuntimeError(f"visual_direction.editing.{key} invalido.")
    _number(editing["text_intensity"], "visual_direction.editing.text_intensity", 0.0, 1.0)
    _number(editing["crossfade_seconds"], "visual_direction.editing.crossfade_seconds", 0.01, 1.0)
    cover = direction["cover"]
    if set(cover) - set(_DEFAULTS["cover"]):
        raise RuntimeError("visual_direction.cover tem campos desconhecidos.")
    for key in ("text_color", "accent_color", "background_color"):
        if not isinstance(cover[key], str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", cover[key]):
            raise RuntimeError(f"visual_direction.cover.{key} precisa usar #RRGGBB.")
    if not isinstance(cover["font_name"], str) or not cover["font_name"].strip():
        raise RuntimeError("visual_direction.cover.font_name precisa ser string nao vazia.")


def visual_role_for_segment(story: object, segment_id: str) -> str:
    """Use authored arc role; infer only first/last, leave other beats as context."""
    segments = story.get("segments", ()) if isinstance(story, Mapping) else getattr(story, "segments", ())
    for index, segment in enumerate(segments):
        sid = segment.get("id") if isinstance(segment, Mapping) else segment.id
        if sid != segment_id:
            continue
        role = segment.get("visual_role") if isinstance(segment, Mapping) else getattr(segment, "visual_role", None)
        if role is not None:
            if not isinstance(role, str) or role not in VISUAL_ROLES:
                raise RuntimeError(f"visual_role invalido no segmento {segment_id!r}: {role!r}.")
            return role
        if index == 0:
            return "hook"
        if index == len(segments) - 1:
            return "payoff"
        return "context"
    return "context"


def build_visual_queries(queries: Sequence[str], direction: dict | None, *, role: str = "context", narration: str = "") -> tuple[str, ...]:
    """Keep factual queries verbatim; add a small role-specific search expansion.

    Narration belongs in candidate ranking; appending full narration would drown
    the factual query. Never append avoid terms as positive search keywords.
    """
    original = tuple(queries)
    if direction is None:
        return original
    role_terms = direction.get("emotional_arc", {}).get(role, {}).get("search_terms", [])
    terms = role_terms or direction.get("search_terms", [])
    expanded = list(original)
    for query in original[:2]:
        for term in terms[:2]:
            expanded.append(f"{query} {term}".strip())
    return tuple(dict.fromkeys(expanded))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Mostra a visual direction resolvida, sem rede ou render.")
    parser.add_argument("story", type=Path, help="Caminho para story.json")
    args = parser.parse_args()
    try:
        from .utils import load_json
        print(json.dumps(resolve_visual_direction(load_json(args.story)), ensure_ascii=False, indent=2))
    except RuntimeError as exc:
        parser.exit(1, f"ERRO: {exc}\n")
