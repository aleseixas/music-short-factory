from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import subprocess
import tempfile
from typing import Literal
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError

from .ffmpeg import probe_video_stream, run_ffmpeg
from .models import VIDEO_ASSET_EXTENSIONS


VisualFingerprintKind = Literal["image", "video"]

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
PHASH_BITS = 64
PHASH_DISTANCE_THRESHOLD = 12
VIDEO_FRAME_COUNT = 8
VIDEO_FRAME_DISTANCE_THRESHOLD = 12
VIDEO_SIMILARITY_THRESHOLD = 0.82
MAX_REPETITION_PENALTY = 70.0
DEFAULT_RECENT_EPISODE_LIMIT = 24
TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
    }
)


def _clean_hex(value: object, length: int, label: str) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip().casefold()
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} invalido.")
    return text


def _hex_tuple(value: object, length: int, label: str, *, dedupe: bool = True) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} precisa ser uma lista.")
    cleaned: list[str] = []
    for raw in value:
        item = _clean_hex(raw, length, label)
        if item is not None and (not dedupe or item not in cleaned):
            cleaned.append(item)
    return tuple(cleaned)


def _finite_non_negative(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} invalido.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} invalido.") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{label} invalido.")
    return parsed


@dataclass(frozen=True)
class VisualFingerprint:
    kind: VisualFingerprintKind
    sha256: str | None = None
    url_hashes: tuple[str, ...] = ()
    perceptual_hashes: tuple[str, ...] = ()
    video_frame_hashes: tuple[str, ...] = ()
    source_start_seconds: float = 0.0
    source_duration_seconds: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "sha256": self.sha256,
            "url_hashes": list(self.url_hashes),
            "perceptual_hashes": list(self.perceptual_hashes),
            "video_frame_hashes": list(self.video_frame_hashes),
            "source_start_seconds": round(self.source_start_seconds, 6),
            "source_duration_seconds": (
                round(self.source_duration_seconds, 6)
                if self.source_duration_seconds is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, raw: object) -> VisualFingerprint:
        if not isinstance(raw, dict):
            raise ValueError("Fingerprint visual precisa ser um objeto.")
        kind = str(raw.get("kind") or "").strip().casefold()
        if kind not in {"image", "video"}:
            raise ValueError("Tipo de fingerprint visual invalido.")
        start = _finite_non_negative(
            raw.get("source_start_seconds", 0.0),
            "source_start_seconds",
        )
        raw_duration = raw.get("source_duration_seconds")
        duration = None
        if raw_duration is not None:
            duration = _finite_non_negative(raw_duration, "source_duration_seconds")
            if duration <= 0:
                raise ValueError("source_duration_seconds precisa ser positivo.")

        raw_urls = raw.get("url_hashes")
        if raw_urls is None and isinstance(raw.get("urls"), (list, tuple)):
            raw_urls = [
                _url_digest(str(url))
                for url in raw["urls"]
                if canonicalize_visual_url(str(url))
            ]
        return cls(
            kind=kind,  # type: ignore[arg-type]
            sha256=_clean_hex(raw.get("sha256"), 64, "sha256"),
            url_hashes=_hex_tuple(raw_urls, 64, "url_hashes"),
            perceptual_hashes=_hex_tuple(
                raw.get("perceptual_hashes"), 16, "perceptual_hashes"
            ),
            video_frame_hashes=_hex_tuple(
                raw.get("video_frame_hashes"), 16, "video_frame_hashes", dedupe=False
            ),
            source_start_seconds=start,
            source_duration_seconds=duration,
        )


