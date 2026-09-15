"""Small, explainable editorial ranking; this module does not inspect video pixels."""
from __future__ import annotations

from pathlib import Path
import re
import unicodedata

from .utils import load_json


DIMENSION_WEIGHTS = {
    "vibe_match": 0.30,
    "emotional_strength": 0.20,
    "cinematic_value": 0.15,
    "storytelling_value": 0.15,
    "narration_fit": 0.20,
}
MAX_VIBE_ADJUSTMENT = 24.0
SEMANTIC_FIT_RANK = {"generic": 0, "contextual": 1, "direct": 2, "exact": 3}
SEMANTIC_RANKING_BAND = 25.0
SEMANTIC_TECHNICAL_SPAN = 24.99
_STOP_WORDS = frozenset(
    "a ao aos as com da das de do dos e ela ele em era foi for from in is it na nao "
    "nas no nos o of on os ou para por que se sua seu the to um uma with www https "
    "video videos official music live show".split()
)


def _normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(
        character for character in text if not unicodedata.combining(character)
    )))


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _explicit_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Bounds also reject NaN/Infinity and avoid converting oversized integers.
    return round(float(value), 2) if 0 <= value <= 100 else None


def _matching_terms(terms: list[str], text: str) -> list[str]:
    # Phrase matches also handle identifiers such as crowd_singalong / close_up.
    padded = f" {text} "
    return [term for term in dict.fromkeys(terms)
            if _normalized(term) and f" {_normalized(term)} " in padded]


def score_visual_candidate(
    candidate: dict,
    direction: dict | None,
    *,
    role: str = "context",
    narration: str = "",
) -> dict | None:
    """Score only documented evidence, retaining unknown dimensions as null.

    Authored visual_metadata supplies quality ratings for this episode/slot (0..100). Search titles,
    descriptions and tags supply weaker lexical evidence for mood, shot types and
    narration. matched_queries are deliberately excluded: a query is not evidence
    that the returned take contains what was searched for.
    """
    if not direction:
        return None
    metadata = candidate.get("visual_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    descriptions = [str(candidate.get(key) or "") for key in ("name", "description")]
    for container, keys in (
        (candidate, ("tags",)),
        (metadata, ("mood", "motifs", "shot_types", "story_roles", "avoid_tags", "description")),
    ):
        for key in keys:
            descriptions.extend(_strings(container.get(key)))
    text = _normalized(" ".join(descriptions))
    arc = direction.get("emotional_arc", {})
    arc = arc.get(role, {}) if isinstance(arc, dict) else {}
    preferred = []
    for container, keys in (
        (direction, ("mood", "visual_motifs", "preferred_shot_types")),
        (arc, ("mood", "preferred_shot_types")),
    ):
        if isinstance(container, dict):
            for key in keys:
                preferred.extend(_strings(container.get(key)))
    matched = _matching_terms(preferred, text)
    role_terms = [term for key in ("mood", "preferred_shot_types")
                  for term in _strings(arc.get(key))] if isinstance(arc, dict) else []
    role_matches = _matching_terms(role_terms, text)
    avoid = _matching_terms(_strings(direction.get("what_to_avoid")), text)
    role_match = role in _strings(metadata.get("story_roles"))
    dimensions = {key: _explicit_score(metadata.get(key)) for key in DIMENSION_WEIGHTS}
    evidence = {key: "editorial_metadata" for key, value in dimensions.items() if value is not None}
    if dimensions["vibe_match"] is None and (matched or avoid or role_match):
        dimensions["vibe_match"] = round(max(0.0, min(
            100.0, 50.0 + min(30.0, 6.0 * len(matched))
            + min(20.0, 10.0 * len(role_matches)) + 10.0 * role_match - 20.0 * len(avoid)
        )), 2)
        evidence["vibe_match"] = "metadata_keyword_match"
    narration_terms = (set(_normalized(narration).split()) - _STOP_WORDS
                       - set(_normalized(direction.get("profile")).split()))
    overlap = sorted(narration_terms.intersection(set(text.split())))
    if dimensions["narration_fit"] is None and overlap:
        dimensions["narration_fit"] = round(min(100.0, 50.0 + 50.0 * len(overlap) / max(1, len(narration_terms))), 2)
        evidence["narration_fit"] = "narration_keyword_match"
    # Unknown dimensions contribute zero adjustment, never a made-up rating.
    adjustment = sum(
        weight * ((dimensions[key] - 50.0) / 50.0) * MAX_VIBE_ADJUSTMENT
        for key, weight in DIMENSION_WEIGHTS.items() if dimensions[key] is not None
    ) - min(MAX_VIBE_ADJUSTMENT, 12.0 * len(avoid))
    return {
        "profile": direction.get("profile"),
        "role": role,
        "dimensions": dimensions,
        "adjustment": round(max(-MAX_VIBE_ADJUSTMENT, min(MAX_VIBE_ADJUSTMENT, adjustment)), 2),
        "evidence": evidence,
        "matched_terms": matched,
        "role_matches": role_matches,
        "avoid_matches": avoid,
        "narration_matches": overlap,
        "review_notes": str(metadata.get("review_notes") or ""),
        "requires_visual_review": True,
    }


def apply_vibe_adjustment(technical_score: float, assessment: dict | None) -> float:
    if not assessment:
        return technical_score
    return round(max(0.0, min(100.0, technical_score + assessment["adjustment"])), 2)


def semantic_selection_score(technical_score: float, semantic_fit: object) -> float:
    """Keep explicit factual tiers in non-overlapping bands after editorial scoring."""
    technical = max(0.0, min(100.0, technical_score))
    rank = SEMANTIC_FIT_RANK.get(str(semantic_fit or "").strip().casefold())
    if rank is None:
        return round(technical, 2)
    return round(min(99.99, rank * SEMANTIC_RANKING_BAND
                     + technical / 100.0 * SEMANTIC_TECHNICAL_SPAN), 2)


def load_search_visual_context(
    project_root: Path,
    episode: str | None,
    *,
    segment: str | None = None,
    role: str | None = None,
    narration: str = "",
) -> tuple[dict | None, str, str]:
    """Read optional story metadata without loading assets or touching the network."""
    from .artist_vibe import profile_catalog_path, resolve_visual_direction, visual_role_for_segment

    if not episode:
        return None, role or "context", narration
    episodes_root = (project_root / "episodes").resolve()
    directory = (episodes_root / episode).resolve()
    try:
        directory.relative_to(episodes_root)
    except ValueError as exc:
        raise RuntimeError("Episodio fora da pasta de episodios.") from exc
    path = directory / "story.json"
    if not path.is_file():
        return None, role or "context", narration
    story = load_json(path)
    direction = resolve_visual_direction(story, profiles_path=profile_catalog_path(project_root))
    selected = next((item for item in story.get("segments", [])
                     if isinstance(item, dict) and item.get("id") == segment), {})
    return (
        direction,
        role or (visual_role_for_segment(story, segment) if selected else "context"),
        narration or str(selected.get("text") or ""),
    )
