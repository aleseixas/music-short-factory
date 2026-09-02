from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from .config import TTSSettings
from .delivery import DEFAULT_DELIVERY
from .ffmpeg import probe_audio_duration, probe_duration, run_ffmpeg
from .models import AudioResult, ScriptSegment, WordTiming
from .tts import TTSProvider, built_in_providers


SEGMENTED_TTS_CACHE_VERSION = 1


async def resolve_audio(
    narration: str,
    settings: TTSSettings,
    episode_dir: Path,
    cache_dir: Path,
    providers: dict[str, TTSProvider] | None = None,
    *,
    segments: tuple[ScriptSegment, ...] | None = None,
) -> AudioResult:
    """Resolve custom voice, configured provider and fallback, in that order."""

    segment_plan = tuple(segments or ())
    segmented = bool(segment_plan) and any(
        segment.delivery is not None for segment in segment_plan
    )
    if segmented:
        planned_narration = " ".join(
            segment.text.strip() for segment in segment_plan
        ).strip()
        if planned_narration != narration.strip():
            raise RuntimeError(
                "Os segmentos enviados ao TTS nao correspondem a narracao completa."
            )

    custom_audio = _resolve(episode_dir, settings.custom_audio)
    custom_timings = _resolve(episode_dir, settings.custom_timings)
    if custom_timings.exists() and not custom_audio.exists():
        raise RuntimeError(
            f"Timestamps customizados encontrados sem o audio correspondente: {custom_timings}"
        )
    if custom_audio.exists():
        if custom_timings.exists():
            if segmented:
                print(
                    "[tts] delivery por segmento nao aplicado: "
                    "o audio customizado tem prioridade."
                )
            duration = probe_duration(custom_audio)
            words = load_timings(
                custom_timings,
                expected_text=narration,
                audio_duration=duration,
            )
            return AudioResult(custom_audio, duration, words, "custom", True)
        if settings.allow_estimated_custom_timings:
            if segmented:
                print(
                    "[tts] delivery por segmento nao aplicado: "
                    "o audio customizado tem prioridade."
                )
            duration = probe_duration(custom_audio)
            print("[voz] custom_voice.mp3 sem sidecar; usando tempos estimados.")
            return AudioResult(
                custom_audio,
                duration,
                estimate_word_timings(narration, duration),
                "custom-estimated",
                False,
            )
        print("[voz] custom_voice.mp3 sem SRT; seguindo para o provider configurado.")

    provider_map = built_in_providers(settings) if providers is None else dict(providers)
    chain = _provider_chain(settings)
    if not chain:
        raise RuntimeError("Nenhum provider de TTS foi configurado.")

    cache_dir.mkdir(parents=True, exist_ok=True)
    generated = cache_dir / "narracao.mp3"
    generated_timings = cache_dir / "narracao.words.json"
    narration_hash = _narration_hash(narration)
    failures: list[str] = []

    for provider_name in chain:
        provider = provider_map.get(provider_name)
        if provider is None:
            failures.append(f"{provider_name}: provider nao registrado")
            continue

        try:
            # Keep provider-specific identity resolution inside the provider
            # attempt so a broken primary still follows the configured chain.
            provider_options = (
                _provider_options(provider, segment_plan) if segmented else None
            )
        except Exception as exc:
            failures.append(f"{provider_name}: {exc}")
            if provider_name != chain[-1]:
                print(f"[voz] provider {provider_name} falhou; tentando fallback.")
            continue

        if settings.reuse_generated_audio and generated.exists():
            try:
                duration = probe_duration(generated)
                words, exact = _load_provider_timings(
                    generated_timings,
                    narration_hash,
                    provider,
                    generated,
                    narration,
                    duration,
                    provider_options=provider_options,
                )
            except (OSError, RuntimeError) as exc:
                print(f"[voz] cache de audio invalido; regenerando: {exc}")
                generated.unlink(missing_ok=True)
                generated_timings.unlink(missing_ok=True)
                words, exact = (), False
            if words:
                timing_label = "exatos" if exact else "estimados"
                print(
                    f"[voz] usando cache {provider_name} com timestamps {timing_label}."
                )
                return AudioResult(
                    generated,
                    duration,
                    words,
                    f"{provider_name}-cache",
                    exact,
                )
            print(f"[voz] cache incompativel com o provider {provider_name}; regenerando.")

        try:
            print(f"[voz] gerando narracao com provider {provider_name}...")
            if segmented:
                words, duration = await _synthesize_segments(
                    provider,
                    segment_plan,
                    generated,
                    generated_timings,
                    narration,
                )
            else:
                words = await provider.synthesize(narration, generated)
                duration = probe_duration(generated)
                words = _validate_timing_sequence(
                    tuple(words),
                    generated_timings,
                    narration,
                    duration,
                )
            _write_provider_timings(
                generated_timings,
                narration_hash,
                words,
                provider,
                generated,
                provider_options=provider_options,
            )
            return AudioResult(generated, duration, words, provider_name, True)
        except Exception as exc:
            failures.append(f"{provider_name}: {exc}")
            if provider_name != chain[-1]:
                print(f"[voz] provider {provider_name} falhou; tentando fallback.")

    details = "; ".join(failures)
    raise RuntimeError(f"Nenhum provider de TTS conseguiu gerar a narracao. {details}")


