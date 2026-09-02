import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from engine.audio import resolve_audio
from engine.config import TTSSettings
from engine.models import ScriptSegment, WordTiming


class RecordingProvider:
    def __init__(self, name: str, fail_on_text: str | None = None):
        self.name = name
        self.fail_on_text = fail_on_text
        self.calls: list[tuple[str, str]] = []

    @property
    def cache_identity(self) -> dict[str, str]:
        return {"fixture": self.name}

    def synthesis_identity(self, delivery: str) -> dict[str, str]:
        return {"fixture": self.name, "delivery": delivery}

    async def synthesize(
        self,
        text: str,
        output: Path,
        delivery: str = "neutral",
    ) -> tuple[WordTiming, ...]:
        self.calls.append((text, delivery))
        if text == self.fail_on_text:
            raise RuntimeError(f"{self.name} failed")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"{self.name}|{text}|{delivery}".encode("utf-8"))
        if text == "primeiro":
            return (WordTiming(text, 0.2, 2.5),)
        if text == "segundo":
            return (WordTiming(text, 1.0, 1.5),)
        tokens = text.split()
        step = 2.0 / len(tokens)
        return tuple(
            WordTiming(token, index * step, (index + 1) * step)
            for index, token in enumerate(tokens)
        )


def make_settings(
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


def fake_probe_duration(path: Path) -> float:
    content = Path(path).read_bytes()
    if content == b"joined":
        return 5.0
    if b"|primeiro|" in content:
        return 3.0
    if b"|segundo|" in content:
        return 2.0
    if content == b"custom audio":
        return 2.0
    raise AssertionError(f"unexpected probe input: {path}")


def fake_run_ffmpeg(arguments: object, cwd: Path | None = None) -> None:
    args = list(arguments)  # type: ignore[arg-type]
    destination = Path(args[-1])
    if cwd is not None and not destination.is_absolute():
        destination = Path(cwd) / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"joined")


class SegmentedTTSTests(unittest.IsolatedAsyncioTestCase):
    async def test_omitted_delivery_keeps_single_legacy_provider_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode = root / "episode"
            episode.mkdir()
            provider = RecordingProvider("primary")
            segments = (
                ScriptSegment("s01", "primeiro"),
                ScriptSegment("s02", "segundo"),
            )

            with patch("engine.audio.probe_duration", return_value=2.0):
                result = await resolve_audio(
                    "primeiro segundo",
                    make_settings(fallback_provider=None),
                    episode,
                    root / "cache",
                    providers={"primary": provider},
                    segments=segments,
                )

        self.assertEqual(provider.calls, [("primeiro segundo", "neutral")])
        self.assertEqual(result.duration, 2.0)

    async def test_delivery_reaches_provider_and_local_timings_become_global(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode = root / "episode"
            episode.mkdir()
            provider = RecordingProvider("primary")
            segments = (
                ScriptSegment("s01", "primeiro", "hook"),
                ScriptSegment("s02", "segundo", "reveal"),
            )
            stdout = io.StringIO()

            with (
                patch("engine.audio.probe_audio_duration", side_effect=fake_probe_duration),
                patch("engine.audio.run_ffmpeg", side_effect=fake_run_ffmpeg, create=True),
                redirect_stdout(stdout),
            ):
                result = await resolve_audio(
                    "primeiro segundo",
                    make_settings(fallback_provider=None),
                    episode,
                    root / "cache",
                    providers={"primary": provider},
                    segments=segments,
                )

        self.assertEqual(
            provider.calls,
            [("primeiro", "hook"), ("segundo", "reveal")],
        )
        self.assertEqual(result.duration, 5.0)
        self.assertAlmostEqual(result.words[0].start, 0.2)
        # The second word starts at 1s locally, after the first 3s clip.
        self.assertAlmostEqual(result.words[1].start, 4.0)
        self.assertAlmostEqual(result.words[1].end, 4.5)
        self.assertIn("[tts] segment=s01 delivery=hook", stdout.getvalue())
        self.assertIn("[tts] segment=s02 delivery=reveal", stdout.getvalue())

    async def test_fallback_restarts_the_complete_segment_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode = root / "episode"
            episode.mkdir()
            primary = RecordingProvider("primary", fail_on_text="segundo")
            fallback = RecordingProvider("fallback")
            segments = (
                ScriptSegment("s01", "primeiro", "hook"),
                ScriptSegment("s02", "segundo", "emotional"),
            )

            with (
                patch("engine.audio.probe_audio_duration", side_effect=fake_probe_duration),
                patch("engine.audio.run_ffmpeg", side_effect=fake_run_ffmpeg, create=True),
            ):
                result = await resolve_audio(
                    "primeiro segundo",
                    make_settings(),
                    episode,
                    root / "cache",
                    providers={"primary": primary, "fallback": fallback},
                    segments=segments,
                )

        self.assertEqual(
            primary.calls,
            [("primeiro", "hook"), ("segundo", "emotional")],
        )
        self.assertEqual(
            fallback.calls,
            [("primeiro", "hook"), ("segundo", "emotional")],
        )
        self.assertEqual(result.source, "fallback")
        self.assertAlmostEqual(result.words[1].start, 4.0)

    async def test_invalid_primary_synthesis_identity_uses_fallback(self):
        class BrokenIdentityProvider(RecordingProvider):
            def synthesis_identity(self, delivery: str) -> dict[str, str]:
                raise RuntimeError("invalid provider controls")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode = root / "episode"
            episode.mkdir()
            primary = BrokenIdentityProvider("primary")
            fallback = RecordingProvider("fallback")
            segments = (
                ScriptSegment("s01", "primeiro", "hook"),
                ScriptSegment("s02", "segundo", "payoff"),
            )

            with (
                patch("engine.audio.probe_audio_duration", side_effect=fake_probe_duration),
                patch("engine.audio.run_ffmpeg", side_effect=fake_run_ffmpeg),
            ):
                result = await resolve_audio(
                    "primeiro segundo",
                    make_settings(),
                    episode,
                    root / "cache",
                    providers={"primary": primary, "fallback": fallback},
                    segments=segments,
                )

        self.assertEqual(primary.calls, [])
        self.assertEqual(
            fallback.calls,
            [("primeiro", "hook"), ("segundo", "payoff")],
        )
        self.assertEqual(result.source, "fallback")

    async def test_custom_audio_with_sidecar_keeps_priority_over_segment_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode = root / "episode"
            assets = episode / "assets"
            assets.mkdir(parents=True)
            custom_audio = assets / "custom_voice.mp3"
            custom_audio.write_bytes(b"custom audio")
            (assets / "custom_voice.srt").write_text(
                "1\n00:00:00,000 --> 00:00:02,000\nvoz pronta\n",
                encoding="utf-8",
            )
            primary = RecordingProvider("primary")
            segments = (ScriptSegment("s01", "voz pronta", "dramatic"),)

            with patch("engine.audio.probe_duration", side_effect=fake_probe_duration):
                result = await resolve_audio(
                    "voz pronta",
                    make_settings(fallback_provider=None),
                    episode,
                    root / "cache",
                    providers={"primary": primary},
                    segments=segments,
                )

        self.assertEqual(result.source, "custom")
        self.assertEqual(result.path, custom_audio.resolve())
        self.assertTrue(result.exact_timings)
        self.assertEqual(primary.calls, [])


if __name__ == "__main__":
    unittest.main()
