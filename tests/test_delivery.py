import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from engine.audio import (
    _load_provider_timings,
    _narration_hash,
    _provider_options,
    _write_provider_timings,
)
from engine.delivery import DELIVERY_PRESETS, get_delivery_preset
from engine.episode import load_story
from engine.models import ScriptSegment, WordTiming
from engine.tts import EdgeTTSProvider


DELIVERIES = (
    "neutral",
    "hook",
    "curious",
    "emotional",
    "dramatic",
    "reveal",
    "payoff",
)


class DeliveryStoryTests(unittest.TestCase):
    def _load(self, segments: list[dict[str, object]]):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "story.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "title": "Delivery test",
                        "slug": "delivery_test",
                        "target_duration_seconds": 75,
                        "segments": segments,
                    }
                ),
                encoding="utf-8",
            )
            return load_story(path)

    def test_legacy_segment_defaults_to_effective_neutral(self):
        story = self._load([{"id": "s01", "text": "Legacy narration."}])

        self.assertIsNone(story.segments[0].delivery)
        self.assertEqual(story.segments[0].effective_delivery, "neutral")
        self.assertEqual(story.narration, "Legacy narration.")

    def test_all_supported_deliveries_are_parsed(self):
        story = self._load(
            [
                {"id": f"s{index:02}", "text": name, "delivery": name}
                for index, name in enumerate(DELIVERIES, start=1)
            ]
        )

        self.assertEqual(
            tuple(segment.delivery for segment in story.segments),
            DELIVERIES,
        )
        self.assertEqual(
            tuple(segment.effective_delivery for segment in story.segments),
            DELIVERIES,
        )

    def test_invalid_delivery_is_rejected_clearly(self):
        for delivery in ("unknown", "", 123):
            with self.subTest(delivery=delivery):
                with self.assertRaisesRegex(RuntimeError, "[Dd]elivery"):
                    self._load(
                        [
                            {
                                "id": "s01",
                                "text": "Invalid delivery.",
                                "delivery": delivery,
                            }
                        ]
                    )

    def test_existing_episode_stories_remain_loadable(self):
        project_root = Path(__file__).resolve().parents[1]
        story_paths = sorted((project_root / "episodes").glob("*/story.json"))

        self.assertTrue(story_paths)
        for story_path in story_paths:
            with self.subTest(story=story_path.parent.name):
                story = load_story(story_path)
                self.assertTrue(story.segments)
                self.assertTrue(
                    all(
                        segment.effective_delivery in DELIVERY_PRESETS
                        for segment in story.segments
                    )
                )


class DeliveryPresetTests(unittest.TestCase):
    def test_catalog_contains_only_the_initial_supported_presets(self):
        self.assertEqual(set(DELIVERY_PRESETS), set(DELIVERIES))
        for name in DELIVERIES:
            with self.subTest(delivery=name):
                self.assertEqual(get_delivery_preset(name), DELIVERY_PRESETS[name])

    def test_unknown_preset_is_rejected(self):
        with self.assertRaises((KeyError, RuntimeError, ValueError)):
            get_delivery_preset("unknown")