@dataclass(frozen=True)
class VisualHistoryEntry:
    episode: str
    shot_id: str
    asset_id: str
    fingerprint: VisualFingerprint
    recorded_at: str | None = None
    recency_rank: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "episode": self.episode,
            "shot_id": self.shot_id,
            "asset_id": self.asset_id,
            "recorded_at": self.recorded_at,
            "fingerprint": self.fingerprint.as_dict(),
        }

    @classmethod
    def from_dict(cls, raw: object) -> VisualHistoryEntry:
        if not isinstance(raw, dict):
            raise ValueError("Uso visual precisa ser um objeto.")
        episode = str(raw.get("episode") or "").strip()
        shot_id = str(raw.get("shot_id") or "").strip()
        asset_id = str(raw.get("asset_id") or "").strip()
        if (
            not re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", episode)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", shot_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", asset_id)
        ):
            raise ValueError("Uso visual sem episode, shot_id ou asset_id.")
        recorded_at = str(raw.get("recorded_at") or "").strip() or None
        if recorded_at is not None and _parse_datetime(recorded_at) is None:
            raise ValueError("recorded_at invalido no uso visual.")
        raw_rank = raw.get("recency_rank", 0)
        if isinstance(raw_rank, bool):
            raise ValueError("recency_rank invalido no uso visual.")
        try:
            rank = int(raw_rank)
        except (TypeError, ValueError) as exc:
            raise ValueError("recency_rank invalido no uso visual.") from exc
        if rank < 0:
            raise ValueError("recency_rank invalido no uso visual.")
        return cls(
            episode=episode,
            shot_id=shot_id,
            asset_id=asset_id,
            fingerprint=VisualFingerprint.from_dict(raw.get("fingerprint")),
            recorded_at=recorded_at,
            recency_rank=rank,
        )


@dataclass(frozen=True)
class RepetitionMatch:
    method: Literal["url", "sha256", "image_phash", "video_frames"]
    episode: str
    shot_id: str
    asset_id: str
    similarity: float
    recency_rank: int
    penalty: float

    def as_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "episode": self.episode,
            "shot_id": self.shot_id,
            "asset_id": self.asset_id,
            "similarity": round(self.similarity, 4),
            "recency_rank": self.recency_rank,
            "penalty": round(self.penalty, 2),
        }

    @classmethod
    def from_dict(cls, raw: object) -> RepetitionMatch:
        if not isinstance(raw, dict):
            raise ValueError("Match de repeticao precisa ser um objeto.")
        method = str(raw.get("method") or "").strip()
        if method not in {"url", "sha256", "image_phash", "video_frames"}:
            raise ValueError("Metodo de repeticao invalido.")
        try:
            similarity = float(raw.get("similarity"))
            recency_rank = int(raw.get("recency_rank", 0))
            penalty = float(raw.get("penalty"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Match de repeticao invalido.") from exc
        if (
            not math.isfinite(similarity)
            or not 0 <= similarity <= 1
            or recency_rank < 0
            or not math.isfinite(penalty)
            or penalty > 0
        ):
            raise ValueError("Match de repeticao invalido.")
        return cls(
            method=method,  # type: ignore[arg-type]
            episode=str(raw.get("episode") or "").strip(),
            shot_id=str(raw.get("shot_id") or "").strip(),
            asset_id=str(raw.get("asset_id") or "").strip(),
            similarity=similarity,
            recency_rank=recency_rank,
            penalty=penalty,
        )


@dataclass(frozen=True)
class RepetitionAssessment:
    is_repeated: bool
    penalty: float
    matches: tuple[RepetitionMatch, ...] = ()
    warnings: tuple[str, ...] = ()
    blocked: bool = False

    @property
    def score_adjustment(self) -> float:
        return self.penalty

    def adjust_score(self, score: float) -> float:
        return round(max(0.0, min(100.0, float(score) + self.penalty)), 2)

    def as_dict(self) -> dict[str, object]:
        return {
            "is_repeated": self.is_repeated,
            "repetition_detected": self.is_repeated,
            "penalty": round(self.penalty, 2),
            "score_adjustment": round(self.penalty, 2),
            "blocked": self.blocked,
            "reason_codes": list(dict.fromkeys(match.method for match in self.matches)),
            "matches": [match.as_dict() for match in self.matches],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, raw: object) -> RepetitionAssessment:
        if not isinstance(raw, dict):
            raise ValueError("Avaliacao de repeticao precisa ser um objeto.")
        raw_matches = raw.get("matches", [])
        if not isinstance(raw_matches, list):
            raise ValueError("matches precisa ser uma lista.")
        matches = tuple(RepetitionMatch.from_dict(item) for item in raw_matches)
        try:
            penalty = float(raw.get("penalty", raw.get("score_adjustment", 0.0)))
        except (TypeError, ValueError) as exc:
            raise ValueError("Penalidade de repeticao invalida.") from exc
        if not math.isfinite(penalty) or penalty > 0:
            raise ValueError("Penalidade de repeticao invalida.")
        raw_warnings = raw.get("warnings", [])
        if not isinstance(raw_warnings, list):
            raise ValueError("warnings precisa ser uma lista.")
        return cls(
            is_repeated=bool(raw.get("is_repeated", raw.get("repetition_detected", matches))),
            penalty=penalty,
            matches=matches,
            warnings=tuple(str(item) for item in raw_warnings),
            # Repetition is deliberately advisory and can never become a hard gate.
            blocked=False,
        )


def canonicalize_visual_url(raw_url: str) -> str:
    """Return a stable comparison form without persisting credentials or fragments."""
    value = str(raw_url or "").strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold().rstrip(".")
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = host
    else:
        netloc = f"{host}:{port}"
    path = quote(unquote(parsed.path or "/"), safe="/%:@!$&'()*+,;=-._~")
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        folded = key.casefold()
        if folded.startswith("utm_") or folded in TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, value))
    query_items.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query_items, doseq=True), ""))


