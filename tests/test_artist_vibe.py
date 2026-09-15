from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.artist_vibe import build_visual_queries, resolve_visual_direction, visual_role_for_segment
from engine.episode import create_episode, load_episode, load_story
from engine.models import ScriptSegment, Story


ROOT = Path(__file__).resolve().parents[1]


class ArtistVibeTests(unittest.TestCase):
    def test_legacy_and_explicit_opt_out_do_not_read_catalog(self):
        with patch("engine.artist_vibe._catalog", side_effect=AssertionError("legacy read catalog")):
            for raw in ({"title": "Taylor Swift HERO"}, {"artist": "Taylor Swift"},
                        {"artist_vibe": False, "visual_direction": {"mood": ["intimate"]}}):
                self.assertIsNone(resolve_visual_direction(raw))

    def test_auto_infers_only_unambiguous_known_artist(self):
        for raw in ({"artist": "Taylor Swift"}, {"title": "All Too Well — TAYLOR SWIFT"}):
            self.assertEqual(resolve_visual_direction({**raw, "artist_vibe": "auto"})["profile"], "taylor_swift")
        for identity in ("Taylor Swift e Elton John", "Taylor Swiftly", "Artista novo"):
            self.assertIsNone(resolve_visual_direction({"artist": identity, "artist_vibe": "auto"}))
        self.assertIsNone(resolve_visual_direction({"artist": "Artista novo", "title": "Tributo Taylor Swift", "artist_vibe": "auto"}))

    def test_overrides_are_deep_and_do_not_mutate_profiles_or_input(self):
        raw = {"artist_vibe": "taylor_swift", "visual_direction": {
            "pacing": {"payoff_seconds": 7.0}, "mood": ["nostalgic"],
            "emotional_arc": {"hook": {"search_terms": ["acoustic opening"]}},
        }}
        original = deepcopy(raw)
        result = resolve_visual_direction(raw)
        self.assertEqual(result["pacing"]["payoff_seconds"], 7.0)
        self.assertEqual(result["pacing"]["hook_seconds"], 3.2)
        self.assertEqual(result["mood"], ["nostalgic"])
        self.assertIn("close-up", result["emotional_arc"]["hook"]["preferred_shot_types"])
        result["mood"].append("changed")
        self.assertEqual(raw, original)
        self.assertNotIn("changed", resolve_visual_direction({"artist_vibe": "taylor_swift"})["mood"])

    def test_inline_direction_and_unknown_auto_artist_overrides_work(self):
        for raw in ({"visual_direction": {"mood": ["intimate"]}},
                    {"artist_vibe": "auto", "artist": "New artist", "visual_direction": {"mood": ["intimate"]}}):
            result = resolve_visual_direction(raw)
            self.assertEqual(result["profile"], "custom")
            self.assertEqual(result["mood"], ["intimate"])
            self.assertIn("what_to_avoid", result)

    def test_invalid_metadata_has_actionable_errors(self):
        cases = [
            ({"artist_vibe": "typo"}, "desconhecida"),
            ({"artist_vibe": True}, "artist_vibe"),
            ({"visual_direction": []}, "objeto"),
            ({"visual_direction": {"pacin": {}}}, "desconhecidos"),
            ({"visual_direction": {"mood": "emotional"}}, "lista"),
            ({"visual_direction": {"pacing": {"payoff_seconds": float("nan")}}}, "finito"),
            ({"visual_direction": {"pacing": {"payoff_seconds": True}}}, "finito"),
            ({"visual_direction": {"pacing": {"min_shot_seconds": 9, "max_shot_seconds": 2}}}, "excede"),
            ({"visual_direction": {"editing": {"transition": "glitch"}}}, "transition"),
            ({"visual_direction": {"cover": {"accent_color": "pink"}}}, "RRGGBB"),
            ({"visual_direction": {"emotional_arc": {"typo": {}}}}, "role"),
        ]
        for raw, message in cases:
            with self.subTest(raw=raw), self.assertRaisesRegex(RuntimeError, message):
                resolve_visual_direction(raw)

    def test_each_artist_has_independent_language(self):
        directions = [resolve_visual_direction({"artist_vibe": name}) for name in
                      ("taylor_swift", "elton_john", "travis_scott", "luan_santana")]
        self.assertEqual(len({item["cover"]["accent_color"] for item in directions}), 4)
        self.assertGreater(directions[0]["pacing"]["payoff_seconds"], directions[2]["pacing"]["payoff_seconds"])
        self.assertIn("chaotic", directions[0]["what_to_avoid"])
        self.assertIn("chaotic", directions[2]["mood"])
        self.assertNotIn("glam", directions[0]["mood"])

    def test_roles_respect_authorship_and_infer_only_boundaries(self):
        raw = {"segments": [{"id": "start"}, {"id": "memory", "visual_role": "intimacy"},
                            {"id": "facts"}, {"id": "end"}]}
        story = Story("test", "test", tuple(ScriptSegment(s["id"], "text", visual_role=s.get("visual_role")) for s in raw["segments"]))
        for input_story in (raw, story):
            self.assertEqual([visual_role_for_segment(input_story, s["id"]) for s in raw["segments"]],
                             ["hook", "intimacy", "context", "payoff"])
            self.assertEqual(visual_role_for_segment(input_story, "missing"), "context")

    def test_search_keeps_factual_queries_and_changes_expansion_by_arc(self):
        direction = resolve_visual_direction({"artist_vibe": "taylor_swift"})
        base = ("Taylor Swift All Too Well",)
        hook = build_visual_queries(base, direction, role="hook")
        payoff = build_visual_queries(base, direction, role="payoff")
        self.assertEqual(build_visual_queries(base, None), base)
        self.assertEqual(hook[0], base[0])
        self.assertNotEqual(hook, payoff)
        self.assertTrue(any("singalong" in value for value in payoff))
        self.assertTrue(all(base[0] in value for value in payoff))
        self.assertFalse(any("trap" in value for value in payoff))

    def test_story_loader_carries_profile_and_segment_role(self):
        raw = {"schema_version": 1, "title": "Taylor Swift", "slug": "demo",
               "target_duration_seconds": 30, "artist_vibe": "auto", "segments": [
                   {"id": "hook", "text": "Uma memoria.", "visual_role": "intimacy"}]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "story.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            story = load_story(path)
            self.assertEqual(story.visual_direction["profile"], "taylor_swift")
            self.assertEqual(story.segments[0].visual_role, "intimacy")
            raw["segments"][0]["visual_role"] = "typo"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "visual_role"):
                load_story(path)

    def test_project_catalog_is_shared_by_episode_loading_and_search(self):
        from engine.visual_vibe_scoring import load_search_visual_context

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode_dir = create_episode(root, "episodes", "demo")
            path = episode_dir / "story.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["artist_vibe"] = "local_artist"
            path.write_text(json.dumps(raw), encoding="utf-8")
            (root / "config").mkdir()
            (root / "config" / "artist_vibes.json").write_text(json.dumps({
                "schema_version": 1, "profiles": {"local_artist": {
                    "mood": ["intimate"], "cover": {"accent_color": "#123456"}
                }}
            }), encoding="utf-8")
            episode = load_episode(root, "episodes", "demo")
            direction, _, _ = load_search_visual_context(root, "demo")
        self.assertEqual(episode.story.visual_direction, direction)
        self.assertEqual(direction["cover"]["accent_color"], "#123456")


if __name__ == "__main__":
    unittest.main()
