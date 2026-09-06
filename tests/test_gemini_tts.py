import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.audio import resolve_audio
from engine.config import TTSSettings
from engine.gemini_tts import _build_prompt, _extract_word_timings
from engine.models import ScriptSegment, WordTiming


class _Annotation:
    def __init__(self, text: str, start: str, end: str):
        self.type = "word_info"
        self.text = text
        self.start_offset = start
        self.end_offset = end


class _Content:
    def __init__(self, annotations: list[object]):
        self.annotations = annotations


class _Step:
    def __init__(self, content: list[object]):
        self.content = content


class _Interaction:
    def __init__(self, steps: list[object]):
        self.steps = steps


class BulkProvider:
    name = "bulk"

    def __init__(self):
        self.segment_calls: list[tuple[ScriptSegment, ...]] = []
        self.single_calls: list[str] = []

    @property
    def cache_identity(self) -> dict[str, str]:
        return {"fixture": "bulk"}

    def synthesis_identity(self, delivery: str = "neutral") -> dict[str, str]:
        return {"fixture": "bulk", "delivery": delivery}

    async def synthesize(
        self,
        text: str,
        output: Path,
        *,
        delivery: str = "neutral",
    ) -> tuple[WordTiming, ...]:
        self.single_calls.append(text)
        raise AssertionError("bulk provider should not use per-segment synthesize")

    async def synthesize_segments(
        self,
        segments: tuple[ScriptSegment, ...],
        output: Path,
    ) -> tuple[WordTiming, ...]:
        self.segment_calls.append(segments)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"bulk-audio")
        return (
            WordTiming("primeiro", 0.0, 0.8),
            WordTiming("segundo", 0.9, 1.8),
        )


def _settings() -> TTSSettings:
    return TTSSettings(
        custom_audio="assets/custom_voice.mp3",
        custom_timings="assets/custom_voice.srt",
        provider="bulk",
        fallback_provider=None,
        reuse_generated_audio=False,
        allow_estimated_custom_timings=False,
        edge_voice="unused",
        edge_rate="+0%",
    )


class GeminiTTSHelperTests(unittest.TestCase):
    def test_prompt_keeps_direction_and_delivery_tags(self):
        prompt = _build_prompt(
            (
                ("[amazed, excited]", "Primeira frase."),
                ("[serious]", "Segunda frase."),
            )
        )
        self.assertIn("# AUDIO PROFILE", prompt)
        self.assertIn("### DIRECTOR'S NOTES", prompt)
        self.assertIn("[amazed, excited] Primeira frase.", prompt)
        self.assertIn("[serious] Segunda frase.", prompt)
        self.assertIn("Speak only the transcript below", prompt)

    def test_extracts_google_word_offsets(self):
        interaction = _Interaction(
            [
                _Step(
                    [
                        _Content(
                            [
                                _Annotation("Ola", "0.100s", "0.400s"),
                                _Annotation("mundo", "0.500s", "0.900s"),
                            ]
                        )
                    ]
                )
            ]
        )
        words = _extract_word_timings(interaction)
        self.assertEqual([word.text for word in words], ["Ola", "mundo"])
        self.assertAlmostEqual(words[0].start, 0.1)
        self.assertAlmostEqual(words[1].end, 0.9)


class BulkSegmentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_segmented_plan_uses_one_bulk_provider_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode = root / "episode"
            episode.mkdir()
            provider = BulkProvider()
            segments = (
                ScriptSegment("s01", "primeiro", "hook"),
                ScriptSegment("s02", "segundo", "payoff"),
            )

            with patch("engine.audio.probe_audio_duration", return_value=2.0):
                result = await resolve_audio(
                    "primeiro segundo",
                    _settings(),
                    episode,
                    root / "cache",
                    providers={"bulk": provider},
                    segments=segments,
                )

        self.assertEqual(len(provider.segment_calls), 1)
        self.assertEqual(provider.single_calls, [])
        self.assertEqual(result.source, "bulk")
        self.assertEqual([word.text for word in result.words], ["primeiro", "segundo"])


if __name__ == "__main__":
    unittest.main()
