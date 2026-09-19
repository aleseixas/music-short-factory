from __future__ import annotations

import math
from pathlib import Path

from .config import ProjectConfig, StyleConfig
from .ffmpeg import (
    LoudnormMeasurement,
    parse_loudnorm_measurement,
    probe_duration,
    probe_video_frame_count,
    run_ffmpeg,
    run_ffmpeg_capture,
)
from .models import (
    AudioResult,
    ResolvedBackgroundMusic,
    ResolvedSfxCue,
    TimelinePlan,
    TimelineScene,
    OverlayCue,
)
from .motion import build_motion_filter


LOUDNESS_INTEGRATED_LUFS = -15.0
LOUDNESS_TRUE_PEAK_DBTP = -1.0
LOUDNESS_AAC_HEADROOM_DB = 0.5
LOUDNESS_FILTER_TRUE_PEAK_DBTP = (
    LOUDNESS_TRUE_PEAK_DBTP - LOUDNESS_AAC_HEADROOM_DB
)
LOUDNESS_RANGE_LU = 11.0


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

    def render_scene(self, scene: TimelineScene, prepared_asset: Path, overlay: Path | None) -> Path:
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
            visual_fx_cues=scene.visual_fx_cues,
            scene_start_frame=scene.start_frame,
            input_fps_normalized=scene.asset.is_video,
        )
        output = self.scene_dir / f"scene_{scene.index:02}.mkv"
        arguments: list[object] = ["-y", "-hide_banner"]
        source_filter = motion_filter
        if scene.asset.is_video:
            source_duration = scene.required_source_duration(render.fps)
            speed_filter = (
                "setpts=PTS-STARTPTS,"
                if scene.shot.speed == 1.0
                else f"setpts=(PTS-STARTPTS)/{scene.shot.speed:.6f},"
            )
            freeze_filter = ""
            if scene.freeze_frame is not None:
                freeze = scene.freeze_frame
                next_source_frame = freeze.start_frame + 1
                if next_source_frame >= scene.source_frame_count:
                    # A freeze ending with the shot has no later frame whose PTS
                    # can be shifted. Extend the selected final frame explicitly;
                    # this is a finite pad, not a source loop.
                    freeze_filter = (
                        f"tpad=stop_mode=clone:stop={freeze.added_frames},"
                        f"trim=end_frame={scene.render_frames},"
                        f"settb=AVTB,setpts=N/({render.fps}*TB),"
                    )
                else:
                    # Move all frames after the selected one forward, then let
                    # fps deterministically fill the gap with that frame.
                    freeze_filter = (
                        "setpts='PTS+if("
                        f"gte(N\\,{next_source_frame})\\,"
                        f"{freeze.added_frames}/({render.fps}*TB)\\,0)',"
                        f"fps=fps={render.fps}:start_time=0,"
                        f"trim=end_frame={scene.render_frames},"
                        f"settb=AVTB,setpts=N/({render.fps}*TB),"
                    )
            work_width = render.width * render.working_scale
            work_height = render.height * render.working_scale
            source_filter = (
                f"trim=start={scene.shot.source_start_seconds:.6f}:"
                f"duration={source_duration:.6f},"
                f"{speed_filter}"
                f"fps={render.fps},settb=AVTB,setpts=N/({render.fps}*TB),"
                f"{freeze_filter}"
                f"scale={work_width}:{work_height}:"
                "force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={work_width}:{work_height}:(iw-ow)/2:(ih-oh)/2,"
                "setsar=1,"
                f"{motion_filter}"
            )
            arguments.extend(["-i", prepared_asset])
        else:
            arguments.extend(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    render.fps,
                    "-i",
                    prepared_asset,
                ]
            )

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
                f"[0:v]{source_filter}[base];"
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
            arguments.extend(["-vf", source_filter])

        if scene.asset.is_video:
            # Preserve the duration of the final CFR frame in the intermediate
            # container so xfade sees the same timing it already receives from
            # image scenes.
            arguments.extend(["-r", render.fps])

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

    def compose_timeline(
        self, clips: list[Path], plan: TimelinePlan, captions_path: Path,
        text_fx_path: Path | None = None,
        graphic_overlays: tuple[tuple[OverlayCue, Path], ...] = (),
        highlight_overlays: tuple[tuple[Path, float, float, float], ...] = (),
    ) -> Path:
        if len(clips) != len(plan.scenes):
            raise RuntimeError("Quantidade de clips nao corresponde a timeline.")
        render = self.config.render
        arguments: list[object] = ["-y", "-hide_banner"]
        for clip in clips:
            arguments.extend(["-i", clip])
        for _, overlay_path in graphic_overlays:
            arguments.extend(["-loop", "1", "-framerate", render.fps, "-i", overlay_path])
        for overlay_path, _, _, _ in highlight_overlays:
            arguments.extend(["-loop", "1", "-framerate", render.fps, "-i", overlay_path])

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
        if text_fx_path is None and not graphic_overlays and not highlight_overlays:
            # Keep the legacy graph byte-for-byte equivalent when the optional
            # editorial layer is absent.
            filters.append(
                f"{current}tpad=stop_mode=clone:stop={render.fps},trim=end_frame={plan.total_frames},"
                f"settb=AVTB,setpts=N/({render.fps}*TB),fps={render.fps},"
                f"subtitles=filename='{caption_filter_path}',setsar=1,format=yuv420p,"
                "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[out]"
            )
        else:
            filters.append(
                f"{current}tpad=stop_mode=clone:stop={render.fps},trim=end_frame={plan.total_frames},"
                f"settb=AVTB,setpts=N/({render.fps}*TB),fps={render.fps}[base_timeline]"
            )
            composed = "[base_timeline]"
            overlay_input = len(clips)
            for index, (cue, _) in enumerate(graphic_overlays):
                label = f"graphic{index}"
                scale = _overlay_scale_expression(cue)
                enter, leave = _overlay_fade_times(cue)
                x, y = _overlay_position_expressions(cue, render.width, render.height, enter)
                filters.append(
                    f"[{overlay_input + index}:v]format=rgba,"
                    f"trim=duration={cue.end_seconds - cue.start_seconds:.4f},"
                    f"setpts=PTS-STARTPTS+{cue.start_seconds:.4f}/TB,"
                    f"colorchannelmixer=aa={cue.opacity:.6f},"
                    f"fade=t=in:st={cue.start_seconds:.4f}:d={enter:.4f}:alpha=1,"
                    f"fade=t=out:st={max(cue.start_seconds, cue.end_seconds - leave):.4f}:d={leave:.4f}:alpha=1,"
                    f"scale=w='max(2\\,trunc(iw*({scale})/2)*2)':"
                    f"h='max(2\\,trunc(ih*({scale})/2)*2)':eval=frame[{label}]"
                )
                output = f"[with_graphic{index}]"
                filters.append(
                    f"{composed}[{label}]overlay=x='{x}':y='{y}':eval=frame:"
                    f"eof_action=pass:repeatlast=0:"
                    f"enable='between(t,{cue.start_seconds:.4f},{cue.end_seconds:.4f})'{output}"
                )
                composed = output
            if text_fx_path is not None:
                text_rel = text_fx_path.relative_to(self.root).as_posix()
                text_filter_path = text_rel.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
                filters.append(f"{composed}subtitles=filename='{text_filter_path}'[with_text_fx]")
                composed = "[with_text_fx]"
            highlight_input = overlay_input + len(graphic_overlays)
            for index, (_, start, end, fade) in enumerate(highlight_overlays):
                label = f"highlight{index}"
                filters.append(
                    f"[{highlight_input + index}:v]scale={render.width}:{render.height},format=rgba,"
                    f"trim=duration={end - start:.4f},setpts=PTS-STARTPTS+{start:.4f}/TB,"
                    f"fade=t=in:st={start:.4f}:d={fade:.4f}:alpha=1,"
                    f"fade=t=out:st={max(start, end - fade):.4f}:d={fade:.4f}:alpha=1[{label}]"
                )
                output = f"[with_highlight{index}]"
                filters.append(
                    f"{composed}[{label}]overlay=0:0:eof_action=pass:repeatlast=0:"
                    f"enable='between(t,{start:.4f},{end:.4f})'{output}"
                )
                composed = output
            filters.append(
                f"{composed}subtitles=filename='{caption_filter_path}',setsar=1,format=yuv420p,"
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

    def mux_audio(
        self,
        video_path: Path,
        audio: AudioResult,
        output_name: str,
        background_music: ResolvedBackgroundMusic | None = None,
        sfx_cues: tuple[ResolvedSfxCue, ...] = (),
    ) -> Path:
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

        sfx_video_duration: float | None = None
        active_sfx_cues: list[ResolvedSfxCue] = []
        if sfx_cues:
            video_frames = probe_video_frame_count(video_path)
            if video_frames <= 0:
                raise RuntimeError(f"Video sem frames para mixagem: {video_path}")
            sfx_video_duration = video_frames / self.config.render.fps
            for cue in sfx_cues:
                sfx_path = cue.path.resolve()
                if not sfx_path.is_file():
                    raise RuntimeError(
                        f"Arquivo de SFX nao encontrado para o type {cue.type!r}: "
                        f"{sfx_path}"
                    )
                if cue.time_seconds >= sfx_video_duration:
                    print(
                        f"[sfx] aviso: cue {cue.index} ({cue.type}) ignorada; "
                        f"time_seconds={cue.time_seconds:.3f}s esta no ou apos o fim "
                        f"do video ({sfx_video_duration:.3f}s)."
                    )
                    continue
                active_sfx_cues.append(cue)

        if background_music is not None:
            music_path = background_music.path.resolve()
            if not music_path.is_file():
                raise RuntimeError(
                    f"Arquivo de background music nao encontrado: {music_path}"
                )
            arguments.extend(["-stream_loop", "-1", "-i", music_path])
            if sfx_video_duration is None:
                video_frames = probe_video_frame_count(video_path)
                if video_frames <= 0:
                    raise RuntimeError(f"Video sem frames para mixagem: {video_path}")
                video_duration = video_frames / self.config.render.fps
            else:
                video_duration = sfx_video_duration
            music_start = background_music.start_seconds
            if (not math.isfinite(music_start) or music_start < 0
                    or music_start >= video_duration):
                raise RuntimeError(
                    "background_music.start_seconds precisa ficar entre zero e "
                    f"o fim do video ({video_duration:.3f}s), sem incluir o fim."
                )
            delay_samples = round(music_start * mix.sample_rate)
            music_duration = video_duration - delay_samples / mix.sample_rate
            if music_duration <= 0:
                raise RuntimeError("background_music.start_seconds nao deixa samples audiveis.")
            fade_in = min(
                mix.background_music_fade_in_seconds,
                music_duration / 2,
            )
            fade_out = min(
                mix.background_music_fade_out_seconds,
                music_duration / 2,
            )
            fade_out_start = max(0.0, music_duration - fade_out)
            music_filters = [
                f"[2:a]aresample={mix.sample_rate}",
                f"aformat=sample_fmts=fltp:sample_rates={mix.sample_rate}:"
                "channel_layouts=stereo",
                f"atrim=duration={music_duration:.6f}",
                "asetpts=N/SR/TB",
                f"volume={background_music.volume:.6f}",
            ]
            if fade_in > 0:
                music_filters.append(f"afade=t=in:st=0:d={fade_in:.6f}")
            if fade_out > 0:
                music_filters.append(
                    f"afade=t=out:st={fade_out_start:.6f}:d={fade_out:.6f}"
                )
            music_graph = ",".join(music_filters)
            if delay_samples:
                # Prefix the same sample-accurate silence used for timed SFX;
                # ducking still receives both inputs from PTS zero.
                music_graph += (
                    "[music_body];"
                    f"anullsrc=r={mix.sample_rate}:cl=stereo,"
                    f"atrim=end_sample={delay_samples},asetpts=N/SR/TB,"
                    f"aformat=sample_fmts=fltp:sample_rates={mix.sample_rate}:"
                    "channel_layouts=stereo[music_silence];"
                    "[music_silence][music_body]concat=n=2:v=0:a=1,"
                    f"apad=whole_dur={video_duration:.6f},"
                    f"atrim=duration={video_duration:.6f},asetpts=N/SR/TB"
                )
            base_audio_filter = (
                f"[1:a]aresample={mix.sample_rate},"
                f"aformat=sample_fmts=fltp:sample_rates={mix.sample_rate}:"
                "channel_layouts=stereo,"
                f"atrim=duration={video_duration:.6f},"
                f"apad=whole_dur={video_duration:.6f},"
                f"atrim=duration={video_duration:.6f},"
                f"asetpts=N/SR/TB,volume={mix.voice_volume:.4f},"
                "asplit=2[voice][duck_control];"
                + music_graph
                + "[music];"
                "[music][duck_control]sidechaincompress="
                f"threshold={mix.background_music_ducking_threshold:.6f}:"
                f"ratio={mix.background_music_ducking_ratio:.3f}:"
                f"attack={mix.background_music_ducking_attack_ms:.3f}:"
                f"release={mix.background_music_ducking_release_ms:.3f}:"
                "makeup=1:detection=rms[ducked_music]"
            )
            sfx_filters, sfx_labels = self._append_sfx_inputs(
                arguments,
                active_sfx_cues,
                video_duration,
                first_input_index=3,
            )
            if sfx_labels:
                audio_filter = (
                    base_audio_filter
                    + ";"
                    + ";".join(sfx_filters)
                    + ";[voice][ducked_music]"
                    + "".join(sfx_labels)
                    + f"amix=inputs={2 + len(sfx_labels)}:duration=longest:"
                    "dropout_transition=0:normalize=0,"
                    + f"alimiter=limit={mix.limiter:.4f},"
                    + f"atrim=duration={video_duration:.6f},asetpts=N/SR/TB[a]"
                )
            else:
                audio_filter = (
                    base_audio_filter
                    + ";[voice][ducked_music]"
                    "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                    + f"alimiter=limit={mix.limiter:.4f},"
                    + f"atrim=duration={video_duration:.6f},asetpts=N/SR/TB[a]"
                )
        elif mix.music_file.strip():
            candidate = Path(mix.music_file)
            music_path = candidate if candidate.is_absolute() else self.root / candidate
            if not music_path.exists():
                raise RuntimeError(f"Trilha configurada nao encontrada: {music_path}")
            arguments.extend(["-stream_loop", "-1", "-i", music_path])
            fade_duration = mix.music_fade_out_seconds
            fade_start = max(0.0, audio.duration - fade_duration)
            if active_sfx_cues:
                assert sfx_video_duration is not None
                sfx_filters, sfx_labels = self._append_sfx_inputs(
                    arguments,
                    active_sfx_cues,
                    sfx_video_duration,
                    first_input_index=3,
                )
                audio_filter = (
                    f"[1:a]aresample={mix.sample_rate},"
                    f"volume={mix.voice_volume:.4f}[voice];"
                    f"[2:a]aresample={mix.sample_rate},"
                    f"volume={mix.music_volume:.4f},"
                    f"atrim=duration={audio.duration:.6f},"
                    f"afade=t=out:st={fade_start:.6f}:d={fade_duration:.6f}[music];"
                    "[voice][music]amix=inputs=2:duration=first:"
                    "dropout_transition=0:normalize=0[voice_music];"
                    + ";".join(sfx_filters)
                    + ";[voice_music]"
                    + "".join(sfx_labels)
                    + f"amix=inputs={1 + len(sfx_labels)}:duration=longest:"
                    "dropout_transition=0:normalize=0,"
                    + f"alimiter=limit={mix.limiter:.4f},"
                    + f"atrim=duration={sfx_video_duration:.6f},asetpts=N/SR/TB[a]"
                )
            else:
                audio_filter = (
                    f"[1:a]aresample={mix.sample_rate},volume={mix.voice_volume:.4f}[voice];"
                    f"[2:a]aresample={mix.sample_rate},volume={mix.music_volume:.4f},"
                    f"atrim=duration={audio.duration:.6f},"
                    f"afade=t=out:st={fade_start:.6f}:d={fade_duration:.6f}[music];"
                    "[voice][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                    f"alimiter=limit={mix.limiter:.4f}[a]"
                )
        else:
            if active_sfx_cues:
                assert sfx_video_duration is not None
                sfx_filters, sfx_labels = self._append_sfx_inputs(
                    arguments,
                    active_sfx_cues,
                    sfx_video_duration,
                    first_input_index=2,
                )
                audio_filter = (
                    f"[1:a]aresample={mix.sample_rate},"
                    f"aformat=sample_fmts=fltp:sample_rates={mix.sample_rate}:"
                    "channel_layouts=stereo,"
                    f"atrim=duration={sfx_video_duration:.6f},"
                    f"apad=whole_dur={sfx_video_duration:.6f},"
                    f"atrim=duration={sfx_video_duration:.6f},"
                    f"asetpts=N/SR/TB,volume={mix.voice_volume:.4f}[voice];"
                    + ";".join(sfx_filters)
                    + ";[voice]"
                    + "".join(sfx_labels)
                    + f"amix=inputs={1 + len(sfx_labels)}:duration=longest:"
                    "dropout_transition=0:normalize=0,"
                    + f"alimiter=limit={mix.limiter:.4f},"
                    + f"atrim=duration={sfx_video_duration:.6f},asetpts=N/SR/TB[a]"
                )
            else:
                audio_filter = (
                    f"[1:a]aresample={mix.sample_rate},volume={mix.voice_volume:.4f},"
                    f"alimiter=limit={mix.limiter:.4f}[a]"
                )

        try:
            loudness = self._analyze_final_mix_loudness(arguments, audio_filter)
        except RuntimeError as exc:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"Falha na normalizacao do audio final durante a analise: {exc}"
            ) from exc

        normalized_audio_filter = (
            audio_filter
            + ";[a]"
            + self._loudnorm_second_pass_filter(loudness)
            + "[normalized_audio]"
        )
        arguments.extend(
            [
                "-filter_complex",
                normalized_audio_filter,
                "-map",
                "0:v:0",
                "-map",
                "[normalized_audio]",
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
        try:
            run_ffmpeg(arguments)
        except RuntimeError as exc:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"Falha na normalizacao do audio final durante a aplicacao: {exc}"
            ) from exc
        expected_frames = probe_video_frame_count(video_path)
        final_frames = probe_video_frame_count(partial)
        if final_frames != expected_frames:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"Saida final com {final_frames} frames; esperado: {expected_frames}."
            )
        final_duration = probe_duration(partial)
        expected_duration = expected_frames / self.config.render.fps
        tolerance = 2 / self.config.render.fps + 0.02
        if abs(final_duration - expected_duration) > tolerance:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"Duracao final invalida: {final_duration:.3f}s; "
                f"video esperado: {expected_duration:.3f}s."
            )
        partial.replace(output)
        return output

    def _analyze_final_mix_loudness(
        self,
        input_arguments: list[object],
        audio_filter: str,
    ) -> LoudnormMeasurement:
        first_pass_filter = (
            audio_filter
            + ";[a]"
            + f"loudnorm=I={LOUDNESS_INTEGRATED_LUFS:.3f}:"
            + f"TP={LOUDNESS_FILTER_TRUE_PEAK_DBTP:.3f}:"
            + f"LRA={LOUDNESS_RANGE_LU:.3f}:print_format=json"
            + "[loudnorm_analysis]"
        )
        output = run_ffmpeg_capture(
            [
                *input_arguments,
                "-nostats",
                "-filter_complex",
                first_pass_filter,
                "-map",
                "[loudnorm_analysis]",
                "-vn",
                "-f",
                "null",
                "-",
            ]
        )
        return parse_loudnorm_measurement(output)

    @staticmethod
    def _loudnorm_second_pass_filter(
        measurement: LoudnormMeasurement,
    ) -> str:
        return (
            f"loudnorm=I={LOUDNESS_INTEGRATED_LUFS:.3f}:"
            f"TP={LOUDNESS_FILTER_TRUE_PEAK_DBTP:.3f}:"
            f"LRA={LOUDNESS_RANGE_LU:.3f}:"
            f"measured_I={measurement.input_i:.6f}:"
            f"measured_TP={measurement.input_tp:.6f}:"
            f"measured_LRA={measurement.input_lra:.6f}:"
            f"measured_thresh={measurement.input_thresh:.6f}:"
            f"offset={measurement.target_offset:.6f}:"
            "linear=true:print_format=summary"
        )

    def _append_sfx_inputs(
        self,
        arguments: list[object],
        cues: list[ResolvedSfxCue],
        video_duration: float,
        first_input_index: int,
    ) -> tuple[list[str], list[str]]:
        mix = self.config.mix
        filters: list[str] = []
        labels: list[str] = []
        for position, cue in enumerate(cues):
            arguments.extend(["-i", cue.path.resolve()])
            input_index = first_input_index + position
            label = f"[sfx{position}]"
            remaining = video_duration - cue.time_seconds
            effect_duration = (
                min(cue.duration_seconds, remaining)
                if cue.duration_seconds is not None
                else remaining
            )
            delay_samples = round(cue.time_seconds * mix.sample_rate)
            if cue.source_start_seconds != 0 or cue.duration_seconds is not None:
                trim_filter = (
                    f"atrim=start={cue.source_start_seconds:.6f}:"
                    f"duration={effect_duration:.6f}"
                )
            else:
                # Preserve the legacy graph exactly when no source trim exists.
                trim_filter = f"atrim=duration={remaining:.6f}"
            effect_filter = (
                f"[{input_index}:a]aresample={mix.sample_rate},"
                f"aformat=sample_fmts=fltp:sample_rates={mix.sample_rate}:"
                "channel_layouts=stereo,"
                f"{trim_filter},asetpts=N/SR/TB,"
                f"volume={cue.volume:.6f}"
            )
            tail_filter = (
                f"apad=whole_dur={video_duration:.6f},"
                f"atrim=duration={video_duration:.6f},asetpts=N/SR/TB{label}"
            )
            if delay_samples:
                # Prefix real silence so amix receives every cue from PTS zero.
                effect_label = f"[sfx_effect{position}]"
                silence_label = f"[sfx_silence{position}]"
                filters.append(
                    effect_filter
                    + effect_label
                    + f";anullsrc=r={mix.sample_rate}:cl=stereo,"
                    + f"atrim=end_sample={delay_samples},asetpts=N/SR/TB,"
                    + f"aformat=sample_fmts=fltp:sample_rates={mix.sample_rate}:"
                    + f"channel_layouts=stereo{silence_label};"
                    + silence_label
                    + effect_label
                    + "concat=n=2:v=0:a=1,"
                    + tail_filter
                )
            else:
                filters.append(effect_filter + "," + tail_filter)
            labels.append(label)
        return filters, labels


