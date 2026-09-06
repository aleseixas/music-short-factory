from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from cloudinary import uploader as cloudinary_uploader
import requests

from publishing.base import ApiError, PublishContext, Publisher, PublishingError
from publishing.credentials import CredentialStore
from publishing.instagram import InstagramPublisher
from publishing.metadata import PLATFORMS, load_post
from publishing.tiktok import TikTokPublisher
from publishing.youtube import YouTubePublisher


PUBLISHER_TYPES: dict[str, type[Publisher]] = {
    "youtube": YouTubePublisher,
    "instagram": InstagramPublisher,
    "tiktok": TikTokPublisher,
}


def create_publisher(
    platform: str,
    credentials: CredentialStore,
    session: requests.Session | None = None,
) -> Publisher:
    try:
        publisher_type = PUBLISHER_TYPES[platform]
    except KeyError as exc:
        raise PublishingError(f"Plataforma desconhecida: {platform!r}.") from exc
    return publisher_type(credentials=credentials, session=session)


def build_context(project_root: Path, episode: str, platform: str) -> PublishContext:
    if not re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", episode):
        raise PublishingError(
            f"Nome de episodio invalido: {episode!r}. "
            "Use letras minusculas, numeros, '_' ou '-'."
        )
    config = _load_json(project_root / "config" / "config.json")
    paths = config.get("paths")
    if not isinstance(paths, Mapping):
        raise PublishingError("config/config.json precisa conter o objeto 'paths'.")
    episodes_dir = str(paths.get("episodes_dir", "episodes"))
    output_dir = str(paths.get("output_dir", "output"))
    episode_dir = (project_root / episodes_dir / episode).resolve()
    episodes_root = (project_root / episodes_dir).resolve()
    try:
        episode_dir.relative_to(episodes_root)
    except ValueError as exc:
        raise PublishingError(f"Caminho de episodio fora da pasta permitida: {episode_dir}") from exc
    if not episode_dir.is_dir():
        raise PublishingError(f"Episodio {episode!r} nao encontrado em {episode_dir}.")
    post = load_post(
        episode_dir / "post.json",
        warning_platforms=(platform,),
    )
    output_root = (project_root / output_dir).resolve()
    video_path = (output_root / f"{episode}.mp4").resolve()
    cover_path = (output_root / f"{episode}_cover.jpg").resolve()
    for path in (video_path, cover_path):
        try:
            path.relative_to(output_root)
        except ValueError as exc:
            raise PublishingError(f"Arquivo de publicacao fora de output/: {path}") from exc
    return PublishContext(
        episode=episode,
        video_path=video_path,
        cover_path=cover_path,
        metadata=post.for_platform(platform),
    )


def _prepare_instagram_cover(
    context: PublishContext,
    credentials: CredentialStore,
) -> tuple[PublishContext, str | None]:
    """Temporarily host the generated JPEG so Meta can use it as Reel cover."""

    existing_cover_url = str(context.metadata.get("cover_url", "")).strip()
    if existing_cover_url:
        return context, None

    if credentials.get("INSTAGRAM_VIDEO_HOST").strip().lower() != "cloudinary":
        return context, None

    required = (
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
    )
    if any(not credentials.has(key) for key in required):
        return context, None

    public_id = f"instagram/{context.episode}/cover-{uuid4().hex}"
    try:
        result = cloudinary_uploader.upload(
            str(context.cover_path),
            resource_type="image",
            public_id=public_id,
            overwrite=False,
            cloud_name=credentials.get("CLOUDINARY_CLOUD_NAME"),
            api_key=credentials.get("CLOUDINARY_API_KEY"),
            api_secret=credentials.get("CLOUDINARY_API_SECRET"),
            timeout=90.0,
        )
        if not isinstance(result, Mapping):
            raise ValueError("unexpected Cloudinary response")
        cover_url = str(result.get("secure_url", "")).strip()
        if not cover_url.startswith("https://"):
            raise ValueError("Cloudinary secure_url is not HTTPS")
    except Exception as exc:
        print(
            "[instagram] cover warning: upload temporario falhou "
            f"({exc.__class__.__name__}); usando thumb_offset.",
            file=sys.stderr,
        )
        return context, None

    metadata = dict(context.metadata)
    metadata["cover_url"] = cover_url
    print(f"[cloudinary] uploaded cover: {public_id}")
    return (
        PublishContext(
            episode=context.episode,
            video_path=context.video_path,
            cover_path=context.cover_path,
            metadata=metadata,
        ),
        public_id,
    )


