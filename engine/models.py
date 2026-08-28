from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MOTIONS = {"push_in", "pull_out", "pan_left", "pan_right", "hold"}
TRANSITIONS = {"cut", "crossfade"}


@dataclass(frozen=True)
class AssetSpec:
    id: str
    file: str
    url: str | None
    credit: str
    license: str
    focus_x: float
    focus_y: float


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
class ShotSpec:
    id: str
    segment_id: str
    asset_id: str
    motion: str
    transition_out: str
    highlight: HighlightSpec | None = None
    focus_x: float | None = None
    focus_y: float | None = None


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

    @property
    def assets_dir(self) -> Path:
        return self.directory / "assets"
