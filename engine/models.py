from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class Story:
    title: str
    slug: str
    segments: tuple[ScriptSegment, ...]
    target_duration_seconds: float = 75.0

    @property
    def narration(self) -> str:
        return " ".join(segment.text.strip() for segment in self.segments).strip()


@dataclass(frozen=True)
class HighlightSpec:
    text: str
    start_seconds: float = 0.18
    duration_seconds: float | None = None


@dataclass(frozen=True)
class BackgroundMusicSpec:
    profile: str
    volume: float


@dataclass(frozen=True)
class ResolvedBackgroundMusic:
    profile: str
    path: Path
    volume: float


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
class OverlayCue:
    start_seconds: float
    end_seconds: float
    asset_id: str
    animation: str
    position: str
    scale: float = DEFAULT_OVERLAY_SCALE
    opacity: float = DEFAULT_OVERLAY_OPACITY


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


@dataclass(frozen=True)
class TimelineSpec:
    shots: tuple[ShotSpec, ...]
    background_music: BackgroundMusicSpec | None = None
    sfx_cues: tuple[SfxCue, ...] = ()
    visual_fx_cues: tuple[VisualFxCue, ...] = ()
    text_fx_cues: tuple[TextFxCue, ...] = ()
    overlay_cues: tuple[OverlayCue, ...] = ()


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

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame


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
    text_fx_cues: tuple[TextFxCue, ...] = ()
    overlay_cues: tuple[OverlayCue, ...] = ()

    @property
    def assets_dir(self) -> Path:
        return self.directory / "assets"
