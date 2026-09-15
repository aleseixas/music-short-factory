from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .delivery import DEFAULT_DELIVERY


MOTIONS = {"push_in", "pull_out", "pan_left", "pan_right", "hold"}
VISUAL_FX_TYPES = {
    "slow_zoom_in",
    "slow_zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
    "punch_zoom",
}
DEFAULT_VISUAL_FX_INTENSITY = 0.5
TEXT_FX_ANIMATIONS = {"pop_in", "scale_bounce", "slide_up", "fade_pop"}
TEXT_FX_POSITIONS = {"center"}
DEFAULT_TEXT_FX_INTENSITY = 0.5
OVERLAY_ANIMATIONS = {"pop_in", "scale_bounce", "slide_up", "slide_left", "slide_right", "fade_in"}
OVERLAY_POSITIONS = {"center", "upper_center", "lower_center", "left", "right"}
DEFAULT_OVERLAY_SCALE = 0.38
DEFAULT_OVERLAY_OPACITY = 1.0
TRANSITIONS = {"cut", "crossfade"}
VIDEO_ASSET_EXTENSIONS = frozenset({".mp4", ".mov", ".webm"})
DEFAULT_VIDEO_SPEED = 1.0
MIN_VIDEO_SPEED = 0.5
MAX_VIDEO_SPEED = 2.0
MIN_FREEZE_DURATION_SECONDS = 0.10
MAX_FREEZE_DURATION_SECONDS = 2.0


@dataclass(frozen=True)
class AssetSpec:
    id: str
    file: str
    url: str | None
    credit: str
    license: str
    focus_x: float
    focus_y: float

    @property
    def media_type(self) -> str:
        return "video" if Path(self.file).suffix.lower() in VIDEO_ASSET_EXTENSIONS else "image"

    @property
    def is_video(self) -> bool:
        return self.media_type == "video"


@dataclass(frozen=True)
class ScriptSegment:
    id: str
    text: str
    # None preserves the monolithic legacy TTS path. Its effective editorial
    # meaning is still neutral.
    delivery: str | None = None

    @property
    def effective_delivery(self) -> str:
        return self.delivery or DEFAULT_DELIVERY


@dataclass(frozen=True)
class Story:
    title: str
    slug: str
    segments: tuple[ScriptSegment, ...]
    target_duration_seconds: float = 75.0

    @property
    def narration(self) -> str:
        return " ".join(segment.text.strip() for segment in self.segments).strip()

    @property
    def uses_segment_delivery(self) -> bool:
        return any(segment.delivery is not None for segment in self.segments)


@dataclass(frozen=True)
class HighlightSpec:
    text: str
    start_seconds: float = 0.18
    duration_seconds: float | None = None


@dataclass(frozen=True)
class BackgroundMusicSpec:
    profile: str
    volume: float
    start_seconds: float = 0.0


@dataclass(frozen=True)
class ResolvedBackgroundMusic:
    profile: str
    path: Path
    volume: float
    start_seconds: float = 0.0


@dataclass(frozen=True)
class SfxCue:
    time_seconds: float
    type: str
    volume: float
    source_start_seconds: float = 0.0
    duration_seconds: float | None = None


@dataclass(frozen=True)
class ResolvedSfxCue:
    index: int
    time_seconds: float
    type: str
    path: Path
    volume: float
    source_start_seconds: float = 0.0
    duration_seconds: float | None = None


@dataclass(frozen=True)
class VisualFxCue:
    start_seconds: float
    end_seconds: float
    type: str
    intensity: float = DEFAULT_VISUAL_FX_INTENSITY


@dataclass(frozen=True)
class ResolvedVisualFxCue:
    index: int
    start_frame: int
    end_frame: int
    local_start_frame: int
    local_end_frame: int
    type: str
    intensity: float


@dataclass(frozen=True)
class TextFxCue:
    start_seconds: float
    end_seconds: float
    text: str
    animation: str
    position: str = "center"
    intensity: float = DEFAULT_TEXT_FX_INTENSITY
    accent_text: str | None = None