def build_visual_fingerprint(
    path: Path,
    kind: VisualFingerprintKind,
    urls: Sequence[str] = (),
    source_start_seconds: float = 0.0,
    source_duration_seconds: float | None = None,
) -> VisualFingerprint:
    """Fingerprint one local visual; video frames are sampled only inside its used trim."""
    source = Path(path).resolve()
    if kind not in {"image", "video"}:
        raise RuntimeError(f"Tipo visual invalido para fingerprint: {kind!r}.")
    if not source.is_file() or source.stat().st_size <= 0:
        raise RuntimeError(f"Midia visual ausente ou vazia para fingerprint: {source.name}")
    start = _finite_non_negative(source_start_seconds, "source_start_seconds")
    duration = None
    if source_duration_seconds is not None:
        duration = _finite_non_negative(source_duration_seconds, "source_duration_seconds")
        if duration <= 0:
            raise RuntimeError("source_duration_seconds precisa ser positivo.")

    url_hashes = tuple(
        dict.fromkeys(
            digest
            for digest in (_url_digest(url) for url in urls)
            if digest
        )
    )
    sha256 = _file_sha256(source)
    if kind == "image":
        if start != 0 or duration is not None:
            raise RuntimeError("Trim de fonte so pode ser usado em fingerprint de video.")
        try:
            with Image.open(source) as opened:
                hashes = _image_perceptual_hashes(ImageOps.exif_transpose(opened))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise RuntimeError(f"Imagem invalida para fingerprint: {source.name}") from exc
        return VisualFingerprint(
            kind="image",
            sha256=sha256,
            url_hashes=url_hashes,
            perceptual_hashes=hashes,
        )

    info = probe_video_stream(source)
    tolerance = 1e-6
    if start >= info.duration - tolerance:
        raise RuntimeError("source_start_seconds fica no ou apos o fim do video.")
    effective_duration = info.duration - start if duration is None else duration
    if start + effective_duration > info.duration + tolerance:
        raise RuntimeError("Intervalo do fingerprint ultrapassa a duracao do video.")
    frame_hashes = _sample_video_frame_hashes(source, start, effective_duration)
    if not frame_hashes:
        raise RuntimeError("FFmpeg nao produziu frames para o fingerprint do video.")
    return VisualFingerprint(
        kind="video",
        sha256=sha256,
        url_hashes=url_hashes,
        video_frame_hashes=frame_hashes,
        source_start_seconds=start,
        source_duration_seconds=effective_duration,
    )


