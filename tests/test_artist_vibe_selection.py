from __future__ import annotations

from dataclasses import replace
from contextlib import redirect_stdout
import io
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import resolve_visual_candidates as resolver
import resolve_visual_candidates_web as web_resolver
from engine.visual_search import VisualInspection, VisualSearchResult, search_visual
from engine.visual_search_web import rank_inspections_for_selection
from engine.visual_vibe_scoring import apply_vibe_adjustment, load_search_visual_context, score_visual_candidate


DIRECTION = {
    "profile": "taylor_swift",
    "mood": ["emotional", "nostalgic", "cinematic"],
    "visual_motifs": ["diary", "crowd_singalong"],
    "preferred_shot_types": ["close_up", "acoustic_performance"],
    "emotional_arc": {
        "hook": {"mood": ["intimate"], "preferred_shot_types": ["close_up"], "search_terms": ["intimate close up"]},
        "payoff": {"mood": ["epic"], "preferred_shot_types": ["crowd_singalong"], "search_terms": ["crowd singalong"]},
    },
    "what_to_avoid": ["chaotic", "aggressive_editing"],
    "search_terms": ["acoustic performance"],
}


def candidate(name: str, identifier: str = "candidate") -> VisualSearchResult:
    return VisualSearchResult(
        provider_id=identifier, name=name, kind="video", source="fixture",
        source_page_url=f"https://example.com/{identifier}", creator="",
        license="", license_url="", attribution="",
    )


def inspection(result: VisualSearchResult, score: float) -> VisualInspection:
    return VisualInspection(
        result=result, path=Path("unused.mp4"), width=720, height=1280,
        aspect_ratio=0.5625, duration_seconds=12.0, fps=30.0,
        motion=None, trim=None, visual_score=score, score_breakdown={},
    )


