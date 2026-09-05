import json
import tempfile
import unittest
from pathlib import Path

from engine.duplicates import DUPLICATE_CODE, find_duplicate_candidate, format_duplicate, infer_identity_from_story, normalize_text


def write_story(root: Path, slug: str, title: str, hook: str) -> Path:
    episode = root / "episodes" / slug
    episode.mkdir(parents=True, exist_ok=True)
    path = episode / "story.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "title": title,
                "slug": slug,
                "target_duration_seconds": 75,
                "segments": [{"id": "hook", "text": hook}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class DuplicateEpisodeTests(unittest.TestCase):
    def test_normalize_text_ignores_accents_case_and_punctuation(self):
        self.assertEqual(normalize_text("Águas de Março!"), "aguas de marco")

    def test_finds_exact_slug_duplicate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_story(root, "my_eyes_travis_scott", "Travis Scott — MY EYES", "MY EYES, de Travis Scott, muda de clima.")

            match = find_duplicate_candidate(
                root,
                song="MY EYES",
                artist="Travis Scott",
                slug="my_eyes_travis_scott",
            )

        self.assertIsNotNone(match)
        self.assertEqual(match.reason, "exact_slug")

    def test_finds_same_song_artist_even_with_different_slug(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_story(
                root,
                "historia_da_dolly",
                "Dolly Parton — I Will Always Love You: o adeus",
                "I Will Always Love You, da Dolly Parton, nasceu de uma despedida profissional.",
            )

            match = find_duplicate_candidate(
                root,
                song="I Will Always Love You",
                artist="Dolly Parton",
                slug="i_will_always_love_you_dolly_parton",
            )

        self.assertIsNotNone(match)
        self.assertEqual(match.slug, "historia_da_dolly")
        self.assertIn(match.reason, {"same_song_and_artist", "song_and_artist_present_in_story"})
        self.assertTrue(format_duplicate(match).startswith(DUPLICATE_CODE))

    def test_does_not_block_different_artist_with_same_generic_words(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_story(root, "hello_adele", "Adele — Hello", "Hello, da Adele, virou um retorno gigantesco.")

            match = find_duplicate_candidate(
                root,
                song="Hello",
                artist="Lionel Richie",
                slug="hello_lionel_richie",
            )

        self.assertIsNone(match)

    def test_detects_existing_queue_and_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue = root / ".publish-queue"
            retry = root / ".publish-retry"
            queue.mkdir()
            retry.mkdir()
            (queue / "song_artist.txt").write_text("song_artist", encoding="utf-8")
            (retry / "other_artist-retry-1.txt").write_text("other_artist", encoding="utf-8")

            queue_match = find_duplicate_candidate(
                root,
                song="Song",
                artist="Artist",
                slug="song_artist",
            )
            retry_match = find_duplicate_candidate(
                root,
                song="Other",
                artist="Artist",
                slug="other_artist",
            )

        self.assertEqual(queue_match.kind, "queue")
        self.assertEqual(retry_match.kind, "retry")

    def test_episode_mode_can_exclude_itself_and_find_older_duplicate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = write_story(
                root,
                "older_slug",
                "Dolly Parton — I Will Always Love You: original",
                "I Will Always Love You, da Dolly Parton, foi escrita como despedida.",
            )
            current = write_story(
                root,
                "current_slug",
                "Dolly Parton — I Will Always Love You: nova historia",
                "I Will Always Love You, da Dolly Parton, tem uma historia enorme.",
            )
            song, artist = infer_identity_from_story(current)
            self.assertEqual((song, artist), ("I Will Always Love You", "Dolly Parton"))

            match = find_duplicate_candidate(
                root,
                song=song,
                artist=artist,
                slug="current_slug",
                exclude_slug="current_slug",
            )

        self.assertIsNotNone(match)
        self.assertEqual(match.path, "episodes/older_slug")
        self.assertTrue(older.is_file())


if __name__ == "__main__":
    unittest.main()
