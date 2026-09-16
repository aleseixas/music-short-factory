from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from publishing.cover import generate_cover
from publishing.metadata import build_post_defaults, sync_selected_cover_timestamp

from .config import ProjectConfig
from .ffmpeg import probe_duration, probe_video_frame_count, run_ffmpeg
from .models import Episode


COVER_INTRO_SECONDS = 0.30


def validate_episode_cover_opening(episode: Episode) -> None:
    """Check the opt-in moving opening before synthesis or rendering."""
    if not (episode.directory / "post.json").is_file():
        return
    post = _load_cover_post(episode.directory)
    cover = post["cover"]
    enabled = cover.get("intro_enabled", True)
    if not isinstance(enabled, bool):
        raise RuntimeError("cover.intro_enabled precisa ser booleano.")
    automatic = cover["source"].get("selection") == "auto_first_shot"
    if automatic and enabled:
        raise RuntimeError("auto_first_shot exige cover.intro_enabled=false.")
    if not enabled:
        opening = episode.shots[0]
        if not episode.assets[opening.asset_id].is_video:
            raise RuntimeError("A abertura sem capa estatica exige video no primeiro take.")
        if opening.freeze_frame is not None:
            raise RuntimeError("O primeiro take da abertura em video nao pode ter freeze_frame.")


def embed_episode_cover_intro(
    project_root: Path,
    episode_dir: Path,
    video_path: Path,
    config: ProjectConfig,
    duration_seconds: float = COVER_INTRO_SECONDS,
) -> tuple[Path, Path, float]:
    """Generate the cover and, by default, prepend it to the rendered MP4.

    The intro is intentionally short and silent. The existing rendered video is
    appended after it as a complete A/V unit, so narration, music, SFX and all
    timeline-relative timings keep their original relationship.
    """

    post = _load_cover_post(episode_dir)
    cover_path = video_path.with_name(f"{episode_dir.name}_cover.jpg")
    generate_cover(project_root, episode_dir, post, cover_path)
    sync_selected_cover_timestamp(episode_dir, cover_path)

    if post["cover"].get("intro_enabled", True) is False:
        return video_path, cover_path, 0.0

    fps = config.render.fps
    intro_frames = max(1, round(duration_seconds * fps))
    intro_duration = intro_frames / fps
    prepend_cover_intro(
        video_path,
        cover_path,
        config,
        intro_frames=intro_frames,
    )
    return video_path, cover_path, intro_duration


