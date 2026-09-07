from __future__ import annotations

import shutil
from pathlib import Path

from .assets import AssetManager
from .audio import resolve_audio, validate_audio_duration
from .captions import create_highlight_overlay, write_ass_captions
from .config import load_project_config, load_style_config
from .cover_intro import embed_episode_cover_intro
from .episode import load_episode
from .editorial import load_editorial_catalogs, validate_editorial_direction
from .ffmpeg import preflight
from .music import resolve_background_music
from .renderer import Renderer
from .sfx import resolve_sfx_cues
from .text_fx import write_text_fx_ass
from .timeline import build_timeline, resolve_text_fx_cues, write_timeline_plan
from .utils import safe_child


async def build_video(project_root: Path, episode_name: str) -> Path:
    project_root = project_root.resolve()
    config = load_project_config(project_root / "config" / "config.json")
    style = load_style_config(project_root / "config" / "style.json")
    episode = load_episode(project_root, config.paths.episodes_dir, episode_name)
    work_root = _project_directory(project_root, config.paths.work_dir, "work")
    output_dir = _project_directory(project_root, config.paths.output_dir, "output")
    cache_root = _project_directory(project_root, config.paths.cache_dir, "cache")
    _assert_disjoint_roots(
        {
            "episodes": episode.directory.parent.resolve(),
            "work": work_root,
            "output": output_dir,
            "cache": cache_root,
        }
    )
    video_cache_dir = _safe_project_child(
        project_root,
        cache_root / "video",
        episode.name,
        "cache de video",
    )
    background_music = resolve_background_music(
        project_root,
        episode.background_music,
        episode.name,
        cache_root,
    )
    sfx_cues = resolve_sfx_cues(
        project_root,
        episode.sfx_cues,
        episode.name,
        cache_root,
    )
    work_dir = _reset_episode_work_dir(project_root, work_root, episode.name)
    audio_cache_dir = _safe_project_child(
        project_root,
        cache_root / "audio",
        episode.name,
        "cache de audio",
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print(f"MUSIC SHORT FACTORY | {episode.story.title}")
    print("=" * 64)
    has_video_assets = any(
        episode.assets[shot.asset_id].is_video
        for shot in getattr(episode, "shots", ())
    )
    preflight(
        require_background_music=background_music is not None,
        require_sfx=bool(sfx_cues),
        require_video_assets=has_video_assets,
    )
    if background_music is not None:
        selected = background_music.path.relative_to(project_root).as_posix()
        print(
            f"[musica] profile={background_music.profile} | "
            f"arquivo={selected} | volume={background_music.volume:.3f}"
        )
    for cue in sfx_cues:
        selected = cue.path.relative_to(project_root).as_posix()
        print(
            f"[sfx] cue={cue.index} | type={cue.type} | "
            f"tempo={cue.time_seconds:.3f}s | arquivo={selected} | "
            f"volume={cue.volume:.3f}"
        )

    overlay_cues = getattr(episode, "overlay_cues", ())
    asset_manager = None
    prepared_overlays = ()
    if overlay_cues:
        asset_manager = AssetManager(
            assets_dir=episode.assets_dir,
            work_dir=work_dir,
            width=config.render.width,
            height=config.render.height,
            scale=config.render.working_scale,
            allowed_assets_root=episode.directory,
            video_cache_dir=video_cache_dir,
        )
        # Overlay files are local-only and validated before TTS or scene rendering.
        prepared_overlays = tuple(
            (cue, asset_manager.prepare_overlay(episode.assets[cue.asset_id], cue.scale))
            for cue in overlay_cues
        )

    if asset_manager is None:
        asset_manager = AssetManager(
            assets_dir=episode.assets_dir,
            work_dir=work_dir,
            width=config.render.width,
            height=config.render.height,
            scale=config.render.working_scale,
            allowed_assets_root=episode.directory,
            video_cache_dir=video_cache_dir,
        )
    if has_video_assets:
        video_assets = tuple(
            episode.assets[asset_id]
            for asset_id in dict.fromkeys(
                shot.asset_id for shot in episode.shots
                if episode.assets[shot.asset_id].is_video
            )
        )
        print(f"[video] validando {len(video_assets)} fonte(s) com ffprobe...")
        asset_manager.ensure_all(video_assets)

    audio = await resolve_audio(
        episode.story.narration,
        config.tts,
        episode.directory,
        audio_cache_dir,
        segments=episode.story.segments,
    )
    duration_warning = validate_audio_duration(
        audio.duration,
        episode.story.target_duration_seconds,
        config.duration.target_tolerance_seconds,
    )
    if duration_warning:
        print(f"[voz] aviso: {duration_warning}")
    timing_label = "exatos" if audio.exact_timings else "estimados"
    print(
        f"[voz] fonte={audio.source} | duracao={audio.duration:.2f}s | "
        f"alvo={episode.story.target_duration_seconds:.2f}s | timestamps={timing_label}"
    )

    plan = build_timeline(
        story=episode.story,
        shots=episode.shots,
        assets=episode.assets,
        words=audio.words,
        audio_duration=audio.duration,
        fps=config.render.fps,
        crossfade_seconds=style.transitions.crossfade_seconds,
        visual_fx_cues=episode.visual_fx_cues,
    )
    resolved_text_fx_cues = resolve_text_fx_cues(episode.text_fx_cues, plan)
    editorial_catalogs = load_editorial_catalogs(
        project_root,
        require_music=episode.background_music is not None,
        require_sfx=bool(episode.sfx_cues),
    )
    editorial_report = validate_editorial_direction(
        episode,
        plan,
        editorial_catalogs,
        highlight_default_duration=style.highlights.default_duration,
        resolved_text_fx_cues=resolved_text_fx_cues,
    )
    for warning in editorial_report.warnings:
        print(f"[direcao] aviso {warning.code}: {warning.message}")

    used_assets = tuple(
        episode.assets[asset_id]
        for asset_id in dict.fromkeys(shot.asset_id for shot in episode.shots)
    )
    print(f"[assets] validando {len(used_assets)} arquivo(s) antes do render...")
    asset_manager.ensure_all(used_assets)
    for scene in plan.scenes:
        info = asset_manager.preflight_video_scene(scene, config.render.fps)
        if info is not None:
            source_start = scene.shot.source_start_seconds
            source_duration = scene.required_source_duration(config.render.fps)
            source_end = source_start + source_duration
            freeze_note = ""
            if scene.freeze_frame is not None:
                freeze_note = (
                    f" | freeze={scene.freeze_frame.start_frame / config.render.fps:.3f}s"
                    f"+{scene.freeze_frame.duration_frames / config.render.fps:.3f}s"
                )
            print(
                f"[video] shot={scene.shot.id} | asset={scene.asset.id} | "
                f"fonte={source_start:.3f}s-{source_end:.3f}s | "
                f"speed={scene.shot.speed:.3f}x | "
                f"original={info.width}x{info.height}@{info.fps:.3f}fps"
                f"{freeze_note}"
            )

    write_timeline_plan(plan, work_dir / "timeline.resolved.json")
    captions_path = work_dir / "captions.ass"
    write_ass_captions(
        words=audio.words,
        path=captions_path,
        width=config.render.width,
        height=config.render.height,
        style=style.captions,
    )
    text_fx_path = None
    if resolved_text_fx_cues:
        text_fx_path = work_dir / "text_fx.ass"
        write_text_fx_ass(
            cues=resolved_text_fx_cues,
            path=text_fx_path,
            width=config.render.width,
            height=config.render.height,
            captions=style.captions,
            highlights=style.highlights,
            video_duration=plan.duration,
        )

    renderer = Renderer(project_root, work_dir, output_dir, config, style)
    graphics_dir = work_dir / "graphics"
    clips: list[Path] = []
    deferred_highlights: list[tuple[Path, float, float, float]] = []
    for scene in plan.scenes:
        shot = scene.shot
        print(
            f"[plano {scene.index:02}] {scene.frame_count / plan.fps:.2f}s | "
            f"{shot.asset_id} | {shot.motion} | {shot.transition_out}"
        )
        prepared_asset = asset_manager.prepare(scene.asset, shot.focus_x, shot.focus_y)
        overlay = None
        if shot.highlight is not None:
            overlay = create_highlight_overlay(
                text=shot.highlight.text,
                path=graphics_dir / f"highlight_{scene.index:02}.png",
                root=project_root,
                width=config.render.width,
                height=config.render.height,
                style=style.highlights,
            )
            if prepared_overlays:
                highlight = shot.highlight
                start = scene.start_frame / plan.fps + max(0.0, highlight.start_seconds)
                desired = highlight.duration_seconds or style.highlights.default_duration
                semantic_end = scene.end_frame / plan.fps - style.highlights.end_margin_seconds
                end = min(start + desired, max(start + style.highlights.minimum_visible_seconds, semantic_end))
                fade = min(style.highlights.fade_seconds, max(0.02, (end - start) / 3))
                deferred_highlights.append((overlay, start, end, fade))
                overlay = None
        clips.append(renderer.render_scene(scene, prepared_asset, overlay))

    print("[timeline] compondo cortes, crossfades e legendas...")
    timeline_video = renderer.compose_timeline(
        clips, plan, captions_path, text_fx_path,
        prepared_overlays, tuple(deferred_highlights),
    )
    print("[final] adicionando voz e mixagem...")
    output = renderer.mux_audio(
        timeline_video,
        audio,
        f"{episode.story.slug}.mp4",
        background_music=background_music,
        sfx_cues=sfx_cues,
    )
    print("[capa] gerando capa e incorporando abertura padrao...")
    output, cover_path, cover_duration = embed_episode_cover_intro(
        project_root,
        episode.directory,
        output,
        config,
    )
    print(
        f"[capa] abertura={cover_duration:.2f}s | "
        f"arquivo={cover_path.relative_to(project_root).as_posix()}"
    )
    print(f"[pronto] {output}")
    return output


def _project_directory(project_root: Path, relative: str, label: str) -> Path:
    directory = (project_root / relative).resolve()
    try:
        directory.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError(f"Pasta {label} fora do projeto: {directory}") from exc
    return directory


def _reset_episode_work_dir(project_root: Path, work_root: Path, episode_name: str) -> Path:
    work_root = work_root.resolve()
    try:
        work_root.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError(f"Pasta de trabalho fora do projeto: {work_root}") from exc
    work_root.mkdir(parents=True, exist_ok=True)
    work_dir = safe_child(work_root, episode_name)
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    return work_dir


def _safe_project_child(
    project_root: Path,
    parent: Path,
    name: str,
    label: str,
) -> Path:
    resolved_parent = parent.resolve()
    try:
        resolved_parent.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError(f"Pasta de {label} fora do projeto: {resolved_parent}") from exc
    child = safe_child(resolved_parent, name)
    try:
        child.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError(f"Pasta de {label} fora do projeto: {child}") from exc
    return child


def _assert_disjoint_roots(roots: dict[str, Path]) -> None:
    entries = list(roots.items())
    for index, (left_name, left) in enumerate(entries):
        left = left.resolve()
        for right_name, right in entries[index + 1 :]:
            right = right.resolve()
            if left == right or left in right.parents or right in left.parents:
                raise RuntimeError(
                    f"Diretorios de runtime nao podem se sobrepor: "
                    f"{left_name}={left} e {right_name}={right}."
                )