def _cleanup_instagram_cover(
    public_id: str,
    credentials: CredentialStore,
) -> None:
    try:
        result = cloudinary_uploader.destroy(
            public_id,
            resource_type="image",
            type="upload",
            invalidate=True,
            cloud_name=credentials.get("CLOUDINARY_CLOUD_NAME"),
            api_key=credentials.get("CLOUDINARY_API_KEY"),
            api_secret=credentials.get("CLOUDINARY_API_SECRET"),
            timeout=90.0,
        )
    except Exception as exc:
        print(
            "[instagram] cover cleanup warning: falhou "
            f"({exc.__class__.__name__}).",
            file=sys.stderr,
        )
        return

    outcome = (
        str(result.get("result", "")).strip().casefold()
        if isinstance(result, Mapping)
        else ""
    )
    if outcome == "ok":
        print(f"[cloudinary] deleted cover: {public_id}")
    elif outcome != "not found":
        print(
            "[instagram] cover cleanup warning: Cloudinary nao confirmou a remocao.",
            file=sys.stderr,
        )


def _log_instagram_container_diagnostics(
    error: Exception,
    credentials: CredentialStore,
) -> None:
    """Best-effort diagnostics for terminal Instagram container failures."""

    match = re.search(r"container\s+(\d+)", str(error), flags=re.IGNORECASE)
    if not match:
        return
    container_id = match.group(1)
    token = credentials.get("INSTAGRAM_ACCESS_TOKEN").strip()
    version = credentials.get("META_GRAPH_API_VERSION").strip()
    host = credentials.get("INSTAGRAM_API_HOST", "graph.facebook.com").strip()
    if not token or not version or host not in {"graph.facebook.com", "graph.instagram.com"}:
        return

    url = f"https://{host}/{version}/{container_id}"
    try:
        if host == "graph.instagram.com":
            response = requests.get(
                url,
                params={
                    "fields": "status_code,status",
                    "access_token": token,
                },
                timeout=30.0,
            )
        else:
            response = requests.get(
                url,
                params={"fields": "status_code,status"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        print(
            "[instagram] diagnostics warning: nao foi possivel consultar o container "
            f"{container_id} ({exc.__class__.__name__}).",
            file=sys.stderr,
        )
        return

    if not isinstance(payload, Mapping):
        return
    status_code = str(payload.get("status_code", "")).strip() or "desconhecido"
    status_detail = str(payload.get("status", "")).strip()
    if status_detail:
        print(
            f"[instagram] container {container_id} diagnostic: "
            f"status_code={status_code}; status={status_detail}",
            file=sys.stderr,
        )
    else:
        print(
            f"[instagram] container {container_id} diagnostic: "
            f"status_code={status_code}; status_sem_detalhe",
            file=sys.stderr,
        )


_INSTAGRAM_MAX_ATTEMPTS = 3


def _without_instagram_cover(context: PublishContext) -> PublishContext:
    metadata = dict(context.metadata)
    metadata.pop("cover_url", None)
    return PublishContext(
        episode=context.episode,
        video_path=context.video_path,
        cover_path=context.cover_path,
        metadata=metadata,
    )


def _is_retryable_instagram_processing_error(error: Exception) -> bool:
    return bool(
        re.search(
            r"InstagramPublisher:\s+container\s+\d+\s+terminou\s+com\s+status\s+ERROR\b",
            str(error),
            flags=re.IGNORECASE,
        )
    )


def _fraction_to_float(value: object) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            return float(numerator) / denominator_value
        return float(text)
    except (TypeError, ValueError):
        return None


def _log_instagram_video_preflight(video_path: Path) -> None:
    """Log Instagram-relevant MP4 properties without making ffprobe a hard dependency."""

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration,bit_rate:"
            "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,"
            "sample_rate,bit_rate"
        ),
        "-of",
        "json",
        str(video_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout)
    except FileNotFoundError:
        print(
            "[instagram] preflight warning: ffprobe nao encontrado; seguindo sem diagnostico.",
            file=sys.stderr,
        )
        return
    except subprocess.TimeoutExpired:
        print(
            "[instagram] preflight warning: ffprobe excedeu 30s; seguindo sem diagnostico.",
            file=sys.stderr,
        )
        return
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        print(
            "[instagram] preflight warning: nao foi possivel inspecionar o MP4 "
            f"({exc.__class__.__name__}); seguindo com a publicacao.",
            file=sys.stderr,
        )
        return

    if not isinstance(payload, Mapping):
        return
    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        streams = []
    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, Mapping) and stream.get("codec_type") == "video"
        ),
        {},
    )
    audio_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
        ),
        {},
    )
    format_info = payload.get("format", {})
    if not isinstance(format_info, Mapping):
        format_info = {}

    duration = _fraction_to_float(format_info.get("duration"))
    fps = _fraction_to_float(
        video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    )
    video_bitrate = _fraction_to_float(
        video_stream.get("bit_rate") or format_info.get("bit_rate")
    )
    video_codec = str(video_stream.get("codec_name", "")).strip().lower() or "desconhecido"
    audio_codec = str(audio_stream.get("codec_name", "")).strip().lower() or "sem_audio"
    sample_rate = _fraction_to_float(audio_stream.get("sample_rate"))
    width = video_stream.get("width", "?")
    height = video_stream.get("height", "?")

    details = [
        f"codec={video_codec}",
        f"resolution={width}x{height}",
        f"fps={fps:.2f}" if fps is not None else "fps=?",
        f"audio={audio_codec}",
        f"sample_rate={int(sample_rate)}Hz" if sample_rate is not None else "sample_rate=?",
        f"duration={duration:.2f}s" if duration is not None else "duration=?",
        (
            f"video_bitrate={video_bitrate / 1_000_000:.2f}Mbps"
            if video_bitrate is not None
            else "video_bitrate=?"
        ),
    ]
    print("[instagram] video preflight: " + "; ".join(details))

    warnings: list[str] = []
    if video_codec not in {"h264", "hevc", "h265"}:
        warnings.append(f"codec de video inesperado: {video_codec}")
    if fps is not None and not 23.0 <= fps <= 60.0:
        warnings.append(f"fps fora da faixa 23-60: {fps:.2f}")
    if audio_stream and audio_codec != "aac":
        warnings.append(f"codec de audio inesperado: {audio_codec}")
    if audio_stream and sample_rate is not None and int(sample_rate) != 48_000:
        warnings.append(f"sample rate diferente de 48000 Hz: {int(sample_rate)}")
    if duration is not None and duration < 3.0:
        warnings.append(f"duracao menor que 3s: {duration:.2f}s")
    if video_bitrate is not None and video_bitrate > 25_000_000:
        warnings.append(
            f"bitrate de video acima de 25 Mbps: {video_bitrate / 1_000_000:.2f} Mbps"
        )
    for warning in warnings:
        print(f"[instagram] preflight warning: {warning}", file=sys.stderr)


