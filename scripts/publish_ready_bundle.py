from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from PIL import Image, UnidentifiedImageError


SCHEMA_VERSION = 1
MAX_VIDEO_BYTES = 4_000_000_000
MAX_COVER_BYTES = 2 * 1024 * 1024
MIN_PLATFORM_DURATION_SECONDS = 3.0
MIN_PLATFORM_FPS = 23.0
MAX_PLATFORM_FPS = 60.0
MAX_PLATFORM_VIDEO_BITRATE = 25_000_000
PLATFORM_AUDIO_SAMPLE_RATE = 48_000
SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*")
RUN_ID_PATTERN = re.compile(r"[1-9][0-9]*")
SHA_PATTERN = re.compile(r"[0-9a-fA-F]{7,64}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class BundleError(RuntimeError):
    """A publish-ready bundle failed validation."""


@dataclass(frozen=True)
class ProjectSettings:
    episodes_dir: Path
    output_dir: Path
    width: int
    height: int
    fps: int
    sample_rate: int


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise BundleError(f"{label} ausente: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"{label} invalido em {path}: {exc}") from exc


def _json_object(path: Path, label: str) -> dict[str, Any]:
    value = _read_json(path, label)
    if not isinstance(value, dict):
        raise BundleError(f"{label} precisa conter um objeto JSON: {path}")
    return value


def _safe_config_directory(raw: object, label: str) -> Path:
    text = str(raw or "").strip()
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts or path == Path("."):
        raise BundleError(f"Caminho inseguro em {label}: {text!r}")
    return path


def _positive_int(raw: object, label: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BundleError(f"Valor invalido em {label}: {raw!r}") from exc
    if value <= 0:
        raise BundleError(f"{label} precisa ser positivo; recebido: {value}")
    return value


def _load_settings(project_root: Path) -> ProjectSettings:
    raw = _json_object(project_root / "config" / "config.json", "config/config.json")
    paths = raw.get("paths", {})
    render = raw.get("render", {})
    mix = raw.get("mix", {})
    if not isinstance(paths, Mapping) or not isinstance(render, Mapping) or not isinstance(mix, Mapping):
        raise BundleError("config/config.json possui secoes paths/render/mix invalidas.")
    return ProjectSettings(
        episodes_dir=_safe_config_directory(paths.get("episodes_dir", "episodes"), "paths.episodes_dir"),
        output_dir=_safe_config_directory(paths.get("output_dir", "output"), "paths.output_dir"),
        width=_positive_int(render.get("width", 720), "render.width"),
        height=_positive_int(render.get("height", 1280), "render.height"),
        fps=_positive_int(render.get("fps", 30), "render.fps"),
        sample_rate=_positive_int(mix.get("sample_rate", 48000), "mix.sample_rate"),
    )


def _validate_slug(episode: str) -> str:
    episode = episode.strip()
    if not SLUG_PATTERN.fullmatch(episode):
        raise BundleError(
            f"Slug de episodio invalido: {episode!r}. "
            "Use apenas letras minusculas, numeros, '_' ou '-'."
        )
    return episode


def _validate_run_id(run_id: str | None) -> str:
    value = str(run_id or "").strip()
    if not RUN_ID_PATTERN.fullmatch(value):
        raise BundleError(
            "source run id ausente ou invalido; informe um inteiro positivo "
            "(no GitHub Actions, use GITHUB_RUN_ID)."
        )
    return value


def _validate_source_sha(source_sha: str | None) -> str:
    value = str(source_sha or "").strip().lower()
    if not SHA_PATTERN.fullmatch(value):
        raise BundleError(
            "source SHA ausente ou invalido; informe de 7 a 64 caracteres hexadecimais "
            "(no GitHub Actions, use GITHUB_SHA)."
        )
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BundleError(f"Nao foi possivel ler {path} para calcular SHA-256: {exc}") from exc
    return digest.hexdigest()


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise BundleError(f"{label} nao pode ser link simbolico: {path}")
    if not path.is_file():
        raise BundleError(f"{label} ausente ou nao e arquivo regular: {path}")


def _find_binary(name: str, environment_name: str) -> str:
    configured = os.environ.get(environment_name, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            raise BundleError(f"{environment_name} nao aponta para um executavel: {candidate}")
        return str(candidate.resolve())
    discovered = shutil.which(name)
    if not discovered:
        raise BundleError(
            f"{name} nao encontrado. Instale FFmpeg/ffprobe ou configure {environment_name}."
        )
    return discovered


def _run_ffprobe(path: Path, executable: str) -> dict[str, Any]:
    command = [
        executable,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BundleError(f"ffprobe nao conseguiu analisar {path}: {exc}") from exc
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-20:])
        raise BundleError(f"ffprobe rejeitou {path} (codigo {result.returncode}):\n{tail}")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BundleError(f"ffprobe retornou JSON invalido para {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BundleError(f"ffprobe retornou payload invalido para {path}.")
    return raw


def _full_decode(path: Path, executable: str) -> None:
    command = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-xerror",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60 * 60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BundleError(f"FFmpeg nao conseguiu decodificar integralmente {path}: {exc}") from exc
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-30:])
        raise BundleError(
            f"Video corrompido ou nao decodificavel por completo: {path} "
            f"(FFmpeg codigo {result.returncode}):\n{tail}"
        )


def _positive_float(raw: object, label: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise BundleError(f"{label} invalido no ffprobe: {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise BundleError(f"{label} precisa ser finito e positivo; recebido: {raw!r}")
    return value


def _frame_rate(stream: Mapping[str, Any]) -> tuple[float, str]:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = str(stream.get(key) or "").strip()
        if not raw or raw == "0/0":
            continue
        try:
            fraction = Fraction(raw)
            value = float(fraction)
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(value) and value > 0:
            return value, str(fraction)
    raise BundleError("FPS invalido ou ausente no stream de video.")


def _validate_video(path: Path, settings: ProjectSettings) -> dict[str, Any]:
    _require_regular_file(path, "Video renderizado")
    size = path.stat().st_size
    if size <= 0:
        raise BundleError(f"Video renderizado esta vazio: {path}")
    if size > MAX_VIDEO_BYTES:
        raise BundleError(
            f"Video renderizado excede o limite de 4 GB: {size} bytes > {MAX_VIDEO_BYTES}."
        )

    ffprobe = _find_binary("ffprobe", "FFPROBE_BINARY")
    ffmpeg = _find_binary("ffmpeg", "FFMPEG_BINARY")
    raw = _run_ffprobe(path, ffprobe)
    format_data = raw.get("format")
    streams = raw.get("streams")
    if not isinstance(format_data, Mapping) or not isinstance(streams, list):
        raise BundleError("ffprobe nao retornou format/streams validos para o MP4 final.")

    format_names = {
        value.strip().casefold()
        for value in str(format_data.get("format_name") or "").split(",")
        if value.strip()
    }
    if "mp4" not in format_names:
        raise BundleError(
            "Container final precisa ser MP4; ffprobe informou "
            f"{format_data.get('format_name')!r}."
        )

    typed_streams = [stream for stream in streams if isinstance(stream, Mapping)]
    video_streams = [stream for stream in typed_streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in typed_streams if stream.get("codec_type") == "audio"]
    other_streams = [
        stream for stream in typed_streams if stream.get("codec_type") not in {"video", "audio"}
    ]
    if len(video_streams) != 1 or len(audio_streams) != 1 or other_streams:
        other_types = [str(stream.get("codec_type") or "desconhecido") for stream in other_streams]
        raise BundleError(
            "MP4 final precisa conter exatamente 1 stream de video e 1 de audio, "
            "sem streams extras; encontrado: "
            f"video={len(video_streams)}, audio={len(audio_streams)}, extras={other_types}."
        )

    video = video_streams[0]
    audio = audio_streams[0]
    codec = str(video.get("codec_name") or "").casefold()
    pixel_format = str(video.get("pix_fmt") or "").casefold()
    if codec != "h264":
        raise BundleError(f"Codec de video final precisa ser H.264; encontrado: {codec or '<ausente>'}.")
    if pixel_format != "yuv420p":
        raise BundleError(
            f"Pixel format final precisa ser yuv420p; encontrado: {pixel_format or '<ausente>'}."
        )
    width = _positive_int(video.get("width"), "stream.video.width")
    height = _positive_int(video.get("height"), "stream.video.height")
    if (width, height) != (settings.width, settings.height):
        raise BundleError(
            f"Resolucao final divergente: {width}x{height}; "
            f"config exige {settings.width}x{settings.height}."
        )
    fps, fps_ratio = _frame_rate(video)
    if not math.isclose(fps, float(settings.fps), rel_tol=0.0, abs_tol=0.01):
        raise BundleError(f"FPS final divergente: {fps:.6g}; config exige {settings.fps}.")
    if not MIN_PLATFORM_FPS <= fps <= MAX_PLATFORM_FPS:
        raise BundleError(
            f"FPS final incompativel com publicacao: {fps:.6g}; "
            f"faixa aceita pelo gate: {MIN_PLATFORM_FPS:.0f}-{MAX_PLATFORM_FPS:.0f}."
        )

    audio_codec = str(audio.get("codec_name") or "").casefold()
    if audio_codec != "aac":
        raise BundleError(
            f"Codec de audio final precisa ser AAC; encontrado: {audio_codec or '<ausente>'}."
        )
    sample_rate = _positive_int(audio.get("sample_rate"), "stream.audio.sample_rate")
    if sample_rate != settings.sample_rate:
        raise BundleError(
            f"Sample rate final divergente: {sample_rate} Hz; "
            f"config exige {settings.sample_rate} Hz."
        )
    if sample_rate != PLATFORM_AUDIO_SAMPLE_RATE:
        raise BundleError(
            f"Sample rate final incompativel com publicacao: {sample_rate} Hz; "
            f"o gate exige {PLATFORM_AUDIO_SAMPLE_RATE} Hz."
        )

    duration = _positive_float(format_data.get("duration"), "format.duration")
    if duration < MIN_PLATFORM_DURATION_SECONDS:
        raise BundleError(
            f"Duracao final incompativel com publicacao: {duration:.6f}s; "
            f"o minimo do gate e {MIN_PLATFORM_DURATION_SECONDS:.1f}s."
        )
    video_duration = _positive_float(video.get("duration"), "stream.video.duration")
    audio_duration = _positive_float(audio.get("duration"), "stream.audio.duration")
    duration_tolerance = max(0.25, 2.0 / settings.fps)
    for label, stream_duration in (("video", video_duration), ("audio", audio_duration)):
        if abs(stream_duration - duration) > duration_tolerance:
            raise BundleError(
                f"Duracao do stream de {label} ({stream_duration:.6f}s) diverge do "
                f"container ({duration:.6f}s) em mais de {duration_tolerance:.3f}s."
            )

    raw_bitrate = video.get("bit_rate") or format_data.get("bit_rate")
    video_bitrate = (
        _positive_float(raw_bitrate, "stream.video.bit_rate")
        if raw_bitrate not in (None, "", "N/A")
        else size * 8.0 / duration
    )
    if video_bitrate > MAX_PLATFORM_VIDEO_BITRATE:
        raise BundleError(
            "Bitrate de video incompativel com publicacao: "
            f"{video_bitrate / 1_000_000:.2f} Mbps; maximo do gate: "
            f"{MAX_PLATFORM_VIDEO_BITRATE / 1_000_000:.0f} Mbps."
        )

    _full_decode(path, ffmpeg)
    return {
        "container": "mp4",
        "duration_seconds": duration,
        "video": {
            "codec": codec,
            "pixel_format": pixel_format,
            "width": width,
            "height": height,
            "fps": fps,
            "frame_rate": fps_ratio,
            "bit_rate": round(video_bitrate),
            "duration_seconds": video_duration,
        },
        "audio": {
            "codec": audio_codec,
            "sample_rate": sample_rate,
            "channels": _positive_int(audio.get("channels"), "stream.audio.channels"),
            "duration_seconds": audio_duration,
        },
    }


def _validate_cover(path: Path, settings: ProjectSettings) -> dict[str, Any]:
    _require_regular_file(path, "Capa JPEG")
    size = path.stat().st_size
    if size <= 0:
        raise BundleError(f"Capa JPEG esta vazia: {path}")
    if size > MAX_COVER_BYTES:
        raise BundleError(
            f"Capa excede o limite de 2 MiB: {size} bytes > {MAX_COVER_BYTES}."
        )
    try:
        with Image.open(path) as opened:
            image_format = str(opened.format or "").upper()
            dimensions = opened.size
            opened.verify()
        with Image.open(path) as opened:
            opened.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise BundleError(f"Capa JPEG corrompida ou invalida: {path}: {exc}") from exc
    if image_format != "JPEG":
        raise BundleError(f"Capa precisa ser JPEG; formato detectado: {image_format or '<ausente>'}.")
    if dimensions != (settings.width, settings.height):
        raise BundleError(
            f"Resolucao da capa divergente: {dimensions[0]}x{dimensions[1]}; "
            f"config exige {settings.width}x{settings.height}."
        )
    return {
        "format": "jpeg",
        "width": dimensions[0],
        "height": dimensions[1],
    }


def _bundle_paths(episode: str) -> dict[str, PurePosixPath]:
    return {
        "video": PurePosixPath("output") / f"{episode}.mp4",
        "cover": PurePosixPath("output") / f"{episode}_cover.jpg",
        "post": PurePosixPath("episodes") / episode / "post.json",
        "visual_usage": PurePosixPath("episodes") / episode / "visual_usage.json",
    }


def _copy_exact(source: Path, destination: Path, label: str) -> None:
    _require_regular_file(source, label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, destination)
    except OSError as exc:
        raise BundleError(f"Falha ao copiar {label} de {source} para {destination}: {exc}") from exc
    if destination.stat().st_size != source.stat().st_size or _sha256(destination) != _sha256(source):
        raise BundleError(f"Copia de {label} nao preservou os bytes: {destination}")


def _file_manifest(path: Path, relative: PurePosixPath, role: str) -> dict[str, Any]:
    return {
        "path": relative.as_posix(),
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def create_bundle(
    project_root: Path,
    episode: str,
    bundle_dir: Path,
    *,
    source_run_id: str | None = None,
    source_sha: str | None = None,
) -> Path:
    project_root = project_root.resolve()
    episode = _validate_slug(episode)
    run_id = _validate_run_id(
        source_run_id
        or os.environ.get("PREFLIGHT_SOURCE_RUN_ID")
        or os.environ.get("GITHUB_RUN_ID")
    )
    sha = _validate_source_sha(
        source_sha
        or os.environ.get("PREFLIGHT_SOURCE_SHA")
        or os.environ.get("GITHUB_SHA")
    )
    settings = _load_settings(project_root)
    target = bundle_dir if bundle_dir.is_absolute() else project_root / bundle_dir
    target = target.resolve()
    if target == project_root:
        raise BundleError("O bundle nao pode substituir a raiz do projeto.")
    if target.exists() or target.is_symlink():
        raise BundleError(f"Diretorio de bundle ja existe; remova-o explicitamente: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    source_episode = project_root / settings.episodes_dir / episode
    source_output = project_root / settings.output_dir
    sources = {
        "video": source_output / f"{episode}.mp4",
        "cover": source_output / f"{episode}_cover.jpg",
        "post": source_episode / "post.json",
        "visual_usage": source_episode / "visual_usage.json",
    }
    for role in ("video", "cover", "post"):
        _require_regular_file(sources[role], f"Arquivo obrigatorio ({role})")
    _json_object(sources["post"], "post.json")
    if sources["visual_usage"].exists() or sources["visual_usage"].is_symlink():
        _require_regular_file(sources["visual_usage"], "visual_usage.json")
        usage = _json_object(sources["visual_usage"], "visual_usage.json")
        usage_episode = usage.get("episode")
        if usage_episode is not None and usage_episode != episode:
            raise BundleError(
                f"visual_usage.json pertence a {usage_episode!r}, nao ao episodio {episode!r}."
            )

    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent)))
    paths = _bundle_paths(episode)
    included_roles = ["video", "cover", "post"]
    if sources["visual_usage"].is_file():
        included_roles.append("visual_usage")
    try:
        for role in included_roles:
            _copy_exact(sources[role], staging / Path(paths[role].as_posix()), role)

        staged_video = staging / Path(paths["video"].as_posix())
        staged_cover = staging / Path(paths["cover"].as_posix())
        video_info = _validate_video(staged_video, settings)
        cover_info = _validate_cover(staged_cover, settings)
        _json_object(staging / Path(paths["post"].as_posix()), "post.json empacotado")

        files = [
            _file_manifest(
                staging / Path(paths[role].as_posix()),
                paths[role],
                role,
            )
            for role in included_roles
        ]
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "episode": episode,
            "source": {"run_id": run_id, "sha": sha},
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "files": files,
            "media": {**video_info, "cover": cover_info},
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        staging.replace(target)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    print(
        f"PUBLISH_READY_BUNDLE=CREATED episode={episode} "
        f"source_run_id={run_id} files={len(included_roles)} path={target}"
    )
    return target


def _manifest_file_map(
    manifest: Mapping[str, Any], episode: str
) -> dict[str, Mapping[str, Any]]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise BundleError("manifest.json nao contem uma lista files valida.")
    expected_paths = _bundle_paths(episode)
    required_roles = {"video", "cover", "post"}
    allowed_roles = required_roles | {"visual_usage"}
    result: dict[str, Mapping[str, Any]] = {}
    seen_paths: set[str] = set()
    for index, raw in enumerate(raw_files):
        if not isinstance(raw, Mapping):
            raise BundleError(f"manifest.files[{index}] precisa ser um objeto.")
        role = str(raw.get("role") or "")
        raw_path = str(raw.get("path") or "")
        if role not in allowed_roles:
            raise BundleError(f"Role inesperado em manifest.files[{index}]: {role!r}.")
        expected = expected_paths[role].as_posix()
        path = PurePosixPath(raw_path)
        if (
            not raw_path
            or "\\" in raw_path
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != expected
        ):
            raise BundleError(
                f"Caminho inseguro ou divergente para role {role}: {raw_path!r}; "
                f"esperado: {expected!r}."
            )
        if role in result or raw_path in seen_paths:
            raise BundleError(f"Entrada duplicada no manifest para role/path {role!r}/{raw_path!r}.")
        try:
            size = int(raw.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise BundleError(f"size_bytes invalido para {raw_path!r}.") from exc
        digest = str(raw.get("sha256") or "")
        if size <= 0 or not SHA256_PATTERN.fullmatch(digest):
            raise BundleError(f"Tamanho ou SHA-256 invalido para {raw_path!r}.")
        result[role] = raw
        seen_paths.add(raw_path)
    missing = required_roles - result.keys()
    if missing:
        raise BundleError(f"Manifest nao contem arquivos obrigatorios: {', '.join(sorted(missing))}.")
    return result


def _scan_bundle_tree(bundle: Path) -> tuple[set[str], set[str]]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise BundleError(f"Bundle ausente, invalido ou simbolico: {bundle}")
    files: set[str] = set()
    directories: set[str] = set()
    for current, dir_names, file_names in os.walk(bundle, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in dir_names:
            child = current_path / name
            relative = child.relative_to(bundle).as_posix()
            if child.is_symlink():
                raise BundleError(f"Bundle contem diretorio simbolico proibido: {relative}")
            directories.add(relative)
        for name in file_names:
            child = current_path / name
            relative = child.relative_to(bundle).as_posix()
            if child.is_symlink() or not child.is_file():
                raise BundleError(f"Bundle contem arquivo nao regular ou simbolico: {relative}")
            files.add(relative)
    return files, directories


def _validate_manifest_header(
    manifest: Mapping[str, Any], episode: str, source_run_id: str
) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BundleError(
            f"Versao de manifest incompatível: {manifest.get('schema_version')!r}; "
            f"esperada: {SCHEMA_VERSION}."
        )
    if manifest.get("episode") != episode:
        raise BundleError(
            f"Bundle pertence ao episodio {manifest.get('episode')!r}, nao a {episode!r}."
        )
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise BundleError("manifest.json nao contem source valido.")
    manifest_run_id = _validate_run_id(str(source.get("run_id") or ""))
    if manifest_run_id != source_run_id:
        raise BundleError(
            f"Bundle pertence ao source run {manifest_run_id}, nao ao run solicitado {source_run_id}."
        )
    _validate_source_sha(str(source.get("sha") or ""))
    media = manifest.get("media")
    if not isinstance(media, Mapping):
        raise BundleError("manifest.json nao contem metadados media validos.")


def _verify_bundle_files(
    bundle: Path,
    episode: str,
    manifest_files: Mapping[str, Mapping[str, Any]],
) -> None:
    actual_files, actual_directories = _scan_bundle_tree(bundle)
    expected_files = {"manifest.json"} | {
        str(entry["path"]) for entry in manifest_files.values()
    }
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise BundleError(
            f"Conteudo do bundle diverge do manifest; ausentes={missing}, extras={extra}."
        )
    expected_directories = {"output", "episodes", f"episodes/{episode}"}
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        extra = sorted(actual_directories - expected_directories)
        raise BundleError(
            f"Arvore de diretorios do bundle e inesperada; ausentes={missing}, extras={extra}."
        )
    for role, entry in manifest_files.items():
        path = bundle / Path(str(entry["path"]))
        _require_regular_file(path, f"Arquivo do bundle ({role})")
        actual_size = path.stat().st_size
        expected_size = int(entry["size_bytes"])
        if actual_size != expected_size:
            raise BundleError(
                f"Tamanho divergente em {entry['path']}: {actual_size}; esperado {expected_size}."
            )
        actual_hash = _sha256(path)
        if actual_hash != entry["sha256"]:
            raise BundleError(
                f"SHA-256 divergente em {entry['path']}: {actual_hash}; "
                f"esperado {entry['sha256']}."
            )


def _destination_for_role(
    project_root: Path, settings: ProjectSettings, episode: str, role: str
) -> Path:
    if role == "video":
        return project_root / settings.output_dir / f"{episode}.mp4"
    if role == "cover":
        return project_root / settings.output_dir / f"{episode}_cover.jpg"
    if role == "post":
        return project_root / settings.episodes_dir / episode / "post.json"
    if role == "visual_usage":
        return project_root / settings.episodes_dir / episode / "visual_usage.json"
    raise BundleError(f"Role de destino desconhecido: {role!r}")


def _assert_safe_destination(project_root: Path, destination: Path) -> None:
    try:
        destination.resolve(strict=False).relative_to(project_root)
    except ValueError as exc:
        raise BundleError(f"Destino sairia da raiz do projeto: {destination}") from exc
    current = destination.parent
    while current != project_root:
        if current.is_symlink():
            raise BundleError(f"Destino passa por diretorio simbolico proibido: {current}")
        if current.parent == current:
            raise BundleError(f"Nao foi possivel validar o destino: {destination}")
        current = current.parent
    if destination.is_symlink():
        raise BundleError(f"Destino existente nao pode ser link simbolico: {destination}")


def restore_bundle(
    project_root: Path,
    episode: str,
    bundle_dir: Path,
    *,
    source_run_id: str,
) -> list[Path]:
    project_root = project_root.resolve()
    episode = _validate_slug(episode)
    requested_run_id = _validate_run_id(source_run_id)
    settings = _load_settings(project_root)
    bundle = bundle_dir if bundle_dir.is_absolute() else project_root / bundle_dir
    bundle = bundle.absolute()
    if bundle.is_symlink():
        raise BundleError(f"Bundle nao pode ser link simbolico: {bundle}")

    manifest_path = bundle / "manifest.json"
    _require_regular_file(manifest_path, "manifest.json")
    manifest = _json_object(manifest_path, "manifest.json")
    _validate_manifest_header(manifest, episode, requested_run_id)
    manifest_files = _manifest_file_map(manifest, episode)
    _verify_bundle_files(bundle, episode, manifest_files)

    staged: list[tuple[Path, Path, Mapping[str, Any]]] = []
    try:
        for role, entry in manifest_files.items():
            source = bundle / Path(str(entry["path"]))
            destination = _destination_for_role(project_root, settings, episode, role)
            _assert_safe_destination(project_root, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.restore-", dir=destination.parent
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                _copy_exact(source, temporary, f"bundle {role}")
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            staged.append((temporary, destination, entry))

        restored: list[Path] = []
        for temporary, destination, entry in staged:
            try:
                temporary.replace(destination)
            except OSError as exc:
                raise BundleError(f"Falha ao restaurar {destination}: {exc}") from exc
            if (
                destination.stat().st_size != int(entry["size_bytes"])
                or _sha256(destination) != entry["sha256"]
            ):
                raise BundleError(f"Bytes restaurados divergem do manifest: {destination}")
            restored.append(destination)
        staged.clear()
    finally:
        for temporary, _, _ in staged:
            temporary.unlink(missing_ok=True)

    print(
        f"PUBLISH_READY_BUNDLE=RESTORED episode={episode} "
        f"source_run_id={requested_run_id} files={len(restored)} path={bundle}"
    )
    return restored


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cria ou restaura um bundle de episodio validado para publicacao."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Valida e empacota video/capa/post.")
    create.add_argument("episode", help="Slug do episodio.")
    create.add_argument("--bundle-dir", type=Path, default=Path(".publish-ready"))
    create.add_argument("--source-run-id", default=None, help=argparse.SUPPRESS)
    create.add_argument("--source-sha", default=None, help=argparse.SUPPRESS)

    restore = subparsers.add_parser("restore", help="Verifica e restaura um bundle.")
    restore.add_argument("episode", help="Slug do episodio.")
    restore.add_argument("--bundle-dir", type=Path, default=Path(".publish-ready"))
    restore.add_argument("--source-run-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            create_bundle(
                args.project_root,
                args.episode,
                args.bundle_dir,
                source_run_id=args.source_run_id,
                source_sha=args.source_sha,
            )
        else:
            restore_bundle(
                args.project_root,
                args.episode,
                args.bundle_dir,
                source_run_id=args.source_run_id,
            )
    except (BundleError, OSError) as exc:
        print(f"PUBLISH_READY_BUNDLE_ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