@dataclass(frozen=True)
class RelativeTextFxCue:
    segment_id: str
    offset_seconds: float
    duration_seconds: float
    text: str
    animation: str
    position: str = "center"
    intensity: float = DEFAULT_TEXT_FX_INTENSITY
    accent_text: str | None = None


@dataclass(frozen=True)
class ResolvedTextFxCue:
    start_seconds: float
    end_seconds: float
    text: str
    animation: str
    position: str = "center"
    intensity: float = DEFAULT_TEXT_FX_INTENSITY
    accent_text: str | None = None
    start_limit_seconds: float | None = None
    end_limit_seconds: float | None = None


TextFxCueSpec = TextFxCue | RelativeTextFxCue


@dataclass(frozen=True)
class OverlayCue:
    start_seconds: float
    end_seconds: float
    asset_id: str
    animation: str
    position: str
    scale: float = DEFAULT_OVERLAY_SCALE
    opacity: float = DEFAULT_OVERLAY_OPACITY


@dataclass(frozen=True)
class FreezeFrameSpec:
    start_seconds: float
    duration_seconds: float


@dataclass(frozen=True)
class SmartVisualPacingSpec:
    enabled: bool = True


@dataclass(frozen=True)
class ResolvedFreezeFrame:
    start_frame: int
    duration_frames: int

    @property
    def added_frames(self) -> int:
        # The selected source frame already contributes one output frame.
        return self.duration_frames - 1


@dataclass(frozen=True)
class ShotSpec:
    id: str
    segment_id: str
    asset_id: str
    motion: str
    transition_out: str
    highlight: HighlightSpec | None = None
    focus_x: float | None = None
    focus_y: float | None = None
    source_start_seconds: float = 0.0
    source_end_seconds: float | None = None
    speed: float = DEFAULT_VIDEO_SPEED
    freeze_frame: FreezeFrameSpec | None = None


@dataclass(frozen=True)
class TimelineSpec:
    shots: tuple[ShotSpec, ...]
    background_music: BackgroundMusicSpec | None = None
    sfx_cues: tuple[SfxCue, ...] = ()
    visual_fx_cues: tuple[VisualFxCue, ...] = ()
    text_fx_cues: tuple[TextFxCueSpec, ...] = ()
    overlay_cues: tuple[OverlayCue, ...] = ()
    smart_visual_pacing: SmartVisualPacingSpec | None = None


@dataclass(frozen=True)
class WordTiming:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class TimelineScene:
    index: int
    shot: ShotSpec
    asset: AssetSpec
    start_frame: int
    end_frame: int
    render_frames: int
    transition_frames: int
    visual_fx_cues: tuple[ResolvedVisualFxCue, ...] = ()
    freeze_frame: ResolvedFreezeFrame | None = None

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def source_frame_count(self) -> int:
        added_frames = self.freeze_frame.added_frames if self.freeze_frame else 0
        return self.render_frames - added_frames

    def required_source_duration(self, fps: int) -> float:
        return self.source_frame_count / fps * self.shot.speed


@dataclass(frozen=True)
class TimelinePlan:
    fps: int
    total_frames: int
    audio_duration: float
    scenes: tuple[TimelineScene, ...]

    @property
    def duration(self) -> float:
        return self.total_frames / self.fps


@dataclass(frozen=True)
class AudioResult:
    path: Path
    duration: float
    words: tuple[WordTiming, ...]
    source: str
    exact_timings: bool


@dataclass(frozen=True)
class Episode:
    name: str
    directory: Path
    story: Story
    assets: dict[str, AssetSpec]
    shots: tuple[ShotSpec, ...]
    background_music: BackgroundMusicSpec | None = None
    sfx_cues: tuple[SfxCue, ...] = ()
    visual_fx_cues: tuple[VisualFxCue, ...] = ()
    text_fx_cues: tuple[TextFxCueSpec, ...] = ()
    overlay_cues: tuple[OverlayCue, ...] = ()
    smart_visual_pacing: SmartVisualPacingSpec | None = None

    @property
    def assets_dir(self) -> Path:
        return self.directory / "assets"
