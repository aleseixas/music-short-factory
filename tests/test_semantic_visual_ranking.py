from __future__ import annotations

import unittest

import resolve_visual_candidates_web as resolver


class SemanticVisualRankingTests(unittest.TestCase):
    def test_direct_beats_generic_even_with_much_lower_technical_score(self):
        direct = resolver._semantic_ranking_score(
            {"semantic_fit": "direct"},
            10.0,
        )
        generic = resolver._semantic_ranking_score(
            {"semantic_fit": "generic"},
            100.0,
        )
        self.assertGreater(direct, generic)

    def test_exact_beats_direct_even_with_lower_technical_score(self):
        exact = resolver._semantic_ranking_score(
            {"semantic_fit": "exact"},
            0.0,
        )
        direct = resolver._semantic_ranking_score(
            {"semantic_fit": "direct"},
            100.0,
        )
        self.assertGreater(exact, direct)

    def test_same_semantic_tier_still_uses_technical_score(self):
        better = resolver._semantic_ranking_score(
            {"semantic_fit": "contextual"},
            80.0,
        )
        worse = resolver._semantic_ranking_score(
            {"semantic_fit": "contextual"},
            40.0,
        )
        self.assertGreater(better, worse)

    def test_unlabelled_candidate_keeps_legacy_score(self):
        self.assertEqual(
            resolver._semantic_ranking_score({}, 83.25),
            83.25,
        )

    def test_lower_semantic_video_is_gated_when_exact_candidate_exists(self):
        slot = {
            "candidates": [
                {"kind": "video", "semantic_fit": "generic"},
                {"kind": "image", "semantic_fit": "exact"},
            ]
        }
        self.assertEqual(
            resolver._semantic_gate_reason(slot, slot["candidates"][0]),
            "semantic_below_exact",
        )
        self.assertIsNone(
            resolver._semantic_gate_reason(slot, slot["candidates"][1]),
        )

    def test_unlabelled_legacy_pool_has_no_semantic_gate(self):
        slot = {
            "candidates": [
                {"kind": "video"},
                {"kind": "image"},
            ]
        }
        self.assertIsNone(
            resolver._semantic_gate_reason(slot, slot["candidates"][0]),
        )


if __name__ == "__main__":
    unittest.main()