class ArtistVibeScoringTests(unittest.TestCase):
    def test_absent_direction_keeps_legacy_technical_score(self):
        scored = score_visual_candidate({"name": "emotional"}, None)
        self.assertIsNone(scored)
        self.assertEqual(apply_vibe_adjustment(83.25, scored), 83.25)

    def test_unknown_quality_is_not_invented_from_search_queries(self):
        scored = score_visual_candidate({
            "name": "Taylor Swift", "matched_queries": ["emotional cinematic diary close up"],
        }, DIRECTION)
        self.assertTrue(all(value is None for value in scored["dimensions"].values()))
        self.assertEqual(scored["adjustment"], 0)
        self.assertEqual(scored["evidence"], {})

    def test_emotional_cinematic_take_beats_aggressive_take_in_same_factual_tier(self):
        tender = {"semantic_fit": "direct", "name": "Taylor Swift emotional cinematic close up", "visual_metadata": {
            "emotional_strength": 92, "cinematic_value": 90, "storytelling_value": 88,
            "narration_fit": 92, "review_notes": "Fixture editorial assessment.",
        }}
        chaotic = {"semantic_fit": "direct", "name": "Taylor Swift chaotic aggressive editing"}
        tender["_artist_vibe_score"] = score_visual_candidate(tender, DIRECTION, role="hook")
        chaotic["_artist_vibe_score"] = score_visual_candidate(chaotic, DIRECTION, role="hook")
        self.assertGreater(
            web_resolver._semantic_ranking_score(tender, 70),
            web_resolver._semantic_ranking_score(chaotic, 90),
        )
        self.assertEqual(tender["_artist_vibe_score"]["dimensions"]["emotional_strength"], 92)
        self.assertIn("aggressive_editing", chaotic["_artist_vibe_score"]["avoid_matches"])

    def test_artist_vibe_cannot_override_higher_factual_tier(self):
        excellent_generic = {"semantic_fit": "generic", "_artist_vibe_score": {"adjustment": 24}}
        weak_exact = {"semantic_fit": "exact", "_artist_vibe_score": {"adjustment": -24}}
        self.assertGreater(
            web_resolver._semantic_ranking_score(weak_exact, 0),
            web_resolver._semantic_ranking_score(excellent_generic, 100),
        )

    def test_role_and_current_narration_change_take_score(self):
        close = {"name": "Taylor Swift intimate close up piano", "visual_metadata": {"story_roles": ["hook"]}}
        crowd = {"name": "Taylor Swift epic crowd singalong", "visual_metadata": {"story_roles": ["payoff"]}}
        close_hook = score_visual_candidate(close, DIRECTION, role="hook", narration="intimate piano")
        crowd_hook = score_visual_candidate(crowd, DIRECTION, role="hook", narration="intimate piano")
        close_payoff = score_visual_candidate(close, DIRECTION, role="payoff", narration="epic crowd singalong")
        crowd_payoff = score_visual_candidate(crowd, DIRECTION, role="payoff", narration="epic crowd singalong")
        self.assertGreater(close_hook["adjustment"], crowd_hook["adjustment"])
        self.assertGreater(crowd_payoff["adjustment"], close_payoff["adjustment"])

    def test_role_specific_shot_changes_ranking_even_when_both_are_global_preferences(self):
        from engine.artist_vibe import resolve_visual_direction

        direction = resolve_visual_direction({"artist_vibe": "taylor_swift"})
        close = {"name": "Taylor Swift close-up"}
        wide = {"name": "Taylor Swift wide stage"}
        self.assertGreater(
            score_visual_candidate(close, direction, role="hook")["adjustment"],
            score_visual_candidate(wide, direction, role="hook")["adjustment"],
        )
        self.assertGreater(
            score_visual_candidate(wide, direction, role="payoff")["adjustment"],
            score_visual_candidate(close, direction, role="payoff")["adjustment"],
        )

    def test_invalid_editorial_scores_are_unknown(self):
        scored = score_visual_candidate({"visual_metadata": {
            "emotional_strength": True, "cinematic_value": float("nan"),
            "storytelling_value": 200, "narration_fit": "90",
        }}, DIRECTION)
        self.assertTrue(all(value is None for value in scored["dimensions"].values()))
        self.assertIsNone(score_visual_candidate({"visual_metadata": {
            "emotional_strength": 10 ** 999,
        }}, DIRECTION)["dimensions"]["emotional_strength"])
        self.assertEqual(score_visual_candidate({"visual_metadata": []}, DIRECTION)["adjustment"], 0)

    def test_search_context_reads_bom_and_segment_role_without_loading_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            episode = root / "episodes" / "taylor"
            episode.mkdir(parents=True)
            (episode / "story.json").write_text(json.dumps({
                "artist_vibe": "taylor_swift", "segments": [
                    {"id": "first", "text": "an intimate memory"},
                    {"id": "ending", "text": "the crowd sings along"},
                ],
            }), encoding="utf-8-sig")
            direction, role, narration = load_search_visual_context(root, "taylor", segment="ending")
            self.assertEqual(direction["profile"], "taylor_swift")
            self.assertEqual(role, "payoff")
            self.assertEqual(narration, "the crowd sings along")
            (root / "config").mkdir()
            (root / "config" / "artist_vibes.json").write_text(json.dumps({
                "schema_version": 1, "profiles": {"taylor_swift": {"mood": ["project-specific"]}},
            }), encoding="utf-8")
            local_direction, _, _ = load_search_visual_context(root, "taylor", segment="ending")
            self.assertEqual(local_direction["mood"], ["project-specific"])

    def test_discovery_queries_and_ranking_use_direction_without_network(self):
        class Provider:
            name = "fixture"
            queries = []

            def search(self, query, kind, limit):
                self.queries.append(query)
                return (
                    candidate("Taylor Swift chaotic aggressive editing", "chaos"),
                    candidate("Taylor Swift emotional intimate close up", "intimate"),
                )

        provider = Provider()
        report = search_visual(
            Path.cwd(), ["Taylor Swift All Too Well"], "video", include_external=True,
            providers=[provider], visual_direction=DIRECTION, visual_role="hook",
        )
        self.assertIn("Taylor Swift All Too Well", provider.queries)
        self.assertTrue(any("intimate" in query for query in provider.queries))
        self.assertEqual(report.results[0].provider_id, "intimate")
        self.assertEqual(report.as_dict()["visual_direction"]["profile"], "taylor_swift")
        self.assertEqual(report.results[0].as_dict()["artist_vibe"]["role"], "hook")

    def test_inspected_search_results_retain_vibe_ranking(self):
        stronger = candidate("emotional cinematic close up", "stronger")
        stronger = replace(stronger, artist_vibe_score=score_visual_candidate(stronger.as_dict(), DIRECTION))
        chaotic = candidate("chaotic aggressive editing", "chaotic")
        chaotic = replace(chaotic, artist_vibe_score=score_visual_candidate(chaotic.as_dict(), DIRECTION))
        ranked = rank_inspections_for_selection((inspection(chaotic, 90), inspection(stronger, 75)))
        self.assertEqual(ranked[0].result.provider_id, "stronger")


