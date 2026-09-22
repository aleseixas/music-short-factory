import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from engine.assets import AssetManager
from engine.ffmpeg import VideoStreamInfo
from engine.image_framing import (
    MIN_CROP_FRACTION_FOR_COVER,
    crop_fraction_for_target,
    prepare_vertical_image,
)
from engine.models import AssetSpec


def _asset(file_name: str, *, focus_x: float = 0.5) -> AssetSpec:
    return AssetSpec(
        id="visual",
        file=file_name,
        url=None,
        credit="",
        license="",
        focus_x=focus_x,
        focus_y=0.5,
    )


def _checker(
    image: Image.Image,
    box: tuple[int, int, int, int],
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    cell: int = 8,
) -> None:
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = box
    for y in range(top, bottom, cell):
        for x in range(left, right, cell):
            color = first if ((x - left) // cell + (y - top) // cell) % 2 else second
            draw.rectangle(
                (x, y, min(right - 1, x + cell - 1), min(bottom - 1, y + cell - 1)),
                fill=color,
            )


class IntelligentVerticalFramingTests(unittest.TestCase):
    def test_crop_fraction_threshold_keeps_near_vertical_and_contains_wide_media(self):
        target = (90, 160)
        self.assertAlmostEqual(crop_fraction_for_target((90, 160), target), 1.0)
        # 4:5 keeps enough source area to preserve the normal cover/crop path.
        self.assertGreater(crop_fraction_for_target((80, 100), target), MIN_CROP_FRACTION_FOR_COVER)
        # Square and wider media cross the neutral-contain threshold.
        self.assertLess(crop_fraction_for_target((100, 100), target), MIN_CROP_FRACTION_FOR_COVER)
        self.assertLess(crop_fraction_for_target((120, 90), target), MIN_CROP_FRACTION_FOR_COVER)
        self.assertLess(crop_fraction_for_target((160, 90), target), MIN_CROP_FRACTION_FOR_COVER)

    def test_tiny_valid_image_uses_safe_fallback_without_crashing_analysis(self):
        source = Image.new("RGB", (1, 1), (40, 120, 200))

        framed, decision = prepare_vertical_image(source, (90, 160), 0.5, 0.5)

        self.assertEqual(decision.mode, "contain_neutral")
        self.assertEqual(framed.size, (90, 160))

    def test_compact_subject_uses_smart_vertical_crop(self):
        source = Image.new("RGB", (320, 180), (72, 72, 72))
        subject = (126, 18, 194, 162)
        _checker(source, subject, (245, 205, 35), (18, 18, 18))

        framed, decision = prepare_vertical_image(source, (90, 160), 0.5, 0.5)

        self.assertEqual(decision.mode, "smart_crop")
        self.assertEqual(framed.size, (90, 160))
        self.assertIsNotNone(decision.crop_box)
        left, top, right, bottom = decision.crop_box or (0, 0, 0, 0)
        self.assertLessEqual(left, subject[0])
        self.assertGreaterEqual(right, subject[2])
        self.assertLessEqual(top, subject[1])
        self.assertGreaterEqual(bottom, subject[3])
        self.assertGreaterEqual(decision.retained_importance, 0.82)

    def test_wide_image_with_distributed_content_uses_full_image_and_neutral_fill(self):
        source = Image.new("RGB", (320, 180), (72, 72, 72))
        _checker(source, (0, 20, 72, 160), (245, 40, 35), (250, 250, 250))
        _checker(source, (248, 20, 320, 160), (30, 75, 245), (250, 250, 250))

        framed, decision = prepare_vertical_image(source, (90, 160), 0.5, 0.5)

        self.assertEqual(decision.mode, "contain_neutral")
        self.assertIsNone(decision.crop_box)
        self.assertEqual(framed.size, (90, 160))
        # The contained foreground spans x=7..82 and keeps both edge subjects.
        left_region = framed.crop((7, 58, 27, 102))
        right_region = framed.crop((63, 58, 83, 102))
        self.assertGreater(left_region.getchannel("R").getextrema()[1], 220)
        self.assertGreater(right_region.getchannel("B").getextrema()[1], 220)
        # The canvas uses the exact shared dark neutral background, not blur.
        self.assertEqual(framed.getpixel((45, 5)), (11, 15, 20))

    def test_explicit_focus_preserves_important_off_center_subject(self):
        source = Image.new("RGB", (320, 180), (72, 72, 72))
        subject = (246, 28, 300, 152)
        _checker(source, subject, (250, 205, 30), (18, 18, 18))

        framed, decision = prepare_vertical_image(source, (90, 160), 0.86, 0.5)

        self.assertEqual(decision.mode, "smart_crop")
        self.assertIsNotNone(decision.crop_box)
        left, _, right, _ = decision.crop_box or (0, 0, 0, 0)
        self.assertGreater(left, 180)
        self.assertLessEqual(left, subject[0])
        self.assertGreaterEqual(right, subject[2])
        self.assertGreater(framed.getchannel("R").getextrema()[1], 220)

    def test_wide_headline_is_not_partially_removed_by_crop(self):
        source = Image.new("RGB", (320, 180), (28, 28, 28))
        draw = ImageDraw.Draw(source)
        draw.rectangle((8, 62, 312, 118), outline=(248, 248, 248), width=4)
        for x in range(18, 303, 24):
            draw.rectangle((x, 72, x + 12, 108), fill=(248, 248, 248))
            draw.rectangle((x + 6, 82, x + 20, 91), fill=(248, 248, 248))

        framed, decision = prepare_vertical_image(source, (90, 160), 0.5, 0.5)

        self.assertEqual(decision.mode, "contain_neutral")
        # Bright headline details from both extremes remain in the foreground.
        band = framed.crop((7, 65, 83, 95))
        self.assertGreater(band.crop((0, 0, 18, 30)).getextrema()[0][1], 220)
        self.assertGreater(band.crop((58, 0, 76, 30)).getextrema()[0][1], 220)

    def test_asset_manager_prepares_images_but_bypasses_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets_dir = root / "assets"
            assets_dir.mkdir()
            image_path = assets_dir / "photo.jpg"
            source = Image.new("RGB", (320, 180), (72, 72, 72))
            _checker(source, (126, 18, 194, 162), (245, 205, 35), (18, 18, 18))
            source.save(image_path)
            video_path = assets_dir / "clip.mp4"
            video_path.write_bytes(b"synthetic")
            manager = AssetManager(assets_dir, root / "work", 90, 160, 1)

            prepared = manager.prepare(_asset("photo.jpg"))
            with Image.open(prepared) as opened:
                self.assertEqual(opened.size, (90, 160))
            self.assertIn("smart-v1", prepared.name)

            info = VideoStreamInfo(duration=2.0, width=320, height=180, fps=30.0)
            with (
                patch("engine.assets.probe_video_stream", return_value=info),
                patch("engine.assets.prepare_vertical_image") as framing,
            ):
                resolved_video = manager.prepare(_asset("clip.mp4"))

            self.assertEqual(resolved_video, video_path.resolve())
            framing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