def fingerprint_to_usage(
    fingerprint: VisualFingerprint,
    episode: str,
    shot_id: str,
    asset_id: str,
    recorded_at: str | None = None,
) -> dict[str, object]:
    """Serialize one selected shot for visual_resolution_report.json."""
    return VisualHistoryEntry(
        episode=str(episode).strip(),
        shot_id=str(shot_id).strip(),
        asset_id=str(asset_id).strip(),
        fingerprint=fingerprint,
        recorded_at=recorded_at,
    ).as_dict()


def assess_repetition(
    candidate: VisualFingerprint,
    history: Sequence[VisualHistoryEntry],
) -> RepetitionAssessment:
    """Return an explainable ranking penalty. A repeat is never a hard error."""
    warnings: list[str] = []
    if not isinstance(candidate, VisualFingerprint):
        return RepetitionAssessment(
            False,
            0.0,
            warnings=("Fingerprint candidato invalido; anti-repeticao ignorada.",),
        )

    matches: list[RepetitionMatch] = []
    strongest_by_usage: dict[tuple[str, str, str], float] = {}
    base_penalties = {
        "url": 56.0,
        "sha256": 62.0,
        "image_phash": 50.0,
        "video_frames": 54.0,
    }
    for raw_entry in history:
        if not isinstance(raw_entry, VisualHistoryEntry):
            warnings.append("Entrada invalida no historico visual foi ignorada.")
            continue
        previous = raw_entry.fingerprint
        if previous.kind != candidate.kind:
            continue
        detected: list[tuple[str, float]] = []
        overlap = _source_overlap(candidate, previous) if candidate.kind == "video" else 1.0
        if overlap > 0 and set(candidate.url_hashes).intersection(previous.url_hashes):
            detected.append(("url", overlap))
        if overlap > 0 and candidate.sha256 and candidate.sha256 == previous.sha256:
            detected.append(("sha256", overlap))
        if candidate.kind == "image":
            similarity = _image_hash_similarity(
                candidate.perceptual_hashes,
                previous.perceptual_hashes,
            )
            if similarity is not None:
                detected.append(("image_phash", similarity))
        else:
            similarity = _video_hash_similarity(
                candidate.video_frame_hashes,
                previous.video_frame_hashes,
            )
            if similarity is not None:
                detected.append(("video_frames", similarity))
        if not detected:
            continue

        factor = _recency_factor(raw_entry.recency_rank)
        usage_key = (raw_entry.episode, raw_entry.shot_id, raw_entry.asset_id)
        for method, similarity in detected:
            penalty = -round(base_penalties[method] * factor * similarity, 2)
            strongest_by_usage[usage_key] = max(
                strongest_by_usage.get(usage_key, 0.0),
                abs(penalty),
            )
            matches.append(
                RepetitionMatch(
                    method=method,  # type: ignore[arg-type]
                    episode=raw_entry.episode,
                    shot_id=raw_entry.shot_id,
                    asset_id=raw_entry.asset_id,
                    similarity=similarity,
                    recency_rank=raw_entry.recency_rank,
                    penalty=penalty,
                )
            )

    if not matches:
        return RepetitionAssessment(False, 0.0, warnings=tuple(dict.fromkeys(warnings)))

    strengths = sorted(strongest_by_usage.values(), reverse=True)
    combined = strengths[0] + sum(value * 0.20 for value in strengths[1:])
    penalty = -round(min(MAX_REPETITION_PENALTY, combined), 2)
    matches.sort(
        key=lambda match: (
            match.recency_rank,
            match.penalty,
            match.episode,
            match.shot_id,
            match.method,
        )
    )
    return RepetitionAssessment(
        True,
        penalty,
        matches=tuple(matches),
        warnings=tuple(dict.fromkeys(warnings)),
        blocked=False,
    )


