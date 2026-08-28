import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.audio import resolve_audio
from engine.config import TTSSettings
from engine.models import WordTiming


class FakeProvider:
    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self.fail = fail
        self.calls: list[str] = []

    @property
    def cache_identity(self) -> dict[str, str]:
        return {"fixture": self.name}

    async def synthesize(self, text: str, output: Path) -> tuple[WordTiming, ...]:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"audio from {self.name}".encode("ascii"))
        tokens = text.split()
        step = 1.0 / len(tokens)
        return tuple(
            WordTiming(token, index * step, (index + 1) * step)
            for index, token in enumerate(tokens)
        )


def settings(
    provider: str = "primary",
    fallback_provider: str | None = "fallback",
) -> TTSSettings:
    return TTSSettings(
        custom_audio="assets/custom_voice.mp3",
        custom_timings="assets/custom_voice.srt",
        provider=provider,
        fallback_provider=fallback_provider,
        reuse_generated_audio=False,
        allow_estimated_custom_timings=False,
        edge_voice="unused",
        edge_rate="+0%",
    )


class AudioSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_audio_with_srt_has_priority_over_providers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets_dir = root / "episode" / "assets"
            assets_dir.mkdir(parents=True)
            custom_audio = assets_dir / "custom_voice.mp3"
            custom_audio.write_bytes(b"custom audio fixture")
            (assets_dir / "custom_voice.srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nola mundo\n",
                encoding="utf-8",
            )
            primary = FakeProvider("primary")

            with patch("engine.audio.probe_duration", return_value=1.0):
                result = await resolve_audio(
                    "ola mundo",
                    settings(),
                    root / "episode",
                    root / "cache",
                    providers={"primary": primary},
                )

        self.assertEqual(result.source, "custom")
        self.assertEqual(result.path, custom_audio.resolve())
        self.assertTrue(result.exact_timings)
        self.assertEqual(primary.calls, [])

    async def test_configured_provider_is_used_before_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "episode").mkdir()
            primary = FakeProvider("primary")
            fallback = FakeProvider("fallback")

            with patch("engine.audio.probe_duration", return_value=1.0):
                result = await resolve_audio(
                    "ola mundo",
                    settings(),
                    root / "episode",
                    root / "cache",
                    providers={"primary": primary, "fallback": fallback},
                )

        self.assertEqual(result.source, "primary")
        self.assertEqual(primary.calls, ["ola mundo"])
        self.assertEqual(fallback.calls, [])
        self.assertTrue(result.exact_timings)

    async def test_custom_audio_without_srt_does_not_override_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets_dir = root / "episode" / "assets"
            assets_dir.mkdir(parents=True)
            (assets_dir / "custom_voice.mp3").write_bytes(b"custom without timings")
            primary = FakeProvider("primary")

            with patch("engine.audio.probe_duration", return_value=1.0):
                result = await resolve_audio(
                    "ola mundo",
                    settings(),
                    root / "episode",
                    root / "cache",
                    providers={"primary": primary},
                )

        self.assertEqual(result.source, "primary")
        self.assertEqual(primary.calls, ["ola mundo"])

    async def test_fallback_is_used_when_configured_provider_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "episode").mkdir()
            primary = FakeProvider("primary", fail=True)
            fallback = FakeProvider("fallback")

            with patch("engine.audio.probe_duration", return_value=1.0):
                result = await resolve_audio(
                    "ola mundo",
                    settings(),
                    root / "episode",
                    root / "cache",
                    providers={"primary": primary, "fallback": fallback},
                )

        self.assertEqual(result.source, "fallback")
        self.assertEqual(primary.calls, ["ola mundo"])
        self.assertEqual(fallback.calls, ["ola mundo"])
        self.assertTrue(result.exact_timings)


if __name__ == "__main__":
    unittest.main()
