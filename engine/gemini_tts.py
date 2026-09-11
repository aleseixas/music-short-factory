from __future__ import annotations

import asyncio
import base64
import os
import re
import tempfile
import wave
from pathlib import Path

from .delivery import DEFAULT_DELIVERY
from .ffmpeg import run_ffmpeg
from .models import ScriptSegment, WordTiming


DEFAULT_GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_GEMINI_TRANSCRIBE_MODEL = "gemini-3.5-transcribe"
DEFAULT_GEMINI_VOICE = "Charon"
DEFAULT_GEMINI_TEMPERATURE = 0.6
DEFAULT_GEMINI_LANGUAGE = "pt-BR"
GEMINI_TTS_IMPLEMENTATION_VERSION = "1"

AUDIO_PROFILE = (
    "Brazilian Portuguese male narrator. Mature, masculine, warm and confident, "
    "with a grounded medium-low pitch. Charismatic, spontaneous and naturally "
    "enthusiastic."
)

SCENE = (
    "Short-form social media narration about music stories, curiosities and "
    "behind-the-scenes facts. The narrator speaks directly to the viewer like a "
    "charismatic creator telling a surprising story."
)

DIRECTOR_NOTES = (
    "Style: Newscaster-level clarity and clean diction, but casual, conversational "
    "and never formal. Keep the voice firm and relaxed even during exciting moments. "
    "Pace: natural, fast and fluid with very little dead air. Use subtle, natural "
    "emphasis without raising the pitch. Accent: neutral native Brazilian Portuguese. "
    "Avoid sing-song intonation, theatrical excitement, commercial delivery, excessive "
    "smiling or radio-announcer cadence."
)

SAMPLE_CONTEXT = (
    "The narrator is speaking to an audience on TikTok, Instagram Reels and YouTube "
    "Shorts. The audience should feel curious and entertained. Energy stays high but "
    "natural, confident and spontaneous."
)

DELIVERY_TAGS: dict[str, str] = {
    "neutral": "[conversational]",
    "hook": "[amazed, excited]",
    "curious": "[curious]",
    "emotional": "[warm, reflective]",
    "dramatic": "[serious]",
    "reveal": "[excited]",
    "payoff": "[confident]",
}