class ArtistVibeResolutionTests(unittest.TestCase):
    def test_final_selection_and_factual_tiers_after_both_takes_are_inspected_in_base_and_web(self):
        # inspect_top=2 proves the final comparison, independently of pre-ranking.
        # The same fixture without a direction must retain the technical winner.
        for web_mode in (False, True):
            for vibe_enabled, unequal_tiers in product((False, True), repeat=2):
                with self.subTest(web=web_mode, vibe=vibe_enabled, unequal_tiers=unequal_tiers), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    episode = root / "episodes" / "demo"
                    episode.mkdir(parents=True)
                    story = {"segments": [{"id": "hook", "text": "an intimate memory", "visual_role": "hook"}]}
                    if vibe_enabled:
                        # A custom project catalog must be used by the resolver,
                        # just as it is used by loading/rendering and search.
                        story["artist_vibe"] = "project_taylor"
                        (root / "config").mkdir()
                        (root / "config" / "artist_vibes.json").write_text(json.dumps({
                            "schema_version": 1, "profiles": {"project_taylor": {
                                key: value for key, value in DIRECTION.items() if key != "profile"
                            }},
                        }), encoding="utf-8")
                    payloads = {
                        "story.json": story,
                        "assets.json": {"schema_version": 1, "assets": [
                            {"id": "hook", "file": "old.mp4", "url": "https://example.com/old.mp4"},
                        ]},
                        "timeline.json": {"schema_version": 1, "shots": [
                            {"id": "shot", "segment": "hook", "asset": "hook", "motion": "hold", "transition_out": "cut"},
                        ]},
                        "visual_candidates.json": {"schema_version": 1, "slots": [{
                            "id": "hook", "inspect_top": 2, "candidates": [
                                {"name": "chaotic aggressive editing", "kind": "video", "file": "chaos.mp4", "url": "https://example.com/chaos.mp4", "semantic_fit": "exact" if unequal_tiers else "direct"},
                                {"name": "emotional intimate cinematic close up", "kind": "video", "file": "tender.mp4", "url": "https://example.com/tender.mp4", "semantic_fit": "generic" if unequal_tiers else "direct", "visual_metadata": {
                                    "emotional_strength": 90, "cinematic_value": 90,
                                    "storytelling_value": 90, "narration_fit": 90,
                                }},
                            ],
                        }]},
                    }
                    for name, payload in payloads.items():
                        (episode / name).write_text(json.dumps(payload), encoding="utf-8")
                    attempts = []
                    technical_scores = {"chaos.mp4": 55, "tender.mp4": 100} if unequal_tiers else {"chaos.mp4": 90, "tender.mp4": 70}

                    def inspect(_root, _slot, raw, index):
                        attempts.append(raw["file"])
                        result = replace(candidate(raw["name"]), file_format="mp4", download_url=raw["url"])
                        technical = technical_scores[raw["file"]]
                        assessed = inspection(result, technical)
                        if web_mode:
                            assessed = web_resolver.RankedInspection(
                                base=assessed, technical_visual_score=technical, visual_score=technical,
                                rights_status="unknown", rights_rank_adjustment=0,
                            )
                        return result, assessed, []

                    score_record = web_resolver._score_record if web_mode else resolver._score_record
                    with (
                        patch.object(resolver, "_inspect_candidate", side_effect=inspect),
                        patch.object(resolver, "_score_record", side_effect=score_record),
                        patch.object(resolver, "load_visual_history", return_value=((), ())),
                        redirect_stdout(io.StringIO()),
                    ):
                        resolver.resolve_episode(root, "demo", prefer_video_candidates=web_mode)
                    self.assertEqual(set(attempts), {"chaos.mp4", "tender.mp4"})
                    self.assertEqual(len(attempts), 2)
                    assets = json.loads((episode / "assets.json").read_text(encoding="utf-8"))
                    if unequal_tiers:
                        expected_file = "chaos.mp4" if (vibe_enabled or web_mode) else "tender.mp4"
                    else:
                        expected_file = "tender.mp4" if vibe_enabled else "chaos.mp4"
                    self.assertEqual(assets["assets"][0]["file"], expected_file)
                    report = json.loads((episode / "visual_resolution_report.json").read_text(encoding="utf-8"))
                    self.assertEqual(len(report["candidate_scores"]["hook"]), 2)
                    chosen = report["selections"]["hook"]
                    self.assertEqual(chosen["visual_score"], technical_scores[expected_file])
                    self.assertEqual("artist_vibe" in chosen, vibe_enabled)

    def test_slot_uses_story_narration_before_shortlist_and_persists_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            episode = root / "episodes" / "demo"
            episode.mkdir(parents=True)
            payloads = {
                "story.json": {"artist_vibe": "taylor_swift", "visual_direction": {
                    key: value for key, value in DIRECTION.items() if key != "profile"
                }, "segments": [
                    {"id": "opening", "text": "intimate piano", "visual_role": "hook"},
                    {"id": "ending", "text": "epic crowd singalong", "visual_role": "payoff"},
                ]},
                "assets.json": {"schema_version": 1, "assets": [
                    {"id": "opening", "file": "old.mp4", "url": "https://example.com/old.mp4"},
                ]},
                "timeline.json": {"schema_version": 1, "shots": [
                    {"id": "shot", "segment": "opening", "asset": "opening", "motion": "hold", "transition_out": "cut"},
                ]},
                "visual_candidates.json": {"schema_version": 1, "slots": [{
                    "id": "opening", "inspect_top": 1, "candidates": [
                        {"name": "chaotic aggressive editing", "kind": "video", "file": "chaos.mp4", "url": "https://example.com/chaos.mp4", "width": 2160, "height": 3840},
                        {"name": "emotional intimate close up piano", "kind": "video", "file": "tender.mp4", "url": "https://example.com/tender.mp4", "width": 720, "height": 1280},
                    ],
                }]},
            }
            for name, payload in payloads.items():
                (episode / name).write_text(json.dumps(payload), encoding="utf-8")

            attempted = []

            def inspect(_root, slot, raw, index):
                attempted.append(raw["name"])
                result = replace(candidate(raw["name"]), file_format="mp4", download_url=raw["url"])
                return result, inspection(result, 75), []

            with (
                patch.object(resolver, "_inspect_candidate", side_effect=inspect),
                patch.object(resolver, "load_visual_history", return_value=((), ())),
            ):
                resolver.resolve_episode(root, "demo")
            self.assertEqual(attempted, ["emotional intimate close up piano"])
            report = json.loads((episode / "visual_resolution_report.json").read_text(encoding="utf-8"))
            chosen = report["selections"]["opening"]
            self.assertEqual(chosen["artist_vibe"]["role"], "hook")
            self.assertIn("piano", chosen["artist_vibe"]["narration_matches"])
            self.assertGreater(chosen["selection_score"], chosen["visual_score"])
            assets = json.loads((episode / "assets.json").read_text(encoding="utf-8"))
            self.assertEqual(assets["assets"][0]["file"], "tender.mp4")
            self.assertNotIn("artist_vibe", assets["assets"][0])


if __name__ == "__main__":
    unittest.main()