def _publish_instagram_with_retries(
    publisher: Publisher,
    context: PublishContext,
    credentials: CredentialStore,
):
    _log_instagram_video_preflight(context.video_path)
    attempt_context = context

    for attempt in range(1, _INSTAGRAM_MAX_ATTEMPTS + 1):
        using_cover = bool(str(attempt_context.metadata.get("cover_url", "")).strip())
        cover_mode = "cover_url" if using_cover else "thumb_offset"
        print(
            f"[instagram] attempt {attempt}/{_INSTAGRAM_MAX_ATTEMPTS}: "
            f"criando novo container ({cover_mode})."
        )
        try:
            uploaded = publisher.upload(attempt_context)
            return publisher.publish(attempt_context, uploaded)
        except ApiError as exc:
            _log_instagram_container_diagnostics(exc, credentials)
            if (
                not _is_retryable_instagram_processing_error(exc)
                or attempt >= _INSTAGRAM_MAX_ATTEMPTS
            ):
                raise

            if using_cover:
                attempt_context = _without_instagram_cover(attempt_context)
                print(
                    "[instagram] processing ERROR; retrying with a new container "
                    "without cover_url."
                )
            else:
                print(
                    "[instagram] processing ERROR; retrying with a new container "
                    "using thumb_offset."
                )

    raise ApiError("InstagramPublisher: retries esgotados sem resultado.")


