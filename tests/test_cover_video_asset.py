from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from publishing.cover import generate_cover
from publishing.metadata import _validate_cover, sync_selected_cover_timestamp


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class CoverVideoAssetTests(unittest.TestCase):
    def test_automatic_cover_stays_in_first_resolved_take_and_syncs_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode = root / "episodes" / "demo"
            (episode / "assets").mkdir(parents=True)
            video = episode / "assets" / "opening.mp4"
            video.write_bytes(b"video fixture")
            _write_json(root / "config/config.json", {"render": {"width": 360, "height": 640}})
            _write_json(root / "config/style.json", {"highlights": {}})
            _write_json(episode / "assets.json", {"assets": [{"id": "opening", "file": video.name}]})
            _write_json(episode / "timeline.json", {"shots": [{"id": "hook", "asset": "opening"}]})
            _write_json(root / "work/demo/timeline.resolved.json", {
                "fps": 30, "scenes": [{"id": "hook", "asset": "opening", "start_frame": 0,
                "end_frame": 39, "end": 1.3, "source_start_seconds": 10, "speed": 1.5}]
            })
            post = {"cover": {"intro_enabled": False, "headline": "ELE QUASE DESISTIU",
                    "source": {"type": "video_frame", "selection": "auto_first_shot"}},
                    "instagram": {"thumb_offset_ms": 9999}, "tiktok": {"video_cover_timestamp_ms": 9999}}
            _write_json(episode / "post.json", post)
            destination = root / "output/demo_cover.jpg"
            times = []

            def extract(source: Path, timestamp: float, frame: Path) -> None:
                self.assertEqual(source, video.resolve())
                times.append(timestamp)
                image = Image.new("RGB", (360, 640), "black")
                if 10.8 <= timestamp <= 11.3:
                    image.paste((122, 122, 122), (0, 0, 360, 640))
                    draw = ImageDraw.Draw(image)
                    for x in range(0, 360, 16):
                        draw.rectangle((x, 0, x + 6, 639), fill=(175, 175, 175))
                image.save(frame)

            with patch("publishing.cover._extract_frame", side_effect=extract):
                generate_cover(root, episode, post, destination)
            selection = json.loads(destination.with_suffix(".selection.json").read_text())
            self.assertEqual(len(times), 7)
            self.assertTrue(all(10 <= t < 10 + 1.3 * 1.5 for t in times))
            self.assertLess(selection["timestamp_seconds"], 1.3)
            self.assertTrue(10.8 <= selection["source_timestamp_seconds"] <= 11.3)
            self.assertFalse((destination.parent / ".demo_cover_opening_frame.png").exists())
            self.assertTrue(sync_selected_cover_timestamp(episode, destination))
            saved = json.loads((episode / "post.json").read_text())
            expected = round(selection["timestamp_seconds"] * 1000)
            self.assertEqual(saved["instagram"]["thumb_offset_ms"], expected)
            self.assertEqual(saved["tiktok"]["video_cover_timestamp_ms"], expected)
            # Repeat preparation must choose the same source despite the persisted offset.
            with patch("publishing.cover._extract_frame", side_effect=extract):
                generate_cover(root, episode, saved, destination)
            self.assertEqual(json.loads(destination.with_suffix(".selection.json").read_text()), selection)

    def test_automatic_cover_requires_explicit_moving_opening(self) -> None:
        cover = {"headline": "ELE QUASE DESISTIU", "source": {
            "type": "video_frame", "selection": "auto_first_shot"}}
        with self.assertRaisesRegex(RuntimeError, "intro_enabled=false"):
            _validate_cover(cover, "fixture")
        cover["intro_enabled"] = False
        _validate_cover(cover, "fixture")
        cover["intro_enabled"] = "false"
        with self.assertRaisesRegex(RuntimeError, "booleano"):
            _validate_cover(cover, "fixture")

    def test_asset_cover_extracts_frame_when_resolved_asset_is_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode_dir = root / "episodes" / "demo"
            assets_dir = episode_dir / "assets"
            assets_dir.mkdir(parents=True)

            _write_json(
                root / "config" / "config.json",
                {"render": {"width": 720, "height": 1280}},
            )
            _write_json(root / "config" / "style.json", {"highlights": {}})
            _write_json(
                episode_dir / "assets.json",
                {
                    "schema_version": 1,
                    "assets": [
                        {
                            "id": "hook_visual",
                            "file": "youtube-demo.mp4",
                            "focus": {"x": 0.5, "y": 0.5},
                        }
                    ],
                },
            )
            video_path = assets_dir / "youtube-demo.mp4"
            video_path.write_bytes(b"video fixture; extraction is mocked")

            post = {
                "cover": {
                    "headline": "DA QUEDA A OBRA-PRIMA",
                    "source": {"type": "asset", "asset_id": "hook_visual"},
                }
            }
            destination = root / "output" / "demo_cover.jpg"
            temporary_frame = destination.parent / ".demo_cover_asset_frame.png"

            def fake_extract_frame(source: Path, timestamp: float, frame: Path) -> None:
                self.assertEqual(source, video_path.resolve())
                self.assertEqual(timestamp, 1.0)
                Image.new("RGB", (1280, 720), "black").save(frame, format="PNG")

            with patch("publishing.cover._extract_frame", side_effect=fake_extract_frame) as extract:
                result = generate_cover(root, episode_dir, post, destination)

            self.assertEqual(result, destination)
            self.assertEqual(extract.call_count, 1)
            self.assertTrue(destination.is_file())
            self.assertFalse(temporary_frame.exists())
            with Image.open(destination) as cover:
                self.assertEqual(cover.size, (720, 1280))
                self.assertEqual(cover.format, "JPEG")


if __name__ == "__main__":
    unittest.main()
