from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from publishing.cover import generate_cover


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class CoverVideoAssetTests(unittest.TestCase):
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