def load_visual_history(
    project_root: Path,
    exclude_episode: str | None = None,
    recent_episode_limit: int = DEFAULT_RECENT_EPISODE_LIMIT,
) -> tuple[tuple[VisualHistoryEntry, ...], tuple[str, ...]]:
    """Load persisted selected-shot fingerprints and URL-only legacy fallbacks."""
    warnings: list[str] = []
    if (
        isinstance(recent_episode_limit, bool)
        or not isinstance(recent_episode_limit, int)
        or recent_episode_limit < 1
    ):
        warnings.append(
            f"recent_episode_limit invalido; usando {DEFAULT_RECENT_EPISODE_LIMIT}."
        )
        recent_episode_limit = DEFAULT_RECENT_EPISODE_LIMIT

    episodes_root = Path(project_root).resolve() / "episodes"
    if not episodes_root.is_dir():
        return (), tuple(warnings)

    grouped: list[
        tuple[str, datetime | None, list[VisualHistoryEntry], set[str]]
    ] = []
    creation_dates = _episode_creation_dates(Path(project_root))
    excluded = str(exclude_episode or "").strip()
    for episode_dir in sorted(episodes_root.iterdir(), key=lambda item: item.name):
        if not episode_dir.is_dir() or episode_dir.name == excluded:
            continue
        persisted: list[VisualHistoryEntry] = []
        report_time: datetime | None = None
        for report_name in ("visual_usage.json", "visual_resolution_report.json"):
            report_path = episode_dir / report_name
            if not report_path.is_file():
                continue
            try:
                report = json.loads(report_path.read_text(encoding="utf-8-sig"))
                if not isinstance(report, dict):
                    raise ValueError("objeto JSON esperado")
                candidate_report_time = _parse_datetime(
                    str(report.get("recorded_at") or report.get("generated_at") or "")
                )
                if candidate_report_time is not None and (
                    report_time is None or candidate_report_time > report_time
                ):
                    report_time = candidate_report_time
                raw_usage = report.get("visual_usage")
                if raw_usage is not None and not isinstance(raw_usage, list):
                    warnings.append(
                        f"Historico visual invalido em episodes/{episode_dir.name}/{report_name}; visual_usage ignorado."
                    )
                elif isinstance(raw_usage, list):
                    for index, item in enumerate(raw_usage):
                        try:
                            entry = VisualHistoryEntry.from_dict(item)
                            if entry.episode != episode_dir.name:
                                entry = replace(entry, episode=episode_dir.name)
                            # visual_usage.json is definitive. The earlier
                            # resolution report may fill only shots that could
                            # not be recorded after render; it never replaces a
                            # final entry for the same shot.
                            if entry.shot_id not in {
                                current.shot_id for current in persisted
                            }:
                                persisted.append(entry)
                        except (TypeError, ValueError):
                            warnings.append(
                                f"Uso visual invalido em episodes/{episode_dir.name}/{report_name}[{index}] foi ignorado."
                            )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                warnings.append(
                    f"Relatorio visual corrompido em episodes/{episode_dir.name}; usando fallback legado quando possivel."
                )
        legacy = _load_legacy_episode_usage(episode_dir, warnings, hydrate=False)
        persisted_shots = {entry.shot_id for entry in persisted}
        entries = persisted + [entry for entry in legacy if entry.shot_id not in persisted_shots]
        if not entries:
            continue
        dated = [
            parsed
            for parsed in (_parse_datetime(entry.recorded_at) for entry in entries)
            if parsed is not None
        ]
        effective_time = max(dated) if dated else report_time or creation_dates.get(episode_dir.name)
        grouped.append((episode_dir.name, effective_time, entries, persisted_shots))

    grouped.sort(
        key=lambda group: (
            group[1] is not None,
            group[1] or datetime.min.replace(tzinfo=timezone.utc),
            group[0],
        ),
        reverse=True,
    )
    selected = grouped[:recent_episode_limit]
    loaded: list[VisualHistoryEntry] = []
    for recency_rank, (episode, _date, entries, persisted_shots) in enumerate(selected):
        # Only the selected recent window incurs local file hashing; no download.
        needs_hydration = {
            entry.shot_id for entry in entries
            if entry.shot_id not in persisted_shots and not entry.recorded_at and not (
                entry.fingerprint.sha256 or entry.fingerprint.perceptual_hashes
                or entry.fingerprint.video_frame_hashes
            )
        }
        hydrated = {
            entry.shot_id: entry for entry in _load_legacy_episode_usage(
                episodes_root / episode, warnings
            )
        } if needs_hydration else {}
        loaded.extend(replace(
            hydrated.get(entry.shot_id, entry) if entry.shot_id in needs_hydration else entry,
            recency_rank=recency_rank,
        ) for entry in entries)
    return tuple(loaded), tuple(dict.fromkeys(warnings))


