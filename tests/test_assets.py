import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from engine.assets import AssetManager
from engine.models import AssetSpec


def asset_spec(file_name: str = "photo.jpg", url: str | None = None) -> AssetSpec:
    return AssetSpec(
        id="photo",
        file=file_name,
        url=url,
        credit="",
        license="",
        focus_x=0.5,
        focus_y=0.5,
    )


class AssetTests(unittest.TestCase):
    def test_missing_asset_without_url_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = AssetManager(root / "assets", root / "work", 720, 1280, 1)

            with self.assertRaisesRegex(RuntimeError, r"Asset ausente e sem URL: photo"):
                manager.ensure(asset_spec())

    def test_existing_non_image_asset_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets_dir = root / "assets"
            assets_dir.mkdir()
            (assets_dir / "photo.jpg").write_text("not an image", encoding="utf-8")
            manager = AssetManager(assets_dir, root / "work", 720, 1280, 1)

            with self.assertRaisesRegex(RuntimeError, "nao e uma imagem valida"):
                manager.ensure(asset_spec())

    def test_valid_cached_asset_is_reused_without_download(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets_dir = root / "assets"
            assets_dir.mkdir()
            cached = assets_dir / "photo.jpg"
            Image.new("RGB", (8, 8), "red").save(cached)
            manager = AssetManager(assets_dir, root / "work", 720, 1280, 1)

            with patch.object(manager, "_download") as download:
                resolved = manager.ensure(asset_spec(url="https://example.invalid/photo.jpg"))

            self.assertEqual(resolved, cached.resolve())
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