class EdgeDeliveryMappingTests(unittest.IsolatedAsyncioTestCase):
    def test_every_preset_maps_to_effective_edge_controls(self):
        provider = EdgeTTSProvider("pt-BR-AntonioNeural", "+6%")

        for delivery, preset in DELIVERY_PRESETS.items():
            with self.subTest(delivery=delivery):
                identity = provider.synthesis_identity(delivery)
                self.assertEqual(identity["voice"], "pt-BR-AntonioNeural")
                self.assertEqual(identity["delivery"], delivery)
                self.assertEqual(
                    identity["rate"],
                    f"{6 + preset.rate_delta_percent:+d}%",
                )
                self.assertEqual(
                    identity["pitch"],
                    f"{preset.pitch_delta_hz:+d}Hz",
                )
                self.assertEqual(
                    identity["volume"],
                    f"{preset.volume_delta_percent:+d}%",
                )

    async def test_edge_uses_only_supported_controls_and_preserves_neutral_rate(self):
        calls: list[tuple[str, str, dict[str, object]]] = []

        class FakeCommunicate:
            def __init__(self, text: str, voice: str, **options: object):
                calls.append((text, voice, options))

            async def stream(self):
                yield {"type": "audio", "data": b"audio"}
                yield {
                    "type": "WordBoundary",
                    "offset": 0,
                    "duration": 2_500_000,
                    "text": "teste",
                }

        provider = EdgeTTSProvider("pt-BR-AntonioNeural", "+6%")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "voice.mp3"
            with patch.dict(
                sys.modules,
                {"edge_tts": SimpleNamespace(Communicate=FakeCommunicate)},
            ):
                await provider.synthesize("teste", output, delivery="neutral")

        identity = provider.synthesis_identity("neutral")
        self.assertEqual(identity["voice"], "pt-BR-AntonioNeural")
        self.assertEqual(identity["rate"], "+6%")
        self.assertEqual(calls[0][0], "teste")
        self.assertEqual(calls[0][1], identity["voice"])
        self.assertEqual(calls[0][2]["rate"], identity["rate"])
        self.assertEqual(calls[0][2]["pitch"], identity["pitch"])
        self.assertEqual(calls[0][2]["volume"], identity["volume"])
        self.assertEqual(calls[0][2]["boundary"], "WordBoundary")
        self.assertEqual(
            set(calls[0][2]),
            {"rate", "pitch", "volume", "boundary"},
        )

    def test_non_neutral_deliveries_change_effective_edge_identity(self):
        provider = EdgeTTSProvider("pt-BR-AntonioNeural", "+6%")
        neutral = provider.synthesis_identity("neutral")

        self.assertNotEqual(provider.synthesis_identity("hook"), neutral)
        self.assertNotEqual(provider.synthesis_identity("emotional"), neutral)
        self.assertEqual(provider.synthesis_identity("hook")["voice"], neutral["voice"])
        self.assertEqual(
            provider.synthesis_identity("emotional")["voice"],
            neutral["voice"],
        )


class DeliveryCacheTests(unittest.TestCase):
    def test_same_text_with_different_delivery_does_not_reuse_cached_timings(self):
        provider = EdgeTTSProvider("pt-BR-AntonioNeural", "+6%")
        neutral_segments = (ScriptSegment("s01", "mesmo texto", "neutral"),)
        emotional_segments = (ScriptSegment("s01", "mesmo texto", "emotional"),)
        neutral_options = _provider_options(provider, neutral_segments)
        emotional_options = _provider_options(provider, emotional_segments)
        words = (
            WordTiming("mesmo", 0.0, 0.4),
            WordTiming("texto", 0.4, 1.0),
        )

        self.assertNotEqual(neutral_options, emotional_options)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "narracao.mp3"
            sidecar = root / "narracao.words.json"
            audio.write_bytes(b"same audio fixture")
            narration_hash = _narration_hash("mesmo texto")
            _write_provider_timings(
                sidecar,
                narration_hash,
                words,
                provider,
                audio,
                provider_options=neutral_options,
            )

            cached_neutral, exact = _load_provider_timings(
                sidecar,
                narration_hash,
                provider,
                audio,
                "mesmo texto",
                1.0,
                provider_options=neutral_options,
            )
            cached_emotional, emotional_exact = _load_provider_timings(
                sidecar,
                narration_hash,
                provider,
                audio,
                "mesmo texto",
                1.0,
                provider_options=emotional_options,
            )

        self.assertEqual(cached_neutral, words)
        self.assertTrue(exact)
        self.assertEqual(cached_emotional, ())
        self.assertFalse(emotional_exact)


if __name__ == "__main__":
    unittest.main()
