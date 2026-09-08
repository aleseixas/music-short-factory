from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable

from .models import AssetSpec, Episode, TimelinePlan
from .utils import safe_child, validate_slug
from .visual_repetition import (
    VisualFingerprint,
    VisualHistoryEntry,
    build_visual_fingerprint,
    fingerprint_to_usage,
)


VISUAL_USAGE_SCHEMA_VERSION = 1
VISUAL_USAGE_FILE = "visual_usage.json"


def record_visual_usage(
    project_root: Path,
    episode: Episode,
    plan: TimelinePlan,
    resolved_asset_paths: Mapping[str, Path],
    *,
    now: Callable[[], datetime] | None = None,
) -> Path:
    """Persist fingerprints for the exact main-shot source ranges just rendered.

    This function never resolves or downloads media. It only fingerprints files that
    the completed render already materialized locally through ``AssetManager``.
    """
    root = Path(project_root).resolve()
    slug = validate_slug(episode.name)
    episode_dir = episode.directory.resolve()
    expected_episode_dir = safe_child(root / "episodes", slug)
    if episode_dir != expected_episode_dir or not episode_dir.is_dir():
        raise RuntimeError("Diretorio do episodio invalido para registrar uso visual.")
    if plan.fps <= 0:
        raise RuntimeError("FPS invalido para registrar uso visual.")

    recorded_at = _utc_timestamp((now or _utc_now)())
    usage: list[dict[str, object]] = []
    warnings: list[str] = []
    prior_usage = _resolution_usage(episode_dir)
    fingerprint_cache: dict[
        tuple[Path, str, float, float | None, tuple[str, ...]], VisualFingerprint
    ] = {}

    for scene in plan.scenes:
        asset = scene.asset
        fingerprint = None
        try:
            kind = "video" if asset.is_video else "image"
            source_start = scene.shot.source_start_seconds if asset.is_video else 0.0
            source_duration = (
                scene.required_source_duration(plan.fps) if asset.is_video else None
            )
            urls = (asset.url,) if asset.url else ()
            fallback = VisualFingerprint.from_dict({
                "kind": kind,
                "urls": urls,
                "source_start_seconds": source_start,
                "source_duration_seconds": source_duration,
            })
            if fallback.url_hashes:
                fingerprint = fallback
            source = _local_source_path(resolved_asset_paths, asset)
            cache_key = (
                source.resolve(),
                kind,
                round(source_start, 9),
                round(source_duration, 9) if source_duration is not None else None,
                urls,
            )
            cached = fingerprint_cache.get(cache_key)
            if cached is None:
                cached = build_visual_fingerprint(
                    source,
                    kind,
                    urls=urls,
                    source_start_seconds=source_start,
                    source_duration_seconds=source_duration,
                )
                fingerprint_cache[cache_key] = cached
            fingerprint = cached
            # Web videos become local assets without a URL. Retain the hashed
            # source identity only when the inspected bytes still match exactly.
            for previous in prior_usage:
                if (
                    previous.episode == slug
                    and previous.shot_id == scene.shot.id
                    and previous.asset_id == asset.id
                    and previous.fingerprint.kind == kind
                    and fingerprint.sha256
                    and previous.fingerprint.sha256 == fingerprint.sha256
                ):
                    fingerprint = replace(fingerprint, url_hashes=tuple(dict.fromkeys(
                        (*fingerprint.url_hashes, *previous.fingerprint.url_hashes)
                    )))
        except Exception as exc:
            # Fingerprinting is an advisory post-render record. One unsupported or
            # temporarily unreadable source must not invalidate a completed video.
            warnings.append(
                f"shot {scene.shot.id!r}: fingerprint indisponivel "
                f"({type(exc).__name__})."
            )
        if fingerprint is not None:
            usage.append(fingerprint_to_usage(
                fingerprint, slug, scene.shot.id, asset.id, recorded_at,
            ))

    payload: dict[str, object] = {
        "schema_version": VISUAL_USAGE_SCHEMA_VERSION,
        "episode": slug,
        "recorded_at": recorded_at,
        "visual_usage": usage,
    }
    if warnings:
        payload["warnings"] = warnings
    validate_visual_usage_payload(payload, slug)

    destination = safe_child(episode_dir, VISUAL_USAGE_FILE)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        f"[visual-usage] registrado: {len(usage)} shot(s) em "
        f"episodes/{slug}/{VISUAL_USAGE_FILE}"
    )
    return destination


