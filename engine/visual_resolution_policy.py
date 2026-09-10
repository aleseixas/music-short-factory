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
    """Block only weak video fallbacks that should never beat a relevant image/existing asset.

    Exact/direct videos remain eligible as render-safe fallbacks if their technical score is
    low. Legacy candidates without semantic_fit keep the previous behavior. Contextual/generic
    videos are blocked only when the authored pool contains a direct/exact alternative tier.
    """
    if practically_static:
        return "practically_static_not_fallbackable"

    fit = str(semantic_fit or "").strip().casefold()
    rank = SEMANTIC_FIT_RANK.get(fit)
    if rank is None or best_semantic_rank is None:
        return None

    if best_semantic_rank >= SEMANTIC_FIT_RANK["direct"] and rank < SEMANTIC_FIT_RANK["direct"]:
        return f"semantic_video_not_fallbackable_{fit}"
    return None