def _load_legacy_episode_usage(
    episode_dir: Path,
    warnings: list[str],
    *,
    hydrate: bool = True,
) -> list[VisualHistoryEntry]:
    assets_path = episode_dir / "assets.json"
    timeline_path = episode_dir / "timeline.json"
    if not assets_path.is_file() or not timeline_path.is_file():
        return []
    try:
        assets_data = json.loads(assets_path.read_text(encoding="utf-8-sig"))
        timeline_data = json.loads(timeline_path.read_text(encoding="utf-8-sig"))
        raw_assets = assets_data.get("assets") if isinstance(assets_data, dict) else None
        raw_shots = timeline_data.get("shots") if isinstance(timeline_data, dict) else None
        if not isinstance(raw_assets, list) or not isinstance(raw_shots, list):
            raise ValueError("assets/shots ausentes")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        warnings.append(
            f"Manifestos visuais invalidos em episodes/{episode_dir.name}; episodio ignorado no historico."
        )
        return []

    assets = {
        str(asset.get("id") or "").strip(): asset
        for asset in raw_assets
        if isinstance(asset, dict) and str(asset.get("id") or "").strip()
    }
    entries: list[VisualHistoryEntry] = []
    for index, shot in enumerate(raw_shots):
        if not isinstance(shot, dict):
            continue
        asset_id = str(shot.get("asset") or "").strip()
        asset = assets.get(asset_id)
        if not isinstance(asset, dict):
            continue
        file_name = str(asset.get("file") or "").strip()
        suffix = Path(file_name).suffix.casefold()
        if suffix in VIDEO_ASSET_EXTENSIONS:
            kind: VisualFingerprintKind = "video"
        elif suffix in IMAGE_EXTENSIONS:
            kind = "image"
        else:
            continue
        url = str(asset.get("url") or "").strip()
        source = episode_dir / "assets" / file_name
        try:
            source.resolve().relative_to((episode_dir / "assets").resolve())
        except ValueError:
            warnings.append(f"Caminho visual invalido em episodes/{episode_dir.name}; asset ignorado.")
            continue
        sha256 = None
        perceptual_hashes: tuple[str, ...] = ()
        if hydrate and source.is_file():
            try:
                sha256 = _file_sha256(source)
                if kind == "image":
                    with Image.open(source) as opened:
                        perceptual_hashes = _image_perceptual_hashes(
                            ImageOps.exif_transpose(opened)
                        )
            except (UnidentifiedImageError, OSError, ValueError):
                warnings.append(
                    f"Imagem legada invalida em episodes/{episode_dir.name}/assets; usando apenas URL/hash exato."
                )
        fingerprint = VisualFingerprint(
            kind=kind,
            sha256=sha256,
            url_hashes=tuple(filter(None, (_url_digest(url),))),
            perceptual_hashes=perceptual_hashes,
            source_start_seconds=_safe_non_negative(shot.get("source_start_seconds")),
            # source_end is an available bound, not proof of consumed duration.
            source_duration_seconds=None,
        )
        if not (
            fingerprint.sha256
            or fingerprint.url_hashes
            or fingerprint.perceptual_hashes
            or source.is_file()
        ):
            continue
        entries.append(
            VisualHistoryEntry(
                episode=episode_dir.name,
                shot_id=str(shot.get("id") or f"shot_{index + 1}"),
                asset_id=asset_id,
                fingerprint=fingerprint,
            )
        )
    return entries


