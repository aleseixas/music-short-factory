from __future__ import annotations


SEMANTIC_FIT_RANK = {
    "generic": 0,
    "contextual": 1,
    "direct": 2,
    "exact": 3,
}

# Soft diversity pressure inside one episode. The existing resolver still owns
# the hard ceiling of three selected segments per video source.
REUSE_PENALTY_BY_PRIOR_USES = {
    0: 0.0,
    1: 10.0,
    2: 20.0,
}


def reuse_penalty_for_prior_uses(prior_uses: int) -> float:
    """Return the soft ranking penalty for a video already selected in this episode."""
    uses = max(0, int(prior_uses))
    if uses in REUSE_PENALTY_BY_PRIOR_USES:
        return REUSE_PENALTY_BY_PRIOR_USES[uses]
    return REUSE_PENALTY_BY_PRIOR_USES[2]


def video_fallback_block_reason(
    *,
    semantic_fit: str | None,
    best_semantic_rank: int | None,
    practically_static: bool,
) -> str | None:
    """Allow only semantically direct/exact moving footage.

    A video is valuable only when it actually shows the artist, band, event, person,
    place, performance or action tied to the narrated beat. Contextual/generic footage
    must never be selected merely to keep the screen moving; a relevant image is better.
    Candidates without explicit semantic_fit are also blocked because their relevance
    cannot be proven. ``best_semantic_rank`` remains in the signature for compatibility
    with existing resolver callers, but no longer weakens this quality gate.
    """
    del best_semantic_rank

    if practically_static:
        return "practically_static_not_fallbackable"

    fit = str(semantic_fit or "").strip().casefold()
    if not fit:
        return "semantic_video_unlabelled_not_allowed"
    if fit not in {"direct", "exact"}:
        if fit in SEMANTIC_FIT_RANK:
            return f"semantic_video_not_allowed_{fit}"
        return "semantic_video_unknown_fit_not_allowed"
    return None
