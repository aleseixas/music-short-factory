from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops

from engine.artist_style import apply_artist_style
from engine.config import load_style_config
from engine.ffmpeg import VideoStreamInfo
from engine.models import AssetSpec, ScriptSegment, Story, WordTiming
from engine.smart_visual_pacing import apply_smart_visual_pacing
from engine.timeline import build_timeline, load_timeline
from publishing.cover import generate_cover


ROOT = Path(__file__).resolve().parents[1]
DIRECTION = {
    "profile": "taylor_swift",
    "pacing": {
        "hook_seconds": 3.4,
        "body_seconds": 3.7,
        "emotional_seconds": 5.0,
        "payoff_seconds": 6.2,
        "min_shot_seconds": 2.2,
        "max_shot_seconds": 6.5,
    },
    "editing": {
        "motion": "push_in",
        "transition": "crossfade",
        "text_animation": "fade_pop",
        "text_intensity": 0.18,
        "crossfade_seconds": 0.28,
    },
    "cover": {
        "text_color": "#FFF6EA",
        "accent_color": "#BCA2D9",
        "background_color": "#241D32",
        "font_name": "Georgia",
    },
}


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ArtistEditingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.story = Story(
            title="Uma memoria compartilhada",
            slug="taylor-memory",
            segments=(
                ScriptSegment("hook", "Carta", "hook"),
                ScriptSegment("context", "Memoria", "emotional"),
                ScriptSegment("payoff", "Multidao", "payoff"),
            ),
            visual_direction=DIRECTION,
        )
        self.assets = {
            name: AssetSpec(name, f"{name}.mp4", None, "", "", 0.5, 0.5)
            for name in ("hook", "context", "payoff")
        }
        self.raw = {
            "schema_version": 1,
            "shots": [
                {"id": name, "segment": name, "asset": name}
                for name in self.assets
            ],
            "text_fx_cues": [{
                "segment": "hook",
                "offset_seconds": 0.25,
                "duration_seconds": 1.4,
                "text": "Uma carta para milhares",
            }],
        }

    def _load(self, raw: dict, story: Story | None = None):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "timeline.json"
            _write(path, raw)
            return load_timeline(path, story or self.story, self.assets)

    def test_artist_defaults_fill_omissions_but_preserve_authored_choices(self):
        self.raw["shots"][1].update(motion="hold", transition_out="cut")
        timeline = self._load(self.raw)
        self.assertEqual(timeline.shots[0].motion, "push_in")
        self.assertEqual(timeline.shots[0].transition_out, "crossfade")
        self.assertEqual(timeline.shots[1].motion, "hold")
        self.assertEqual(timeline.shots[1].transition_out, "cut")
        self.assertEqual(timeline.shots[-1].transition_out, "cut")
        self.assertEqual(timeline.text_fx_cues[0].animation, "fade_pop")
        self.assertEqual(timeline.text_fx_cues[0].intensity, 0.18)
        self.assertTrue(timeline.smart_visual_pacing.enabled)
        self.assertTrue(timeline.preserve_authored_video_trims)

        self.raw["text_fx_cues"][0].update(animation="slide_up", intensity=0.3)
        self.raw["smart_visual_pacing"] = {"enabled": False}
        self.raw["preserve_authored_video_trims"] = False
        explicit = self._load(self.raw)
        self.assertEqual(explicit.text_fx_cues[0].animation, "slide_up")
        self.assertEqual(explicit.text_fx_cues[0].intensity, 0.3)
        self.assertFalse(explicit.smart_visual_pacing.enabled)
        self.assertFalse(explicit.preserve_authored_video_trims)
        self.raw["smart_visual_pacing"] = None
        self.assertIsNone(self._load(self.raw).smart_visual_pacing)

    def test_episode_without_direction_keeps_legacy_defaults_and_validation(self):
        legacy = replace(self.story, visual_direction=None)
        with self.assertRaisesRegex(RuntimeError, "animation"):
            self._load(self.raw, legacy)
        self.raw["text_fx_cues"] = []
        timeline = self._load(self.raw, legacy)
        self.assertTrue(all(shot.motion == "hold" for shot in timeline.shots))
        self.assertTrue(all(shot.transition_out == "cut" for shot in timeline.shots))
        self.assertIsNone(timeline.smart_visual_pacing)
        self.assertFalse(timeline.preserve_authored_video_trims)

    def test_sentimental_payoff_gets_more_time_with_frame_and_trim_safety(self):
        self.raw["text_fx_cues"] = []
        timeline = self._load(self.raw)
        words = tuple(
            WordTiming(segment.text, index * 4, (index + 1) * 4)
            for index, segment in enumerate(self.story.segments)
        )
        plan = build_timeline(self.story, timeline.shots, self.assets, words, 12, 10, 0.28)
        infos = {
            asset: VideoStreamInfo(duration=120, width=1920, height=1080, fps=30)
            for asset in self.assets
        }
        directed, report = apply_smart_visual_pacing(
            plan, self.story, enabled=True, crossfade_seconds=0.28, video_infos=infos,
        )
        with patch(
            "engine.smart_visual_pacing.visual_role_for_segment",
            side_effect=AssertionError("legacy pacing resolved an artist role"),
        ):
            generic, _ = apply_smart_visual_pacing(
                plan, replace(self.story, visual_direction=None), enabled=True,
                crossfade_seconds=0.28, video_infos=infos,
            )
        self.assertTrue(report.applied)
        self.assertGreater(directed.scenes[-1].frame_count, generic.scenes[-1].frame_count)
        self.assertEqual(directed.total_frames, plan.total_frames)
        self.assertEqual([scene.asset.id for scene in directed.scenes], list(self.assets))
        self.assertTrue(all(
            abs(after.end_frame - before.end_frame) <= 10
            for before, after in zip(plan.scenes, directed.scenes)
        ))
        self.assertIn("artist_vibe_role:payoff", report.scenes[-1].signals)
        short_infos = {asset: replace(info, duration=1) for asset, info in infos.items()}
        preserved, unsafe = apply_smart_visual_pacing(
            plan, self.story, enabled=True, crossfade_seconds=0.28, video_infos=short_infos,
        )
        self.assertEqual(preserved, plan)
        self.assertFalse(unsafe.applied)

    def test_palette_reaches_episode_text_and_generated_cover(self):
        style = load_style_config(ROOT / "config/style.json")
        self.assertIs(apply_artist_style(style, None), style)
        directed = apply_artist_style(style, DIRECTION)
        self.assertEqual(directed.captions.active_color, DIRECTION["cover"]["accent_color"])
        self.assertEqual(directed.highlights.accent_color, directed.captions.active_color)
        self.assertEqual(directed.captions.font_name, "Georgia")
        self.assertEqual(directed.transitions.crossfade_seconds, 0.28)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = root / "episodes/demo"
            _write(root / "config/config.json", {"render": {"width": 360, "height": 640}})
            _write(root / "config/style.json", {"highlights": {}})
            _write(episode / "assets.json", {"assets": [{"id": "portrait", "file": "portrait.jpg"}]})
            (episode / "assets").mkdir()
            Image.new("RGB", (360, 640), (80, 90, 110)).save(episode / "assets/portrait.jpg")
            post = {"cover": {
                "headline": "Nossa memoria",
                "source": {"type": "asset", "asset_id": "portrait"},
            }}
            legacy_path = root / "output/legacy.jpg"
            directed_path = root / "output/directed.jpg"
            generate_cover(root, episode, post, legacy_path)
            _write(episode / "story.json", {"artist_vibe": "taylor_swift"})
            _write(root / "config/artist_vibes.json", {
                "schema_version": 1,
                "profiles": {"taylor_swift": {key: value for key, value in DIRECTION.items() if key != "profile"}},
            })
            generate_cover(root, episode, post, directed_path)
            with Image.open(legacy_path) as old, Image.open(directed_path) as new:
                self.assertIsNotNone(ImageChops.difference(old, new).getbbox())
                # The authored image remains intact above the text/gradient.
                self.assertIsNone(ImageChops.difference(old.crop((0, 0, 360, 160)), new.crop((0, 0, 360, 160))).getbbox())

    def test_directed_asset_cover_uses_selected_hook_trim_unless_timestamp_is_authored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = root / "episodes/demo"
            _write(root / "config/config.json", {"render": {"width": 360, "height": 640}})
            _write(root / "config/style.json", {"highlights": {}})
            _write(episode / "assets.json", {"assets": [
                {"id": "hook", "file": "source.mp4"},
                {"id": "other", "file": "source.mp4"},
            ]})
            _write(episode / "timeline.json", {"shots": [{
                "id": "hook", "asset": "hook", "source_start_seconds": 20,
                "source_end_seconds": 28,
            }]})
            (episode / "assets").mkdir()
            (episode / "assets/source.mp4").write_bytes(b"mocked video source")
            post = {"cover": {
                "headline": "Nossa memoria",
                "source": {"type": "asset", "asset_id": "hook"},
            }}
            times = []

            def extract(_source, timestamp, destination):
                times.append(timestamp)
                Image.new("RGB", (360, 640), (80, 90, 110)).save(destination)

            with patch("publishing.cover._extract_frame", side_effect=extract):
                generate_cover(root, episode, post, root / "output/legacy.jpg")
                # Missing local catalog exercises the same shipped fallback as
                # load_episode while opting into the directed hook behavior.
                _write(episode / "story.json", {"artist_vibe": "taylor_swift"})
                generate_cover(root, episode, post, root / "output/directed.jpg")
                post["cover"]["source"]["timestamp_seconds"] = 12
                generate_cover(root, episode, post, root / "output/explicit.jpg")
                del post["cover"]["source"]["timestamp_seconds"]
                post["cover"]["source"]["asset_id"] = "other"
                generate_cover(root, episode, post, root / "output/other.jpg")
            self.assertEqual(times, [1.0, 20.0, 12.0, 1.0])


if __name__ == "__main__":
    unittest.main()
