from __future__ import annotations

from pathlib import Path
import unittest

from check_episode_media import _assert_intra_episode_visuals_unique
from engine.models import AssetSpec, Episode, ShotSpec, Story


class IntraEpisodeVisualUniquenessTests(unittest.TestCase):
    @staticmethod
    def _asset(asset_id: str, file_name: str, url: str | None) -> AssetSpec:
        return AssetSpec(
            id=asset_id,
            file=file_name,
            url=url,
            credit="",
            license="",
            focus_x=0.5,
            focus_y=0.5,
        )

    @staticmethod
    def _shot(shot_id: str, asset_id: str) -> ShotSpec:
        return ShotSpec(
            id=shot_id,
            segment_id="segment",
            asset_id=asset_id,
            motion="hold",
            transition_out="cut",
        )

    @classmethod
    def _episode(
        cls,
        assets: list[AssetSpec],
        shot_asset_ids: list[str],
    ) -> Episode:
        return Episode(
            name="episode",
            directory=Path("episodes/episode"),
            story=Story(
                title="Episode",
                slug="episode",
                segments=(),
            ),
            assets={asset.id: asset for asset in assets},
            shots=tuple(
                cls._shot(f"shot_{index}", asset_id)
                for index, asset_id in enumerate(shot_asset_ids, start=1)
            ),
        )

    def test_repeated_asset_id_is_blocked(self):
        asset = self._asset(
            "visual_01",
            "visual_01.jpg",
            "https://example.com/visual_01.jpg",
        )
        episode = self._episode([asset], ["visual_01", "visual_01"])

        with self.assertRaisesRegex(RuntimeError, "INTRA_EPISODE_VISUAL_REUSE_BLOCKED"):
            _assert_intra_episode_visuals_unique(episode)

    def test_different_asset_ids_with_same_file_are_blocked(self):
        first = self._asset("visual_01", "same.jpg", "https://example.com/a.jpg")
        second = self._asset("visual_02", "same.jpg", "https://example.com/b.jpg")
        episode = self._episode([first, second], ["visual_01", "visual_02"])

        with self.assertRaisesRegex(RuntimeError, "INTRA_EPISODE_VISUAL_REUSE_BLOCKED"):
            _assert_intra_episode_visuals_unique(episode)

    def test_same_normalized_url_is_blocked(self):
        first = self._asset(
            "visual_01",
            "first.jpg",
            "https://example.com/media/photo.jpg?token=old#fragment",
        )
        second = self._asset(
            "visual_02",
            "second.jpg",
            "https://EXAMPLE.com/media/photo.jpg?token=new",
        )
        episode = self._episode([first, second], ["visual_01", "visual_02"])

        with self.assertRaisesRegex(RuntimeError, "INTRA_EPISODE_VISUAL_REUSE_BLOCKED"):
            _assert_intra_episode_visuals_unique(episode)

    def test_distinct_visuals_pass(self):
        first = self._asset(
            "visual_01",
            "first.jpg",
            "https://example.com/media/first.jpg",
        )
        second = self._asset(
            "visual_02",
            "second.webm",
            "https://example.com/media/second.webm",
        )
        episode = self._episode([first, second], ["visual_01", "visual_02"])

        _assert_intra_episode_visuals_unique(episode)


if __name__ == "__main__":
    unittest.main()
