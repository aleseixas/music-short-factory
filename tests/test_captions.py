import tempfile
import unittest
from pathlib import Path

from engine.captions import ass_time, write_ass_captions
from engine.config import CaptionStyle
from engine.models import WordTiming


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.style = CaptionStyle(
            font_name="Arial",
            font_size=52,
            text_color="#FFFFFF",
            active_color="#FFD43B",
            outline_color="#090909",
            outline_size=5,
            shadow_size=1,
            margin_left=54,
            margin_right=54,
            margin_bottom=185,
            max_words=4,
            max_chars=25,
            max_duration=1.7,
        )

    def test_ass_time_rolls_over_minutes(self):
        self.assertEqual(ass_time(59.995), "0:01:00.00")
        self.assertEqual(ass_time(3600.0), "1:00:00.00")

    def test_caption_file_has_active_word_and_valid_intervals(self):
        words = (
            WordTiming("Você", 0.0, 0.3),
            WordTiming("sabia?", 0.3, 0.8),
            WordTiming("Teste", 1.1, 1.4),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "captions.ass"
            write_ass_captions(words, path, 720, 1280, self.style)
            content = path.read_text(encoding="utf-8-sig")
        self.assertIn("PlayResX: 720", content)
        self.assertIn(r"\c&H3BD4FF&", content)
        self.assertIn("MarginV", content)
        self.assertNotIn("0:00:00.80,0:00:00.30", content)


if __name__ == "__main__":
    unittest.main()