async def _synthesize_segments(
    provider: TTSProvider,
    segments: tuple[ScriptSegment, ...],
    output: Path,
    timings_path: Path,
    narration: str,
) -> tuple[tuple[WordTiming, ...], float]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    global_words: list[WordTiming] = []
    offset = 0.0

    with tempfile.TemporaryDirectory(
        prefix=".tts-segments-",
        dir=output.parent,
    ) as temp_dir:
        temp_root = Path(temp_dir)
        audio_parts: list[Path] = []
        for index, segment in enumerate(segments, start=1):
            delivery = segment.effective_delivery
            part = temp_root / f"{index:03d}.mp3"
            print(f"[tts] segment={segment.id} delivery={delivery}")
            local_words = await _provider_synthesize(
                provider,
                segment.text,
                part,
                delivery,
            )
            part_duration = probe_audio_duration(part)
            local_words = _validate_timing_sequence(
                tuple(local_words),
                part,
                segment.text,
                part_duration,
            )
            global_words.extend(
                WordTiming(
                    word.text,
                    word.start + offset,
                    word.end + offset,
                )
                for word in local_words
            )
            offset += part_duration
            audio_parts.append(part)

        combined = temp_root / "narracao.mp3"
        _concatenate_segment_audio(tuple(audio_parts), combined)
        combined.replace(output)

    duration = probe_audio_duration(output)
    words = _validate_timing_sequence(
        tuple(global_words),
        timings_path,
        narration,
        duration,
    )
    return words, duration