def _overlay_fade_times(cue: OverlayCue) -> tuple[float, float]:
    duration = cue.end_seconds - cue.start_seconds
    phases = 3 if cue.animation == "scale_bounce" else 2 if cue.animation == "pop_in" else 1
    enter = max(0.001, min(0.16, duration / (phases + 2)))
    leave = max(0.001, min(0.18, duration / 4))
    return enter, leave


def _overlay_ease(start: float, duration: float) -> str:
    progress = f"max(0\\,min(1\\,(t-{start:.4f})/{duration:.4f}))"
    return f"(0.5-0.5*cos(PI*{progress}))"


def _overlay_scale_expression(cue: OverlayCue) -> str:
    start = cue.start_seconds
    enter, _ = _overlay_fade_times(cue)
    if cue.animation == "pop_in":
        settle = start + enter * 2
        return (
            f"if(lt(t\\,{start + enter:.4f})\\,0.75+0.30*{_overlay_ease(start, enter)}\\,"
            f"if(lt(t\\,{settle:.4f})\\,1.05-0.05*{_overlay_ease(start + enter, enter)}\\,1))"
        )
    if cue.animation == "scale_bounce":
        second = start + enter * 2
        third = start + enter * 3
        return (
            f"if(lt(t\\,{start + enter:.4f})\\,0.65+0.47*{_overlay_ease(start, enter)}\\,"
            f"if(lt(t\\,{second:.4f})\\,1.12-0.16*{_overlay_ease(start + enter, enter)}\\,"
            f"if(lt(t\\,{third:.4f})\\,0.96+0.04*{_overlay_ease(second, enter)}\\,1)))"
        )
    if cue.animation == "fade_in":
        return f"if(lt(t\\,{start + enter:.4f})\\,0.95+0.05*{_overlay_ease(start, enter)}\\,1)"
    return "1.000000"


