import tempfile
import unittest
from pathlib import Path

from engine.audio import (
    _load_generated_timings,
    _write_generated_timings,
    estimate_word_timings,
    load_timings,
)
from engine.models import WordTiming


class TTSTests(unittest.TestCase):
    def test_srt_fraction_keeps_leading_zeroes(self):
        content = """1
00:00:00,030 --> 00:00:01,230
Olá mundo
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "voice.srt"
            path.write_text(content, encoding="utf-8")
            words = load_timings(path)
        self.assertAlmostEqual(words[0].start, 0.03)
        self.assertAlmostEqual(words[-1].end, 1.23)

    def test_estimated_timings_cover_the_audio_exactly(self):
        words = estimate_word_timings("Um teste curto.", 2.75)
        self.assertEqual(words[0].start, 0.0)
        self.assertEqual(words[-1].end, 2.75)
        self.assertTrue(all(word.end > word.start for word in words))

    def test_sidecar_text_must_match_narration(self):
        content = """1
00:00:00,000 --> 00:00:01,000
texto completamente diferente
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "voice.srt"
            path.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "nao corresponde ao roteiro"):
                load_timings(path, expected_text="esta e a narracao correta", audio_duration=1.0)

    def test_sidecar_rejects_overlapping_words(self):
        content = """{
  "words": [
    {"text": "um", "start": 0.0, "end": 0.8},
    {"text": "teste", "start": 0.7, "end": 1.0}
  ]
}
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "voice.json"
            path.write_text(content, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "sobrepostos"):
                load_timings(path, expected_text="um teste", audio_duration=1.0)

    def test_generated_cache_depends_on_voice_rate_and_audio(self):
        words = (WordTiming("teste", 0.0, 0.5),)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "voice.mp3"
            timings = root / "voice.words.json"
            audio.write_bytes(b"audio-v1")
            _write_generated_timings(
                timings,
                "story-hash",
                words,
                exact=True,
                edge_voice="voice-a",
                edge_rate="+0%",
                audio_path=audio,
            )
            cached, exact = _load_generated_timings(
                timings,
                "story-hash",
                "voice-a",
                "+0%",
                audio,
                "teste",
                0.5,
            )
            wrong_voice, _ = _load_generated_timings(
                timings,
                "story-hash",
                "voice-b",
                "+0%",
                audio,
                "teste",
                0.5,
            )
            audio.write_bytes(b"audio-v2")
            changed_audio, _ = _load_generated_timings(
                timings,
                "story-hash",
                "voice-a",
                "+0%",
                audio,
                "teste",
                0.5,
            )
        self.assertEqual(cached, words)
        self.assertTrue(exact)
        self.assertEqual(wrong_voice, ())
        self.assertEqual(changed_audio, ())

    def test_generated_cache_rejects_estimated_timings(self):
        words = (WordTiming("teste", 0.0, 0.5),)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "voice.mp3"
            timings = root / "voice.words.json"
            audio.write_bytes(b"audio")
            _write_generated_timings(
                timings,
                "story-hash",
                words,
                exact=False,
                edge_voice="voice-a",
                edge_rate="+0%",
                audio_path=audio,
            )
            cached, exact = _load_generated_timings(
                timings,
                "story-hash",
                "voice-a",
                "+0%",
                audio,
                "teste",
                0.5,
            )
        self.assertEqual(cached, ())
        self.assertFalse(exact)


if __name__ == "__main__":
    unittest.main()
