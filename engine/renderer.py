from __future__ import annotations

from pathlib import Path

from .config import ProjectConfig, StyleConfig
from .ffmpeg import probe_duration, probe_video_frame_count, run_ffmpeg
from .models import AudioResult, TimelinePlan, TimelineScene
from .motion import build_motion_filter


class Renderer:
    def __init__(self, root: Path, work_dir: Path, output_dir: Path, config: ProjectConfig, style: StyleConfig):
        self.root = root
        self.work_dir = work_dir
        self.output_dir = output_dir
        self.config = config
        self.style = style
        self.scene_dir = work_dir / "scenes"
        self.scene_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render_scene(self, scene: TimelineScene, prepared_image: Path, overlay: Path | None) -> Path:
        render = self.config.render
        focus_x = scene.asset.focus_x if scene.shot.focus_x is None else scene.shot.focus_x
        focus_y = scene.asset.focus_y if scene.shot.focus_y is None else scene.shot.focus_y
        motion_filter = build_motion_filter(
            motion=scene.shot.motion,
            frames=scene.render_frames,
            fps=render.fps,
            output_width=render.width,
            output_height=render.height,
            working_scale=render.working_scale,
            focus_x=focus_x,
            focus_y=focus_y,
            style=self.style.motion,
        )
        output = self.scene_dir / f"scene_{scene.index:02}.mkv"
        arguments: list[object] = [
            "-y",
            "-hide_banner",
            "-loop",
            "1",
            "-framerate",
            render.fps,
            "-i",
            prepared_image,
        ]

        if overlay is not None and scene.shot.highlight is not None:
            highlight = scene.shot.highlight
            start = max(0.0, highlight.start_seconds)
            desired_duration = highlight.duration_seconds or self.style.highlights.default_duration
            semantic_duration = scene.frame_count / render.fps
            end = min(
                start + desired_duration,
                max(
                    start + self.style.highlights.minimum_visible_seconds,
                    semantic_duration - self.style.highlights.end_margin_seconds,
                ),
            )
            fade = min(self.style.highlights.fade_seconds, max(0.02, (end - start) / 3))
            filter_graph = (
                f"[0:v]{motion_filter}[base];"
                f"[1:v]scale={render.width}:{render.height},format=rgba,"
                f"fade=t=in:st={start:.4f}:d={fade:.4f}:alpha=1,"
                f"fade=t=out:st={max(start, end - fade):.4f}:d={fade:.4f}:alpha=1[title];"
                "[base][title]overlay=0:0:eof_action=repeat:shortest=1,format=yuv420p[out]"
            )
            arguments.extend(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    render.fps,
                    "-i",
                    overlay,
                    "-filter_complex",
                    filter_graph,
                    "-map",
                    "[out]",
                ]
            )
        else:
            arguments.extend(["-vf", motion_filter])

        arguments.extend(
            [
                "-frames:v",
                scene.render_frames,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                render.intermediate_preset,
                "-crf",
                render.intermediate_crf,
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
                output,
            ]
        )
        run_ffmpeg(arguments)
        return output

    def compose_timeline(self, clips: list[Path], plan: TimelinePlan, captions_path: Path) -> Path:
        if len(clips) != len(plan.scenes):
            raise RuntimeError("Quantidade de clips nao corresponde a timeline.")
        render = self.config.render
        arguments: list[object] = ["-y", "-hide_banner"]
        for clip in clips:
            arguments.extend(["-i", clip])

        filters: list[str] = []
        for index in range(len(clips)):
            filters.append(
                f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,"
                f"fps={render.fps},format=yuv420p[s{index}]"
            )

        current = "[s0]"
        elapsed_frames = plan.scenes[0].render_frames
        for index in range(1, len(clips)):
            previous = plan.scenes[index - 1]
            raw_label = f"[raw{index}]"
            output_label = f"[joined{index}]"
            if previous.transition_frames:
                fade_seconds = previous.transition_frames / render.fps
                offset_seconds = (elapsed_frames - previous.transition_frames) / render.fps
                filters.append(
                    f"{current}[s{index}]xfade=transition=fade:duration={fade_seconds:.6f}:"
                    f"offset={offset_seconds:.6f}{raw_label}"
                )
                elapsed_frames += plan.scenes[index].render_frames - previous.transition_frames
            else:
                filters.append(f"{current}[s{index}]concat=n=2:v=1:a=0{raw_label}")
                elapsed_frames += plan.scenes[index].render_frames
            filters.append(
                f"{raw_label}settb=AVTB,setpts=PTS-STARTPTS,"
                f"fps={render.fps},format=yuv420p{output_label}"
            )
            current = output_label

        caption_rel = captions_path.relative_to(self.root).as_posix()
        caption_filter_path = (
            caption_rel.replace("\\", r"\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
        )
        filters.append(
            f"{current}tpad=stop_mode=clone:stop={render.fps},trim=end_frame={plan.total_frames},"
            f"settb=AVTB,setpts=N/({render.fps}*TB),fps={render.fps},"
            f"subtitles=filename='{caption_filter_path}',setsar=1,format=yuv420p,"
            "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[out]"
        )
        video_only = self.work_dir / "timeline_captioned.mp4"
        arguments.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[out]",
                "-frames:v",
                plan.total_frames,
                "-an",
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
                video_only,
            ]
        )
        run_ffmpeg(arguments, cwd=self.root)
        actual_frames = probe_video_frame_count(video_only)
        if actual_frames != plan.total_frames:
            raise RuntimeError(
                f"Timeline renderizada com {actual_frames} frames; esperado: {plan.total_frames}."
            )
        return video_only

    def mux_audio(self, video_path: Path, audio: AudioResult, output_name: str) -> Path:
        mix = self.config.mix
        output = self.output_dir / output_name
        partial = output.with_name(f"{output.stem}.part{output.suffix}")
        partial.unlink(missing_ok=True)
        arguments: list[object] = [
            "-y",
            "-hide_banner",
            "-i",
            video_path,
            "-i",
            audio.path,
        ]

        music_path: Path | None = None
        if mix.music_file.strip():
            candidate = Path(mix.music_file)
            music_path = candidate if candidate.is_absolute() else self.root / candidate
            if not music_path.exists():
                raise RuntimeError(f"Trilha configurada nao encontrada: {music_path}")

        if music_path is not None:
            arguments.extend(["-stream_loop", "-1", "-i", music_path])
            fade_duration = mix.music_fade_out_seconds
            fade_start = max(0.0, audio.duration - fade_duration)
            audio_filter = (
                f"[1:a]aresample={mix.sample_rate},volume={mix.voice_volume:.4f}[voice];"
                f"[2:a]aresample={mix.sample_rate},volume={mix.music_volume:.4f},"
                f"atrim=duration={audio.duration:.6f},"
                f"afade=t=out:st={fade_start:.6f}:d={fade_duration:.6f}[music];"
                "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                f"alimiter=limit={mix.limiter:.4f}[a]"
            )
        else:
            audio_filter = (
                f"[1:a]aresample={mix.sample_rate},volume={mix.voice_volume:.4f},"
                f"alimiter=limit={mix.limiter:.4f}[a]"
            )

        arguments.extend(
            [
                "-filter_complex",
                audio_filter,
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-c:v",
                "copy",
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
        )
        run_ffmpeg(arguments)
        expected_frames = probe_video_frame_count(video_path)
        final_frames = probe_video_frame_count(partial)
        if final_frames != expected_frames:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"Saida final com {final_frames} frames; esperado: {expected_frames}."
            )
        final_duration = probe_duration(partial)
        tolerance = 1 / self.config.render.fps + 0.02
        if abs(final_duration - audio.duration) > tolerance:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"Duracao final invalida: {final_duration:.3f}s; "
                f"audio: {audio.duration:.3f}s."
            )
        partial.replace(output)
        return output