def main(
    argv: Sequence[str] | None = None,
    default_project_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Valida ou publica um episodio pelas APIs oficiais. Dry-run e o padrao."
    )
    parser.add_argument("episode", help="Slug do episodio (ex.: duckworth).")
    parser.add_argument(
        "--platform",
        required=True,
        choices=[*PLATFORMS, "all"],
        help="Plataforma de destino.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Somente validar; nenhuma chamada externa (comportamento padrao).",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Autoriza explicitamente upload/publicacao real pelas APIs oficiais.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root or Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    dry_run = not args.live
    platforms = list(PLATFORMS) if args.platform == "all" else [args.platform]
    try:
        credentials = CredentialStore.load(project_root)
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    if dry_run:
        print("MODO SEGURO: DRY-RUN — nenhuma chamada de API sera feita.")
    else:
        print("MODO LIVE: upload/publicacao real autorizados por --live.")

    failed = False
    for platform in platforms:
        publisher = create_publisher(platform, credentials)
        hosted_cover_public_id: str | None = None
        try:
            context = build_context(project_root, args.episode, platform)
            if dry_run:
                result = publisher.dry_run(context)
                credential_warning = _credential_warning(publisher, context)
                if credential_warning:
                    print(credential_warning)
            else:
                publisher.validate(context, require_credentials=True)
                if platform == "instagram":
                    context, hosted_cover_public_id = _prepare_instagram_cover(
                        context,
                        credentials,
                    )
                    result = _publish_instagram_with_retries(
                        publisher,
                        context,
                        credentials,
                    )
                else:
                    uploaded = publisher.upload(context)
                    result = publisher.publish(context, uploaded)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
        except (PublishingError, RuntimeError, ApiError) as exc:
            failed = True
            if (
                platform == "instagram"
                and not dry_run
                and not _is_retryable_instagram_processing_error(exc)
            ):
                _log_instagram_container_diagnostics(exc, credentials)
            print(f"ERRO: {exc}", file=sys.stderr)
        finally:
            if hosted_cover_public_id:
                _cleanup_instagram_cover(hosted_cover_public_id, credentials)
    return 1 if failed else 0


def _credential_warning(publisher: Publisher, context: PublishContext) -> str:
    try:
        publisher.validate(context, require_credentials=True)
    except PublishingError as exc:
        if "credenciais nao configuradas" in str(exc):
            return str(exc)
        raise
    return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PublishingError(f"Arquivo obrigatorio ausente: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PublishingError(
            f"JSON invalido em {path} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise PublishingError(f"O arquivo {path} precisa conter um objeto JSON.")
    return data


if __name__ == "__main__":
    raise SystemExit(main(default_project_root=Path(__file__).resolve().parent))