class GeminiTTSProvider:
    name = "gemini"

    def __init__(self) -> None:
        self.model = os.getenv("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL).strip()
        self.transcribe_model = os.getenv(
            "GEMINI_TRANSCRIBE_MODEL", DEFAULT_GEMINI_TRANSCRIBE_MODEL
        ).strip()
        self.voice = os.getenv("GEMINI_TTS_VOICE", DEFAULT_GEMINI_VOICE).strip()
        self.language = os.getenv("GEMINI_TTS_LANGUAGE", DEFAULT_GEMINI_LANGUAGE).strip()
        temperature_raw = os.getenv(
            "GEMINI_TTS_TEMPERATURE", str(DEFAULT_GEMINI_TEMPERATURE)
        ).strip()
        try:
            self.temperature = float(temperature_raw)
        except ValueError as exc:
            raise RuntimeError(
                f"GEMINI_TTS_TEMPERATURE invalida: {temperature_raw!r}"
            ) from exc
        if not 0.0 <= self.temperature <= 2.0:
            raise RuntimeError("GEMINI_TTS_TEMPERATURE precisa ficar entre 0 e 2.")
        if not self.model or not self.transcribe_model or not self.voice:
            raise RuntimeError("Configuracao Gemini TTS incompleta.")

    @property
    def cache_identity(self) -> dict[str, str]:
        return {
            "implementation": GEMINI_TTS_IMPLEMENTATION_VERSION,
            "model": self.model,
            "transcribe_model": self.transcribe_model,
            "voice": self.voice,
            "language": self.language,
            "temperature": _compact_float(self.temperature),
            "audio_profile": AUDIO_PROFILE,
            "scene": SCENE,
            "director_notes": DIRECTOR_NOTES,
            "sample_context": SAMPLE_CONTEXT,
        }

    def synthesis_identity(self, delivery: str = DEFAULT_DELIVERY) -> dict[str, str]:
        return {
            **self.cache_identity,
            "delivery": delivery,
            "audio_tag": _delivery_tag(delivery),
        }

    async def synthesize(
        self,
        text: str,
        output: Path,
        *,
        delivery: str = DEFAULT_DELIVERY,
    ) -> tuple[WordTiming, ...]:
        return await asyncio.to_thread(
            self._synthesize_sync,
            ((_delivery_tag(delivery), text.strip()),),
            output,
        )

    async def synthesize_segments(
        self,
        segments: tuple[ScriptSegment, ...],
        output: Path,
    ) -> tuple[WordTiming, ...]:
        if not segments:
            raise RuntimeError("Gemini TTS recebeu um plano de segmentos vazio.")
        tagged_segments = tuple(
            (_delivery_tag(segment.effective_delivery), segment.text.strip())
            for segment in segments
            if segment.text.strip()
        )
        if not tagged_segments:
            raise RuntimeError("Gemini TTS nao recebeu texto para sintetizar.")
        return await asyncio.to_thread(self._synthesize_sync, tagged_segments, output)

    def _synthesize_sync(
        self,
        tagged_segments: tuple[tuple[str, str], ...],
        output: Path,
    ) -> tuple[WordTiming, ...]:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            _raise_diagnostic_error(
                "configuration",
                "GEMINI_API_KEY nao configurada; o fallback de TTS pode assumir.",
                model=self.model,
            )

        try:
            from google import genai
        except ModuleNotFoundError as exc:
            raise _diagnostic_exception(
                "sdk_import",
                exc,
                model=self.model,
                fallback_message=(
                    "Provider Gemini TTS indisponivel. Execute: "
                    "python -m pip install -r requirements.txt"
                ),
            ) from exc

        try:
            client = genai.Client(api_key=api_key)
        except Exception as exc:
            raise _diagnostic_exception(
                "client_init",
                exc,
                model=self.model,
            ) from exc

        prompt = _build_prompt(tagged_segments)
        try:
            interaction = client.interactions.create(
                model=self.model,
                input=prompt,
                response_format={"type": "audio"},
                generation_config={
                    "temperature": self.temperature,
                    "speech_config": [{"voice": self.voice}],
                },
            )
        except Exception as exc:
            raise _diagnostic_exception(
                "synthesis_request",
                exc,
                model=self.model,
            ) from exc

        output_audio = getattr(interaction, "output_audio", None)
        encoded_audio = getattr(output_audio, "data", None)
        if not encoded_audio:
            _raise_diagnostic_error(
                "synthesis_response",
                "Gemini TTS nao retornou audio.",
                model=self.model,
            )
        try:
            pcm = base64.b64decode(encoded_audio)
        except (TypeError, ValueError) as exc:
            raise _diagnostic_exception(
                "audio_decode",
                exc,
                model=self.model,
                fallback_message="Gemini TTS retornou audio em formato invalido.",
            ) from exc
        if len(pcm) < 512:
            _raise_diagnostic_error(
                "audio_validation",
                f"Gemini TTS retornou audio vazio ou truncado ({len(pcm)} bytes).",
                model=self.model,
            )

        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix=".gemini-tts-", dir=output.parent) as temp_dir:
            wav_path = Path(temp_dir) / "narracao.wav"
            try:
                _write_pcm_wave(wav_path, pcm)
            except Exception as exc:
                raise _diagnostic_exception(
                    "wav_write",
                    exc,
                    model=self.model,
                ) from exc
            try:
                run_ffmpeg(
                    [
                        "-y",
                        "-hide_banner",
                        "-i",
                        wav_path,
                        "-vn",
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        output,
                    ]
                )
            except Exception as exc:
                raise _diagnostic_exception(
                    "mp3_encode",
                    exc,
                    model=self.model,
                ) from exc
        if not output.is_file() or output.stat().st_size <= 0:
            _raise_diagnostic_error(
                "mp3_validation",
                "FFmpeg nao produziu o MP3 do Gemini TTS.",
                model=self.model,
            )

        return self._transcribe_word_timings(client, output)

    def _transcribe_word_timings(self, client: object, audio_path: Path) -> tuple[WordTiming, ...]:
        files = getattr(client, "files", None)
        interactions = getattr(client, "interactions", None)
        if files is None or interactions is None:
            _raise_diagnostic_error(
                "transcribe_sdk",
                "SDK google-genai sem suporte a Files/Interactions API.",
                model=self.transcribe_model,
            )

        try:
            uploaded = files.upload(file=str(audio_path))
        except Exception as exc:
            raise _diagnostic_exception(
                "transcribe_upload",
                exc,
                model=self.transcribe_model,
            ) from exc

        uri = getattr(uploaded, "uri", None)
        mime_type = getattr(uploaded, "mime_type", None) or "audio/mp3"
        if not uri:
            _raise_diagnostic_error(
                "transcribe_upload_response",
                "Gemini Transcribe nao recebeu URI do audio enviado.",
                model=self.transcribe_model,
            )

        try:
            interaction = interactions.create(
                model=self.transcribe_model,
                input=[
                    {
                        "type": "audio",
                        "uri": uri,
                        "mime_type": mime_type,
                    }
                ],
                generation_config={
                    "transcription_config": {
                        "language_codes": [self.language],
                        "mode": {
                            "type": "verbatim",
                            "timestamp_granularities": ["word"],
                        },
                    }
                },
            )
        except Exception as exc:
            raise _diagnostic_exception(
                "transcribe_request",
                exc,
                model=self.transcribe_model,
            ) from exc

        words = _extract_word_timings(interaction)
        if not words:
            _raise_diagnostic_error(
                "transcribe_timestamps",
                "Gemini Transcribe nao retornou timestamps por palavra; o fallback de TTS pode assumir.",
                model=self.transcribe_model,
            )
        return words


