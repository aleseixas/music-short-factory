import json
import tempfile
import unittest
from pathlib import Path

from engine.episode import create_episode, load_episode, load_story


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class EpisodeTests(unittest.TestCase):
    def test_load_episode_reads_content_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode_dir = root / "episodes" / "demo"
            (episode_dir / "assets").mkdir(parents=True)
            write_json(
                episode_dir / "story.json",
                {
                    "schema_version": 1,
                    "title": "Demo Song — Demo Artist",
                    "slug": "demo",
                    "target_duration_seconds": 75,
                    "segments": [{"id": "hook", "text": "Uma abertura curta."}],
                },
            )
            write_json(
                episode_dir / "assets.json",
                {
                    "schema_version": 1,
                    "assets": [
                        {
                            "id": "cover",
                            "file": "cover.jpg",
                            "url": "",
                            "credit": "Demo",
                            "license": "CC0",
                            "focus": {"x": 0.4, "y": 0.6},
                        }
                    ],
                },
            )
            write_json(
                episode_dir / "timeline.json",
                {
                    "schema_version": 1,
                    "shots": [
                        {
                            "id": "shot_hook",
                            "segment": "hook",
                            "asset": "cover",
                            "motion": "push_in",
                            "transition_out": "cut",
                        }
                    ],
                },
            )
            (episode_dir / "sources.txt").write_text("Demo source\n", encoding="utf-8")

            episode = load_episode(root, "episodes", "demo")

        self.assertEqual(episode.name, "demo")
        self.assertEqual(episode.story.slug, "demo")
        self.assertEqual(episode.story.target_duration_seconds, 75)
        self.assertEqual(episode.story.narration, "Uma abertura curta.")
        self.assertEqual(episode.assets["cover"].focus_x, 0.4)
        self.assertEqual(episode.shots[0].segment_id, "hook")
        self.assertIsNone(episode.background_music)
        self.assertEqual(episode.sfx_cues, ())
        self.assertEqual(episode.visual_fx_cues, ())
        self.assertEqual(episode.text_fx_cues, ())
        self.assertEqual(episode.overlay_cues, ())
        self.assertIsNone(episode.smart_visual_pacing)

    def test_load_story_reports_invalid_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "story.json"
            path.write_text('{"schema_version": 1, "segments": [', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, r"JSON invalido.*linha.*coluna"):
                load_story(path)

    def test_load_story_rejects_unknown_schema_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "story.json"
            write_json(
                path,
                {
                    "schema_version": 99,
                    "title": "Demo",
                    "slug": "demo",
                    "target_duration_seconds": 75,
                    "segments": [{"id": "hook", "text": "Texto."}],
                },
            )
            with self.assertRaisesRegex(RuntimeError, "schema_version invalido"):
                load_story(path)

    def test_create_episode_writes_loadable_templates_and_assets_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = create_episode(root, "episodes", "my_eyes")

            self.assertTrue((destination / "assets").is_dir())
            self.assertEqual(
                {path.name for path in destination.iterdir()},
                {"story.json", "timeline.json", "assets.json", "sources.txt", "assets"},
            )
            episode = load_episode(root, "episodes", "my_eyes")
            timeline = json.loads(
                (destination / "timeline.json").read_text(encoding="utf-8")
            )
            story = json.loads(
                (destination / "story.json").read_text(encoding="utf-8")
            )

        self.assertEqual(episode.story.slug, "my_eyes")
        self.assertEqual(episode.story.target_duration_seconds, 75)
        self.assertEqual(story["segments"][0]["delivery"], "hook")
        self.assertEqual(episode.shots[0].asset_id, "main_image")
        self.assertIsNone(timeline["background_music"])
        self.assertEqual(timeline["sfx_cues"], [])
        self.assertEqual(timeline["visual_fx_cues"], [])
        self.assertEqual(timeline["text_fx_cues"], [])
        self.assertEqual(timeline["overlay_cues"], [])
        self.assertEqual(timeline["smart_visual_pacing"], {"enabled": True})
        self.assertTrue(episode.smart_visual_pacing.enabled)

    def test_create_episode_refuses_to_overwrite_existing_episode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = create_episode(root, "episodes", "stan")
            sources = destination / "sources.txt"
            sources.write_text("do not overwrite\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "ja existe"):
                create_episode(root, "episodes", "stan")

            self.assertEqual(sources.read_text(encoding="utf-8"), "do not overwrite\n")


if __name__ == "__main__":
    unittest.main()