def prepend_cover_intro(
    video_path: Path,
    cover_path: Path,
    config: ProjectConfig,
    *,
    intro_frames: int,
) -> Path:
    """Prepend an exact number of silent cover frames to an MP4 in-place."""

    if intro_frames <= 0:
        raise RuntimeError("A abertura de capa precisa ter ao menos 1 frame.")
    if not video_path.is_file():
        raise RuntimeError(f"Video final ausente para inserir capa: {video_path}")
    if not cover_path.is_file():
        raise RuntimeError(f"Capa ausente para inserir no video: {cover_path}")

    render = config.render
    mix = config.mix
    original_frames = probe_video_frame_count(video_path)
    if original_frames <= 0:
        raise RuntimeError(f"Video sem frames para inserir capa: {video_path}")

    fps = render.fps
    intro_duration = intro_frames / fps
    total_frames = original_frames + intro_frames
    total_duration = total_frames / fps
    partial = video_path.with_name(f"{video_path.stem}.cover.part{video_path.suffix}")
    partial.unlink(missing_ok=True)

    filter_graph = ";".join(
        [
            (
                f"[0:v]scale={render.width}:{render.height}:"
                "force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={render.width}:{render.height}:(iw-ow)/2:(ih-oh)/2,"
                f"setsar=1,fps={fps},trim=end_frame={intro_frames},"
                f"settb=AVTB,setpts=N/({fps}*TB),format=yuv420p[intro_v]"
            ),
            (
                f"[1:v]fps={fps},trim=end_frame={original_frames},setsar=1,"
                f"settb=AVTB,setpts=N/({fps}*TB),format=yuv420p[main_v]"
            ),
            "[intro_v][main_v]concat=n=2:v=1:a=0[out_v]",
            (
                f"[2:a]atrim=duration={intro_duration:.6f},asetpts=N/SR/TB,"
                f"aformat=sample_fmts=fltp:sample_rates={mix.sample_rate}:"
                "channel_layouts=stereo[silence]"
            ),
            (
                f"[1:a]aresample={mix.sample_rate},"
                f"aformat=sample_fmts=fltp:sample_rates={mix.sample_rate}:"
                "channel_layouts=stereo,asetpts=N/SR/TB[main_a]"
            ),
            (
                "[silence][main_a]concat=n=2:v=0:a=1,"
                f"apad=whole_dur={total_duration:.6f},"
                f"atrim=duration={total_duration:.6f},asetpts=N/SR/TB[out_a]"
            ),
        ]
    )

    arguments: list[object] = [
        "-y",
        "-hide_banner",
        "-loop",
        "1",
        "-framerate",
        fps,
        "-i",
        cover_path,
        "-i",
        video_path,
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={mix.sample_rate}:cl=stereo",
        "-filter_complex",
        filter_graph,
        "-map",
        "[out_v]",
        "-map",
        "[out_a]",
        "-frames:v",
        total_frames,
        "-r",
        fps,
        "-c:v",
        "libx264",
        "-preset",
        render.preset,
        "-crf",
        render.crf,
        "-x264-params",
        "colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-c:a",
        "aac",
        "-b:a",
        mix.bitrate,
        "-ar",
        mix.sample_rate,
        "-movflags",
        "+faststart",
        partial,
    ]

    try:
        run_ffmpeg(arguments)
        rendered_frames = probe_video_frame_count(partial)
        if rendered_frames != total_frames:
            raise RuntimeError(
                f"Video com capa gerou {rendered_frames} frames; esperado: {total_frames}."
            )
        rendered_duration = probe_duration(partial)
        tolerance = 2 / fps + 0.02
        if abs(rendered_duration - total_duration) > tolerance:
            raise RuntimeError(
                f"Duracao do video com capa invalida: {rendered_duration:.3f}s; "
                f"esperado: {total_duration:.3f}s."
            )
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    partial.replace(video_path)
    return video_path


def _load_cover_post(episode_dir: Path) -> Mapping[str, Any]:
    post_path = episode_dir / "post.json"
    try:
        data = json.loads(post_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return _default_cover_post(episode_dir)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"JSON invalido em {post_path} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc

    if not isinstance(data, Mapping):
        return _default_cover_post(episode_dir)
    cover = data.get("cover")
    if not isinstance(cover, Mapping):
        return _default_cover_post(episode_dir)
    if not str(cover.get("headline", "")).strip():
        return _default_cover_post(episode_dir)
    if not isinstance(cover.get("source"), Mapping):
        return _default_cover_post(episode_dir)
    return data


def _default_cover_post(episode_dir: Path) -> Mapping[str, Any]:
    story = _load_json(episode_dir / "story.json")
    timeline = _load_json(episode_dir / "timeline.json")
    shots = timeline.get("shots")
    if not isinstance(shots, list) or not shots or not isinstance(shots[0], Mapping):
        raise RuntimeError(
            f"Nao foi possivel criar a capa padrao: timeline sem primeiro shot em {episode_dir}."
        )
    first_asset = str(shots[0].get("asset", "")).strip()
    if not first_asset:
        raise RuntimeError(
            f"Nao foi possivel criar a capa padrao: primeiro shot sem asset em {episode_dir}."
        )
    return build_post_defaults(story, first_asset)


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Arquivo obrigatorio ausente para gerar capa: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"JSON invalido em {path} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc
    if not isinstance(data, Mapping):
        raise RuntimeError(f"O arquivo {path} precisa conter um objeto JSON.")
    return data