async def _provider_synthesize(
    provider: TTSProvider,
    text: str,
    output: Path,
    delivery: str,
) -> tuple[WordTiming, ...]:
    """Pass delivery to modern providers and adapt older implementations safely."""

    try:
        parameters = inspect.signature(provider.synthesize).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_delivery = any(
        (
            parameter.name == "delivery"
            and parameter.kind is not inspect.Parameter.POSITIONAL_ONLY
        )
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if accepts_delivery:
        return await provider.synthesize(text, output, delivery=delivery)
    if delivery != DEFAULT_DELIVERY:
        print(
            f"[tts] provider={provider.name} nao traduz delivery={delivery}; "
            "usando seus controles neutros."
        )
    return await provider.synthesize(text, output)


def _concatenate_segment_audio(parts: tuple[Path, ...], output: Path) -> None:
    if not parts:
        raise RuntimeError("Nao ha segmentos de audio para concatenar.")
    output.unlink(missing_ok=True)
    arguments: list[object] = ["-y", "-hide_banner"]
    for part in parts:
        arguments.extend(("-i", part))
    normalized: list[str] = []
    filter_parts: list[str] = []
    for index in range(len(parts)):
        label = f"segment_{index}"
        normalized.append(f"[{label}]")
        filter_parts.append(
            f"[{index}:a:0]"
            "aformat=sample_fmts=fltp:sample_rates=24000:channel_layouts=mono,"
            f"asetpts=PTS-STARTPTS[{label}]"
        )
    filter_parts.append(
        "".join(normalized) + f"concat=n={len(parts)}:v=0:a=1[out]"
    )
    arguments.extend(
        (
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            output,
        )
    )
    run_ffmpeg(
        arguments
    )
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("FFmpeg nao produziu a narracao segmentada final.")


def _provider_options(
    provider: TTSProvider,
    segments: tuple[ScriptSegment, ...],
) -> object:
    if not segments:
        return provider.cache_identity
    return {
        "segmented_tts_version": SEGMENTED_TTS_CACHE_VERSION,
        "base": provider.cache_identity,
        "segments": [
            {
                "id": segment.id,
                "text_sha256": _narration_hash(segment.text),
                "delivery": segment.effective_delivery,
                "synthesis": _provider_synthesis_identity(
                    provider,
                    segment.effective_delivery,
                ),
            }
            for segment in segments
        ],
    }


def _provider_synthesis_identity(
    provider: TTSProvider,
    delivery: str,
) -> object:
    resolver = getattr(provider, "synthesis_identity", None)
    if callable(resolver):
        try:
            return resolver(delivery)
        except NotImplementedError:
            pass
    return {
        "base": provider.cache_identity,
        "delivery": delivery,
        "delivery_applied": False,
    }


def validate_audio_duration(
    duration: float,
    target_duration_seconds: float,
    tolerance_seconds: float,
) -> str | None:
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"Duracao de audio invalida: {duration!r}")
    if not math.isfinite(target_duration_seconds) or target_duration_seconds <= 0:
        raise RuntimeError(
            f"target_duration_seconds invalido: {target_duration_seconds!r}"
        )
    if not math.isfinite(tolerance_seconds) or tolerance_seconds < 0:
        raise RuntimeError(f"Tolerancia de duracao invalida: {tolerance_seconds!r}")
    deviation = abs(duration - target_duration_seconds)
    if deviation > tolerance_seconds:
        lower = max(0.0, target_duration_seconds - tolerance_seconds)
        upper = target_duration_seconds + tolerance_seconds
        return (
            f"Duracao da narracao fora do alvo: {duration:.2f}s. "
            f"Esperado entre {lower:.2f}s e {upper:.2f}s para o alvo "
            f"de {target_duration_seconds:.2f}s. A duracao real sera usada sem "
            "corte, aceleracao ou nova geracao."
        )
    return None


def load_timings(
    path: Path,
    expected_text: str | None = None,
    audio_duration: float | None = None,
) -> tuple[WordTiming, ...]:
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return _load_srt(path, expected_text, audio_duration)
    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON de timestamps invalido em {path}: {exc}") from exc
        raw_words = data.get("words", []) if isinstance(data, dict) else data
        return _validate_words(raw_words, path, expected_text, audio_duration)
    raise RuntimeError(f"Formato de timestamps nao suportado: {path.suffix}")


def estimate_word_timings(text: str, duration: float) -> tuple[WordTiming, ...]:
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"Duracao invalida para estimar timestamps: {duration!r}")
    tokens = re.findall(r"\S+", text)
    if not tokens:
        raise RuntimeError("Nao ha palavras no roteiro para temporizar.")
    weights: list[float] = []
    for token in tokens:
        weight = 1.0 + min(len(token), 12) * 0.025
        if token.endswith((".", "!", "?")):
            weight += 0.65
        elif token.endswith((",", ";", ":")):
            weight += 0.28
        weights.append(weight)
    scale = duration / sum(weights)
    words: list[WordTiming] = []
    cursor = 0.0
    for index, (token, weight) in enumerate(zip(tokens, weights)):
        end = duration if index == len(tokens) - 1 else cursor + weight * scale
        words.append(WordTiming(token, cursor, end))
        cursor = end
    return tuple(words)