def _build_prompt(tagged_segments: tuple[tuple[str, str], ...]) -> str:
    transcript_lines = [
        f"{tag} {text}" if tag else text
        for tag, text in tagged_segments
        if text.strip()
    ]
    transcript = "\n".join(transcript_lines)
    if not transcript:
        raise RuntimeError("Gemini TTS nao recebeu texto para sintetizar.")
    return (
        "# AUDIO PROFILE\n"
        f"{AUDIO_PROFILE}\n\n"
        "## THE SCENE\n"
        f"{SCENE}\n\n"
        "### DIRECTOR'S NOTES\n"
        f"{DIRECTOR_NOTES}\n\n"
        "### SAMPLE CONTEXT\n"
        f"{SAMPLE_CONTEXT}\n\n"
        "#### TRANSCRIPT\n"
        "Speak only the transcript below. Audio tags are silent performance directions; "
        "do not pronounce the tags or any of the instructions above.\n"
        f"{transcript}"
    )


def _delivery_tag(delivery: str) -> str:
    try:
        return DELIVERY_TAGS[delivery]
    except KeyError as exc:
        supported = ", ".join(sorted(DELIVERY_TAGS))
        raise ValueError(
            f"Delivery Gemini desconhecido: {delivery!r}. Valores: {supported}."
        ) from exc


def _write_pcm_wave(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)


def _extract_word_timings(interaction: object) -> tuple[WordTiming, ...]:
    result: list[WordTiming] = []
    for step in getattr(interaction, "steps", []) or []:
        for content in getattr(step, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                annotation_type = _field(annotation, "type")
                if annotation_type != "word_info":
                    continue
                text = str(_field(annotation, "text") or "").strip()
                start = _parse_google_offset(_field(annotation, "start_offset"))
                end = _parse_google_offset(_field(annotation, "end_offset"))
                if not text or start is None or end is None or end <= start:
                    continue
                result.append(WordTiming(text, start, end))
    return tuple(result)


def _diagnostic_exception(
    stage: str,
    exc: Exception,
    *,
    model: str | None = None,
    fallback_message: str | None = None,
) -> RuntimeError:
    error_type = type(exc).__name__
    message = _safe_error_message(exc)
    if fallback_message and not message:
        message = fallback_message
    metadata = _error_metadata(exc)
    fields = [f"provider=gemini", f"stage={stage}"]
    if model:
        fields.append(f"model={model}")
    fields.append(f"type={error_type}")
    if metadata:
        fields.append(metadata)
    fields.append(f"message={message or 'sem mensagem'}")
    diagnostic = " ".join(fields)
    print(f"[tts-error] {diagnostic}")
    return RuntimeError(diagnostic)


def _raise_diagnostic_error(
    stage: str,
    message: str,
    *,
    model: str | None = None,
) -> None:
    safe_message = _safe_text(message)
    fields = ["provider=gemini", f"stage={stage}"]
    if model:
        fields.append(f"model={model}")
    fields.append("type=RuntimeError")
    fields.append(f"message={safe_message}")
    diagnostic = " ".join(fields)
    print(f"[tts-error] {diagnostic}")
    raise RuntimeError(diagnostic)


def _safe_error_message(exc: Exception) -> str:
    return _safe_text(str(exc))


def _safe_text(value: str) -> str:
    raw = value.strip()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        raw = raw.replace(api_key, "***")
    raw = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "***", raw)
    raw = " ".join(raw.split())
    if len(raw) > 1600:
        raw = raw[:1597] + "..."
    return raw


def _error_metadata(exc: Exception) -> str:
    values: list[str] = []
    seen: set[str] = set()

    for name in ("status_code", "code", "status"):
        value = getattr(exc, name, None)
        if value is None:
            continue
        rendered = _safe_text(str(value))
        if rendered and rendered not in seen:
            values.append(f"{name}={rendered}")
            seen.add(rendered)

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None) if response is not None else None
    if response_status is not None:
        rendered = _safe_text(str(response_status))
        if rendered and rendered not in seen:
            values.append(f"http_status={rendered}")

    return " ".join(values)


def _field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _parse_google_offset(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", raw)
    if not match:
        return None
    return float(match.group(1))


def _compact_float(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")
