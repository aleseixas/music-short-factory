import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from engine.config import load_project_config
from engine.cover_intro import (
    COVER_INTRO_SECONDS, embed_episode_cover_intro, prepend_cover_intro,
    validate_episode_cover_opening,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CoverIntroTests(unittest.TestCase):
    def setUp(self):
        self.config = load_project_config(PROJECT_ROOT / "config" / "config.json")

    def test_prepend_cover_intro_adds_exact_frames_and_silent_prefix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "episode.mp4"
            cover = root / "episode_cover.jpg"
            video.write_bytes(b"original-video")
            cover.write_bytes(b"cover")
            ffmpeg_calls: list[list[object]] = []

            def fake_run(arguments, cwd=None):
                ffmpeg_calls.append(list(arguments))
                Path(arguments[-1]).write_bytes(b"video-with-cover")

            with (
                patch(
                    "engine.cover_intro.probe_video_frame_count",
                    side_effect=[90, 99],
                ),
                patch("engine.cover_intro.probe_duration", return_value=3.3),
                patch("engine.cover_intro.run_ffmpeg", side_effect=fake_run),
            ):
                output = prepend_cover_intro(
                    video,
                    cover,
                    self.config,
                    intro_frames=9,
                )

            self.assertEqual(output, video)
            self.assertEqual(video.read_bytes(), b"video-with-cover")
            self.assertEqual(len(ffmpeg_calls), 1)
            arguments = ffmpeg_calls[0]
            graph = arguments[arguments.index("-filter_complex") + 1]
            self.assertIn("trim=end_frame=9", graph)
            self.assertIn("trim=end_frame=90", graph)
            self.assertIn("[intro_v][main_v]concat=n=2:v=1:a=0", graph)
            self.assertIn("[silence][main_a]concat=n=2:v=0:a=1", graph)
            frame_index = arguments.index("-frames:v")
            self.assertEqual(arguments[frame_index + 1], 99)

    def test_embed_episode_cover_intro_uses_default_300ms_at_30fps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode_dir = root / "episodes" / "test_episode"
            output_dir = root / "output"
            episode_dir.mkdir(parents=True)
            output_dir.mkdir()
            video = output_dir / "test_episode.mp4"
            video.write_bytes(b"video")
            (episode_dir / "post.json").write_text(
                '{"cover":{"headline":"Titulo teste","source":{"type":"asset","asset_id":"hero"}}}',
                encoding="utf-8",
            )
            calls: list[int] = []

            def fake_generate(project_root, source_episode_dir, post, output_path):
                self.assertEqual(source_episode_dir, episode_dir)
                self.assertEqual(post["cover"]["headline"], "Titulo teste")
                output_path.write_bytes(b"jpeg")
                return output_path

            def fake_prepend(video_path, cover_path, config, *, intro_frames):
                calls.append(intro_frames)
                self.assertEqual(video_path, video)
                self.assertEqual(cover_path, output_dir / "test_episode_cover.jpg")
                return video_path

            with (
                patch("engine.cover_intro.generate_cover", side_effect=fake_generate),
                patch("engine.cover_intro.prepend_cover_intro", side_effect=fake_prepend),
            ):
                output, cover, duration = embed_episode_cover_intro(
                    root,
                    episode_dir,
                    video,
                    self.config,
                )

            expected_frames = round(COVER_INTRO_SECONDS * self.config.render.fps)
            self.assertEqual(calls, [expected_frames])
            self.assertEqual(output, video)
            self.assertEqual(cover, output_dir / "test_episode_cover.jpg")
            self.assertAlmostEqual(duration, expected_frames / self.config.render.fps)

    def test_opt_out_generates_cover_without_changing_first_video_frame(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode = root / "demo"
            episode.mkdir()
            video = root / "demo.mp4"
            video.write_bytes(b"original moving opening")
            (episode / "post.json").write_text(
                '{"cover":{"intro_enabled":false,"headline":"ELE QUASE DESISTIU",'
                '"source":{"type":"video_frame","timestamp_seconds":0.7}}}', encoding="utf-8"
            )
            with (
                patch("engine.cover_intro.generate_cover") as generate,
                patch("engine.cover_intro.prepend_cover_intro") as prepend,
            ):
                output, _, duration = embed_episode_cover_intro(root, episode, video, self.config)
            self.assertEqual(output.read_bytes(), b"original moving opening")
            self.assertEqual(duration, 0)
            generate.assert_called_once()
            prepend.assert_not_called()

    def test_moving_opening_rejects_static_asset_before_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "post.json").write_text(
                '{"cover":{"intro_enabled":false,"headline":"ELE QUASE DESISTIU",'
                '"source":{"type":"video_frame","selection":"auto_first_shot"}}}', encoding="utf-8"
            )
            shot = SimpleNamespace(asset_id="opening", freeze_frame=None)
            episode = SimpleNamespace(directory=root, shots=(shot,),
                                      assets={"opening": SimpleNamespace(is_video=False)})
            with self.assertRaisesRegex(RuntimeError, "video no primeiro take"):
                validate_episode_cover_opening(episode)
            episode.assets["opening"].is_video = True
            validate_episode_cover_opening(episode)
            shot.freeze_frame = object()
            with self.assertRaisesRegex(RuntimeError, "freeze_frame"):
                validate_episode_cover_opening(episode)


if __name__ == "__main__":
    unittest.main()
