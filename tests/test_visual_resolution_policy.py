from __future__ import annotations

import unittest

from engine.visual_resolution_policy import (
    reuse_penalty_for_prior_uses,
    video_fallback_block_reason,
)


class VisualResolutionPolicyTests(unittest.TestCase):
    def test_reuse_penalty_is_progressive_before_hard_limit(self):
        self.assertEqual(reuse_penalty_for_prior_uses(0), 0.0)
        self.assertEqual(reuse_penalty_for_prior_uses(1), 10.0)
        self.assertEqual(reuse_penalty_for_prior_uses(2), 20.0)

    def test_practically_static_video_is_never_a_fallback(self):
        self.assertEqual(
            video_fallback_block_reason(
                semantic_fit="exact",
                best_semantic_rank=3,
                practically_static=True,
            ),
            "practically_static_not_fallbackable",
        )

    def test_contextual_video_cannot_fallback_when_direct_or_exact_exists(self):
        self.assertEqual(
            video_fallback_block_reason(
                semantic_fit="contextual",
                best_semantic_rank=2,
                practically_static=False,
            ),
            "semantic_video_not_fallbackable_contextual",
        )
        self.assertEqual(
            video_fallback_block_reason(
                semantic_fit="generic",
                best_semantic_rank=3,
                practically_static=False,
            ),
            "semantic_video_not_fallbackable_generic",
        )

    def test_direct_video_can_remain_render_safe_fallback_if_exact_fails(self):
        self.assertIsNone(
            video_fallback_block_reason(
                semantic_fit="direct",
                best_semantic_rank=3,
                practically_static=False,
            )
        )

    def test_contextual_video_is_allowed_when_contextual_is_best_authored_tier(self):
        self.assertIsNone(
            video_fallback_block_reason(
                semantic_fit="contextual",
                best_semantic_rank=1,
                practically_static=False,
            )
        )

    def test_legacy_candidate_without_semantic_fit_keeps_previous_behavior(self):
        self.assertIsNone(
            video_fallback_block_reason(
                semantic_fit=None,
                best_semantic_rank=3,
                practically_static=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