def _resolution_usage(episode_dir: Path) -> tuple[VisualHistoryEntry, ...]:
    try:
        payload = json.loads((episode_dir / "visual_resolution_report.json").read_text(
            encoding="utf-8-sig"
        ))
        raw_usage = payload.get("visual_usage", [])
        if not isinstance(raw_usage, list):
            return ()
        entries = []
        for raw in raw_usage:
            try:
                entries.append(VisualHistoryEntry.from_dict(raw))
            except (TypeError, ValueError):
                continue
        return tuple(entries)
    except (OSError, UnicodeError, ValueError, AttributeError):
        return ()


def record_visual_usage_safely(
    project_root: Path,
    episode: Episode,
    plan: TimelinePlan,
    resolved_asset_paths: Mapping[str, Path],
) -> Path | None:
    """Best-effort wrapper used after a successful render and cover build."""
    try:
        return record_visual_usage(project_root, episode, plan, resolved_asset_paths)
    except Exception as exc:
        print(
            "[visual-usage] aviso: registro pos-render nao concluido "
            f"({type(exc).__name__}); o video renderizado continua valido."
        )
        return None


def validate_visual_usage_payload(
    payload: object,
    expected_episode: str | None = None,
) -> tuple[VisualHistoryEntry, ...]:
    """Validate the persisted contract before it is copied or committed."""
    if not isinstance(payload, dict):
        raise RuntimeError("visual_usage.json precisa conter um objeto JSON.")
    if payload.get("schema_version") != VISUAL_USAGE_SCHEMA_VERSION:
        raise RuntimeError("schema_version invalido em visual_usage.json.")
    slug = validate_slug(str(payload.get("episode") or ""), "episode")
    if expected_episode is not None and slug != validate_slug(expected_episode):
        raise RuntimeError("O episode de visual_usage.json nao corresponde ao slug.")
    _parse_utc_timestamp(payload.get("recorded_at"))
    raw_usage = payload.get("visual_usage")
    if not isinstance(raw_usage, list):
        raise RuntimeError("visual_usage.json precisa definir visual_usage como lista.")

    entries: list[VisualHistoryEntry] = []
    for index, raw in enumerate(raw_usage):
        try:
            entry = VisualHistoryEntry.from_dict(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Entrada visual_usage[{index}] invalida."
            ) from exc
        if entry.episode != slug:
            raise RuntimeError(
                f"Entrada visual_usage[{index}] pertence a outro episodio."
            )
        entries.append(entry)
    return tuple(entries)


def load_and_validate_visual_usage(path: Path, expected_episode: str) -> dict[str, object]:
    """Load and canonicalize the file, discarding every unknown field.

    The persistence script serializes this return value rather than the raw JSON,
    so a hand-edited file cannot smuggle paths, URLs or credentials into Git.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("visual_usage.json ausente ou invalido.") from exc
    entries = validate_visual_usage_payload(payload, expected_episode)
    return {
        "schema_version": VISUAL_USAGE_SCHEMA_VERSION,
        "episode": validate_slug(expected_episode),
        "recorded_at": _utc_timestamp(_parse_utc_timestamp(payload["recorded_at"])),
        "visual_usage": [entry.as_dict() for entry in entries],
    }


def _local_source_path(
    resolved_asset_paths: Mapping[str, Path],
    asset: AssetSpec,
) -> Path:
    raw_source = resolved_asset_paths.get(asset.id)
    if raw_source is not None:
        source = Path(raw_source).resolve()
        if source.is_file() and source.stat().st_size > 0:
            return source
    raise RuntimeError(f"Fonte local do asset {asset.id!r} nao esta disponivel.")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise RuntimeError("Horario invalido para visual_usage.json.")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_utc_timestamp(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError("recorded_at ausente em visual_usage.json.")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise RuntimeError("recorded_at invalido em visual_usage.json.") from exc
    if parsed.tzinfo is None:
        raise RuntimeError("recorded_at precisa informar timezone.")
    return parsed.astimezone(timezone.utc)