def _episode_creation_dates(project_root: Path) -> dict[str, datetime]:
    """Use Git history rather than checkout mtimes to order legacy episodes."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "log", "--format=%x1e%ct", "--name-only",
             "--diff-filter=A", "--", "episodes"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        if result.returncode:
            return {}
        dates: dict[str, datetime] = {}
        timestamp = None
        for line in result.stdout.split("\n"):
            if line.startswith("\x1e"):
                timestamp = datetime.fromtimestamp(int(line[1:].strip()), timezone.utc)
            elif timestamp is not None:
                parts = line.strip().split("/")
                if len(parts) == 3 and parts[0] == "episodes" and parts[2] == "timeline.json":
                    dates.setdefault(parts[1], timestamp)
        return dates
    except (OSError, ValueError, OverflowError, subprocess.TimeoutExpired):
        return {}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _url_digest(url: str) -> str:
    canonical = canonicalize_visual_url(url)
    if not canonical:
        return ""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _center_crop(image: Image.Image, ratio: float) -> Image.Image:
    width, height = image.size
    crop_width = max(1, round(width * ratio))
    crop_height = max(1, round(height * ratio))
    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    return image.crop((left, top, left + crop_width, top + crop_height))


def _image_perceptual_hashes(image: Image.Image) -> tuple[str, ...]:
    converted = image.convert("RGB")
    # Fingerprinting needs low-frequency structure, not full-resolution pixels.
    # Bound memory before materializing the center/corner crop variants.
    converted.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    # Flat fills have no reliable perceptual identity. Keep exact URL/SHA evidence
    # for these rather than claiming different plain backgrounds are the same photo.
    if ImageStat.Stat(converted.convert("L")).stddev[0] < 2.0:
        return ()
    crops = [_center_crop(converted, ratio) for ratio in (1.0, 0.90, 0.80)]
    width, height = converted.size
    crop_width, crop_height = max(1, round(width * .8)), max(1, round(height * .8))
    for x in (0, width - crop_width):
        for y in (0, height - crop_height):
            crops.append(converted.crop((x, y, x + crop_width, y + crop_height)))
    hashes = [_perceptual_hash(crop) for crop in crops]
    return tuple(dict.fromkeys(hashes))


def _perceptual_hash(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    statistics = ImageStat.Stat(rgb)
    if max(statistics.stddev) < 2.0:
        # pHash carries no colour for a flat frame. A deterministic colour token
        # keeps synthetic/blank red and blue clips from matching by accident.
        colour = bytes(min(15, round(value / 17)) for value in statistics.mean)
        return hashlib.sha256(b"flat-frame:" + colour).hexdigest()[:16]
    size = 32
    low_frequency = 8
    grayscale = image.convert("L").resize(
        (size, size),
        Image.Resampling.LANCZOS,
    )
    pixels = list(grayscale.get_flattened_data())
    cosines = [
        [math.cos(math.pi * (2 * coordinate + 1) * frequency / (2 * size)) for coordinate in range(size)]
        for frequency in range(low_frequency)
    ]
    row_projection = [
        [sum(pixels[y * size + x] * cosines[u][x] for x in range(size))
         for u in range(low_frequency)]
        for y in range(size)
    ]
    coefficients: list[float] = []
    for vertical in range(low_frequency):
        for horizontal in range(low_frequency):
            total = sum(row_projection[y][horizontal] * cosines[vertical][y] for y in range(size))
            coefficients.append(total)
    pivot = median(coefficients[1:])
    bits = 0
    for coefficient in coefficients:
        bits = (bits << 1) | int(coefficient > pivot)
    return f"{bits:016x}"


def _sample_video_frame_hashes(
    path: Path,
    source_start_seconds: float,
    source_duration_seconds: float,
) -> tuple[str, ...]:
    with tempfile.TemporaryDirectory(prefix="msf-visual-fingerprint-") as directory:
        target = Path(directory) / "frame-%03d.png"
        sample_fps = VIDEO_FRAME_COUNT / source_duration_seconds
        run_ffmpeg(
            [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{source_start_seconds:.6f}",
                "-t",
                f"{source_duration_seconds:.6f}",
                "-i",
                path,
                "-t",
                f"{source_duration_seconds:.6f}",
                "-an",
                "-vf",
                (
                    f"trim=duration={source_duration_seconds:.6f},setpts=PTS-STARTPTS,"
                    f"fps={sample_fps:.9f},"
                    "scale=160:160:force_original_aspect_ratio=decrease:flags=area"
                ),
                "-frames:v",
                str(VIDEO_FRAME_COUNT),
                target,
            ]
        )
        hashes: list[str] = []
        for frame_path in sorted(Path(directory).glob("frame-*.png")):
            try:
                with Image.open(frame_path) as frame:
                    hashes.append(_perceptual_hash(frame))
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                raise RuntimeError("Frame invalido retornado pelo FFmpeg.") from exc
        return tuple(hashes)


def _hamming(first: str, second: str) -> int:
    return (int(first, 16) ^ int(second, 16)).bit_count()


def _image_hash_similarity(
    first: Sequence[str],
    second: Sequence[str],
) -> float | None:
    if not first or not second:
        return None
    distance = min(_hamming(left, right) for left in first for right in second)
    if distance > PHASH_DISTANCE_THRESHOLD:
        return None
    return round(1.0 - distance / PHASH_BITS, 4)


def _video_hash_similarity(
    first: Sequence[str],
    second: Sequence[str],
) -> float | None:
    if len(first) < 2 or len(second) < 2:
        return None
    count = min(len(first), len(second))
    first_indexes = [round(index * (len(first) - 1) / max(1, count - 1)) for index in range(count)]
    second_indexes = [round(index * (len(second) - 1) / max(1, count - 1)) for index in range(count)]
    distances = [
        _hamming(first[first_index], second[second_index])
        for first_index, second_index in zip(first_indexes, second_indexes)
    ]
    average_similarity = sum(1.0 - distance / PHASH_BITS for distance in distances) / count
    close_ratio = sum(distance <= VIDEO_FRAME_DISTANCE_THRESHOLD for distance in distances) / count
    similarity = 0.75 * average_similarity + 0.25 * close_ratio
    if similarity < VIDEO_SIMILARITY_THRESHOLD or close_ratio < 0.50:
        return None
    return round(min(1.0, similarity), 4)


def _recency_factor(recency_rank: int) -> float:
    rank = max(0, int(recency_rank))
    return max(0.35, 1.0 - 0.04 * rank)


def _source_overlap(first: VisualFingerprint, second: VisualFingerprint) -> float:
    if first.source_duration_seconds is None or second.source_duration_seconds is None:
        # Legacy manifests give source identity but cannot prove the actual range.
        return 0.25
    overlap = min(first.source_start_seconds + first.source_duration_seconds,
                  second.source_start_seconds + second.source_duration_seconds) - max(
                      first.source_start_seconds, second.source_start_seconds)
    return max(0.0, min(1.0, overlap / min(first.source_duration_seconds, second.source_duration_seconds)))


def _parse_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_non_negative(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed >= 0 else 0.0
