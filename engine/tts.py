from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from .config import TTSSettings
from .delivery import DEFAULT_DELIVERY, get_delivery_preset
from .models import WordTiming


TICKS_PER_SECOND = 10_000_000


@runtime_checkable
class TTSProvider(Protocol):
    """Interface minima para providers de sintese de voz."""

    name: str

    @property
    def cache_identity(self) -> dict[str, str]: ...

    def synthesis_identity(self, delivery: str = DEFAULT_DELIVERY) -> dict[str, str]: ...

    async def synthesize(
        self,
        text: str,
        output: Path,
        *,
        delivery: str = DEFAULT_DELIVERY,
    ) -> tuple[WordTiming, ...]: ...


class EdgeTTSProvider:
    name = "edge"

    def __init__(self, voice: str, rate: str):
        self.voice = voice
        self.rate = rate

    @property
    def cache_identity(self) -> dict[str, str]:
        return {"voice": self.voice, "rate": self.rate}

    def synthesis_identity(self, delivery: str = DEFAULT_DELIVERY) -> dict[str, str]:
        preset = get_delivery_preset(delivery)
        base_rate = _parse_percentage(self.rate, "rate")
        return {
            "voice": self.voice,
            "delivery": delivery,
            "rate": _format_signed(base_rate + preset.rate_delta_percent, "%"),
            "pitch": _format_signed(preset.pitch_delta_hz, "Hz"),
            "volume": _format_signed(preset.volume_delta_percent, "%"),
        }

    async def synthesize(
        self,
        text: str,
        output: Path,
        *,
        delivery: str = DEFAULT_DELIVERY,
    ) -> tuple[WordTiming, ...]:
        try:
            import edge_tts
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Provider Edge TTS indisponivel. Execute: "
                "python -m pip install -r requirements.txt"
            ) from exc

        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".part")
        partial.unlink(missing_ok=True)
        words: list[WordTiming] = []
        controls = self.synthesis_identity(delivery)
        communicate = edge_tts.Communicate(
            text,
            self.voice,
            rate=controls["rate"],
            pitch=controls["pitch"],
            volume=controls["volume"],
            boundary="WordBoundary",
        )
        try:
            with partial.open("wb") as audio_file:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_file.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start = float(chunk["offset"]) / TICKS_PER_SECOND
                        end = start + float(chunk["duration"]) / TICKS_PER_SECOND
                        words.append(WordTiming(str(chunk["text"]), start, end))
            if not words:
                raise RuntimeError("Edge TTS nao retornou marcadores de palavra.")
            partial.replace(output)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return tuple(words)


def built_in_providers(settings: TTSSettings) -> dict[str, TTSProvider]:
    edge = EdgeTTSProvider(settings.edge_voice, settings.edge_rate)
    return {edge.name: edge}


def _parse_percentage(value: str, label: str) -> int:
    match = re.fullmatch(r"([+-])(\d+)%", value.strip())
    if not match:
        raise RuntimeError(
            f"Parametro {label} invalido para Edge TTS: use o formato '+6%' ou '-4%'."
        )
    amount = int(match.group(2))
    return amount if match.group(1) == "+" else -amount


def _format_signed(value: int, suffix: str) -> str:
    return f"{value:+d}{suffix}"