def _provider_chain(settings: TTSSettings) -> list[str]:
    result: list[str] = []
    for name in (settings.provider, settings.fallback_provider):
        if name and name not in result:
            result.append(name)
    return result


def _load_srt(
    path: Path,
    expected_text: str | None,
    audio_duration: float | None,
) -> tuple[WordTiming, ...]:
    content = path.read_text(encoding="utf-8-sig")
    words: list[WordTiming] = []
    for block in re.split(r"\r?\n\s*\r?\n", content.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start_raw, end_raw = [part.strip() for part in lines[timing_index].split("-->", 1)]
        start = _parse_srt_time(start_raw)
        end = _parse_srt_time(end_raw.split()[0])
        if end <= start:
            raise RuntimeError(f"Intervalo SRT invalido em {path}: {start_raw} --> {end_raw}")
        text = re.sub(r"<[^>]+>", "", " ".join(lines[timing_index + 1 :])).strip()
        tokens = re.findall(r"\S+", text)
        if not tokens:
            continue
        token_weights = [max(1.0, len(token) * 0.18) for token in tokens]
        total = sum(token_weights)
        cursor = start
        for index, (token, weight) in enumerate(zip(tokens, token_weights)):
            token_end = end if index == len(tokens) - 1 else cursor + (end - start) * weight / total
            words.append(WordTiming(token, cursor, token_end))
            cursor = token_end
    return _validate_timing_sequence(tuple(words), path, expected_text, audio_duration)


def _parse_srt_time(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d+)", value)
    if not match:
        raise RuntimeError(f"Timestamp SRT invalido: {value}")
    hours_raw, minutes_raw, seconds_raw, fraction_raw = match.groups()
    if int(minutes_raw) >= 60 or int(seconds_raw) >= 60:
        raise RuntimeError(f"Timestamp SRT invalido: {value}")
    fraction = int(fraction_raw) / (10 ** len(fraction_raw))
    return int(hours_raw) * 3600 + int(minutes_raw) * 60 + int(seconds_raw) + fraction


def _validate_words(
    raw_words: object,
    path: Path,
    expected_text: str | None = None,
    audio_duration: float | None = None,
) -> tuple[WordTiming, ...]:
    if not isinstance(raw_words, list):
        raise RuntimeError(f"Lista 'words' invalida em {path}")
    result: list[WordTiming] = []
    for raw in raw_words:
        if not isinstance(raw, dict):
            raise RuntimeError(f"Entrada de palavra invalida em {path}")
        try:
            word = WordTiming(str(raw["text"]).strip(), float(raw["start"]), float(raw["end"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Entrada de palavra invalida em {path}") from exc
        result.append(word)
    return _validate_timing_sequence(tuple(result), path, expected_text, audio_duration)


def _validate_timing_sequence(
    words: tuple[WordTiming, ...],
    path: Path,
    expected_text: str | None,
    audio_duration: float | None,
) -> tuple[WordTiming, ...]:
    if not words:
        raise RuntimeError(f"Nenhuma palavra encontrada em {path}")
    last_end = 0.0
    for word in words:
        if (
            not word.text
            or not math.isfinite(word.start)
            or not math.isfinite(word.end)
            or word.start < 0
            or word.end <= word.start
            or word.start < last_end - 0.001
        ):
            raise RuntimeError(f"Tempos invalidos, sobrepostos ou fora de ordem em {path}")
        last_end = word.end
    if audio_duration is not None and words[-1].end > audio_duration + 0.10:
        raise RuntimeError(
            f"Timestamps terminam depois do audio em {path}: "
            f"{words[-1].end:.3f}s > {audio_duration:.3f}s"
        )
    if expected_text is not None:
        expected = _normalize_tokens(expected_text)
        actual = _normalize_tokens(" ".join(word.text for word in words))
        length_ratio = len(actual) / max(1, len(expected))
        similarity = SequenceMatcher(None, expected, actual, autojunk=False).ratio()
        if not 0.65 <= length_ratio <= 1.35 or similarity < 0.80:
            raise RuntimeError(
                f"O texto dos timestamps em {path} nao corresponde ao roteiro "
                f"(similaridade {similarity:.0%})."
            )
    return words


def _normalize_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.findall(r"[a-z0-9]+", without_accents)


def _load_provider_timings(
    path: Path,
    expected_hash: str,
    provider: TTSProvider,
    audio_path: Path,
    expected_text: str,
    audio_duration: float,
    provider_options: object | None = None,
) -> tuple[tuple[WordTiming, ...], bool]:
    if not path.exists():
        return (), False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return (), False
    if not isinstance(data, dict) or not bool(data.get("exact", False)):
        return (), False

    expected_provider_options = (
        provider.cache_identity if provider_options is None else provider_options
    )
    provider_matches = (
        data.get("provider") == provider.name
        and data.get("provider_options") == expected_provider_options
    )
    if (
        provider_options is None
        and provider.name == "edge"
        and data.get("provider") is None
    ):
        provider_matches = (
            data.get("edge_voice") == provider.cache_identity.get("voice")
            and data.get("edge_rate") == provider.cache_identity.get("rate")
        )
    if (
        data.get("narration_sha256") != expected_hash
        or not provider_matches
        or data.get("audio_sha256") != _file_hash(audio_path)
    ):
        return (), False
    try:
        words = _validate_words(data.get("words", []), path, expected_text, audio_duration)
    except RuntimeError:
        return (), False
    return words, True


def _write_provider_timings(
    path: Path,
    narration_hash: str,
    words: tuple[WordTiming, ...],
    provider: TTSProvider,
    audio_path: Path,
    provider_options: object | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "narration_sha256": narration_hash,
        "provider": provider.name,
        "provider_options": (
            provider.cache_identity if provider_options is None else provider_options
        ),
        "audio_sha256": _file_hash(audio_path),
        "exact": True,
        "words": [
            {"text": word.text, "start": round(word.start, 6), "end": round(word.end, 6)}
            for word in words
        ],
    }
    if provider.name == "edge":
        data["edge_voice"] = provider.cache_identity.get("voice", "")
        data["edge_rate"] = provider.cache_identity.get("rate", "")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class _LegacyEdgeProvider:
    name = "edge"

    def __init__(self, voice: str, rate: str):
        self.voice = voice
        self.rate = rate

    @property
    def cache_identity(self) -> dict[str, str]:
        return {"voice": self.voice, "rate": self.rate}

    def synthesis_identity(self, delivery: str = DEFAULT_DELIVERY) -> dict[str, str]:
        return {**self.cache_identity, "delivery": delivery}

    async def synthesize(
        self,
        text: str,
        output: Path,
        *,
        delivery: str = DEFAULT_DELIVERY,
    ) -> tuple[WordTiming, ...]:
        raise NotImplementedError


def _load_generated_timings(
    path: Path,
    expected_hash: str,
    expected_voice: str,
    expected_rate: str,
    audio_path: Path,
    expected_text: str,
    audio_duration: float,
) -> tuple[tuple[WordTiming, ...], bool]:
    provider = _LegacyEdgeProvider(expected_voice, expected_rate)
    return _load_provider_timings(
        path,
        expected_hash,
        provider,
        audio_path,
        expected_text,
        audio_duration,
    )


def _write_generated_timings(
    path: Path,
    narration_hash: str,
    words: tuple[WordTiming, ...],
    exact: bool,
    edge_voice: str,
    edge_rate: str,
    audio_path: Path,
) -> None:
    provider = _LegacyEdgeProvider(edge_voice, edge_rate)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "narration_sha256": narration_hash,
        "provider": provider.name,
        "provider_options": provider.cache_identity,
        "edge_voice": edge_voice,
        "edge_rate": edge_rate,
        "audio_sha256": _file_hash(audio_path),
        "exact": exact,
        "words": [
            {"text": word.text, "start": round(word.start, 6), "end": round(word.end, 6)}
            for word in words
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Caminho de audio fora do episodio: {resolved}") from exc
    return resolved


def _narration_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