def _overlay_position_expressions(
    cue: OverlayCue,
    width: int,
    height: int,
    enter: float,
) -> tuple[str, str]:
    # A proportional safe rectangle reserves 24% at the top for highlights and
    # 22% at the bottom for word captions, on any output resolution.
    margin_x = max(4, round(width * 0.075))
    safe_top = round(height * 0.24)
    safe_bottom = round(height * 0.22)
    center_x = f"min(max(({width}-w)/2\\,{margin_x})\\,{width}-w-{margin_x})"
    center_y = f"min(max(({height}-h)/2\\,{safe_top})\\,{height}-h-{safe_bottom})"
    x_by_position = {
        "center": center_x,
        "upper_center": center_x,
        "lower_center": center_x,
        "left": f"min(max({width}*0.18-w/2\\,{margin_x})\\,{width}-w-{margin_x})",
        "right": f"min(max({width}*0.82-w/2\\,{margin_x})\\,{width}-w-{margin_x})",
    }
    y_by_position = {
        "center": center_y,
        "upper_center": f"min(max({height}*0.40-h/2\\,{safe_top})\\,{height}-h-{safe_bottom})",
        "lower_center": f"min(max({height}*0.60-h/2\\,{safe_top})\\,{height}-h-{safe_bottom})",
        "left": center_y,
        "right": center_y,
    }
    x, y = x_by_position[cue.position], y_by_position[cue.position]
    distance = max(4, round(width * 0.058))
    if cue.animation == "slide_up":
        y = f"min(({y})\\,{height}-h-{safe_bottom}-{distance})"
        y = f"({y})+if(lt(t\\,{cue.start_seconds + enter:.4f})\\,{distance}*(1-{_overlay_ease(cue.start_seconds, enter)})\\,0)"
    elif cue.animation == "slide_left":
        x = f"min(({x})\\,{width}-w-{margin_x}-{distance})"
        x = f"({x})+if(lt(t\\,{cue.start_seconds + enter:.4f})\\,{distance}*(1-{_overlay_ease(cue.start_seconds, enter)})\\,0)"
    elif cue.animation == "slide_right":
        x = f"max(({x})\\,{margin_x}+{distance})"
        x = f"({x})-if(lt(t\\,{cue.start_seconds + enter:.4f})\\,{distance}*(1-{_overlay_ease(cue.start_seconds, enter)})\\,0)"
    return x, y
