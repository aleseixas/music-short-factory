from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from engine.visual_search import VisualSearchResult, assess_trim, inspect_visual_result
from engine.visual_repetition import (
    VisualFingerprint,
    assess_repetition,
    canonicalize_visual_url,
    fingerprint_to_usage,
    load_visual_history,
)


APPROVED_VISUAL_HOSTS = frozenset({"upload.wikimedia.org", "live.staticflickr.com"})
DEFAULT_REQUIRED_SECONDS = 8.0
DEFAULT_CROSSFADE_SECONDS = 0.35
DEFAULT_INSPECT_TOP = 4
MAX_INSPECT_TOP = 8
MIN_IMAGE_SCORE = 35.0
MIN_VIDEO_SCORE = 45.0
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"})
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON invalido em {path}: objeto esperado.")
    return data


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _number(value, default=0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _asset_kind(asset: dict | None) -> str | None:
    if not isinstance(asset, dict):
        return None

    explicit = str(asset.get("kind") or "").strip().lower()
    if explicit in {"image", "video"}:
        return explicit

    file_name = str(asset.get("file") or "").strip()
    url = str(asset.get("url") or "").strip()
    suffix = Path(file_name or urlparse(url).path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return None


def _metadata_priority(candidate: dict, index: int) -> tuple[float, float, float, float]:
    """Cheap pre-ranking: decide which candidates deserve download/FFmpeg inspection."""
    width = _number(candidate.get("width"))
    height = _number(candidate.get("height"))
    pixels = width * height
    ratio = width / height if width > 0 and height > 0 else 0.0
    portrait_fit = max(0.0, 1.0 - abs(ratio - (9 / 16))) if ratio else 0.0
    kind_bonus = 1.0 if str(candidate.get("kind") or "").lower() == "video" else 0.0
    editorial_rank = max(1.0, _number(candidate.get("editorial_rank"), index))
    return (kind_bonus, portrait_fit, pixels, -editorial_rank)


def _candidate_source_key(candidate: dict) -> str:
    """Return a credential-free identity used only to reserve a selected source."""
    kind = str(candidate.get("kind") or "").strip().casefold()
    provider = str(candidate.get("search_provider") or "").strip().casefold()
    provider_id = str(candidate.get("provider_id") or "").strip()
    raw_url = str(candidate.get("source_page_url") or candidate.get("url") or "").strip()
    host = (urlparse(raw_url).hostname or "").casefold()
    if kind == "video" and provider_id and (provider == "youtube_web" or host in YOUTUBE_HOSTS):
        return f"video:youtube:{provider_id}"
    canonical = canonicalize_visual_url(raw_url)
    return f"{kind}:url:{canonical}" if kind and canonical else ""


def _safe_log_text(value: object, fallback: str = "unknown") -> str:
    parts: list[str] = []
    current = value if isinstance(value, BaseException) else None
    if current is None:
        parts.append(str(value or ""))
    else:
        seen: set[int] = set()
        while current is not None and id(current) not in seen and len(parts) < 3:
            seen.add(id(current))
            detail = str(current or "").strip()
            if detail and detail not in parts:
                parts.append(detail)
            current = current.__cause__ or current.__context__
    text = " | caused_by: ".join(parts)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()
    text = re.sub(r"https?://\S+", "<url>", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?[^\s,;|]+",
        "authorization=<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)\bcookies?\s*[:=]\s*[^;\s,|]+"
        r"(?:\s*[;,]\s*[^;\s,|]+)*",
        "cookie=<redacted>",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[^\s,;|]+", "Bearer <redacted>", text)
    text = re.sub(
        r"(?i)\b(access[_ -]?token|password|secret|po[_ -]?token)"
        r"\s*[:=]\s*[^\s,;|]+",
        r"\1=<redacted>",
        text,
    )
    return (text or fallback)[:500]


def _candidate_log_reference(candidate: dict, index: int) -> tuple[str, str, str]:
    provider_id = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        str(candidate.get("provider_id") or index),
    ).strip("._")[:100] or str(index)
    raw_url = str(candidate.get("source_page_url") or candidate.get("url") or "").strip()
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").casefold()
    is_youtube = (
        str(candidate.get("search_provider") or "").strip().casefold() == "youtube_web"
        or host in YOUTUBE_HOSTS
    )
    if is_youtube:
        safe_url = f"https://www.youtube.com/watch?v={provider_id}"
        downloader = "yt-dlp"
    elif parsed.scheme.casefold() == "https" and parsed.netloc:
        safe_url = f"https://{parsed.netloc}{parsed.path}"
        downloader = "direct-cache"
    else:
        safe_url = "<invalid>"
        downloader = "unknown"
    return provider_id, safe_url, downloader


def _shortlist_candidates(
    indexed: list[tuple[int, dict]],
    inspect_top: int,
    *,
    prefer_video_candidates: bool,
) -> list[tuple[int, dict]]:
    if not prefer_video_candidates:
        return indexed[:inspect_top]

    # In the web resolver inspect_top is a per-kind ceiling. This guarantees that
    # video failures cannot hide the image fallback (or vice versa), while keeping
    # the video shortlist at its full authored size.
    videos = [pair for pair in indexed if str(pair[1].get("kind") or "").casefold() == "video"]
    images = [pair for pair in indexed if str(pair[1].get("kind") or "").casefold() == "image"]
    return videos[:inspect_top] + images[:inspect_top]


def _candidate_result(candidate: dict, slot_id: str, index: int) -> VisualSearchResult:
    url = str(candidate.get("url") or "").strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"{slot_id}[{index}]: url HTTPS direta obrigatoria.")
    host = (urlparse(url).hostname or "").casefold()
    if host not in APPROVED_VISUAL_HOSTS:
        raise RuntimeError(f"{slot_id}[{index}]: host visual nao aprovado: {host or '<vazio>'}.")

    kind = str(candidate.get("kind") or "").strip().lower()
    if kind not in {"image", "video"}:
        raise RuntimeError(f"{slot_id}[{index}]: kind deve ser image ou video.")

    file_name = str(candidate.get("file") or "").strip()
    suffix = Path(file_name or urlparse(url).path).suffix.lower().lstrip(".")
    if not suffix:
        raise RuntimeError(f"{slot_id}[{index}]: extensao de arquivo ausente.")

    width = candidate.get("width")
    height = candidate.get("height")
    duration = candidate.get("duration_seconds")
    return VisualSearchResult(
        provider_id=str(candidate.get("provider_id") or f"{slot_id}-{index}"),
        name=str(candidate.get("name") or file_name or f"{slot_id}-{index}"),
        kind=kind,
        source=str(candidate.get("source") or "authoring-pool"),
        source_page_url=str(candidate.get("source_page_url") or ""),
        creator=str(candidate.get("creator") or ""),
        license=str(candidate.get("license") or ""),
        license_url=str(candidate.get("license_url") or ""),
        attribution=str(candidate.get("credit") or candidate.get("attribution") or ""),
        search_provider=str(candidate.get("search_provider") or "authoring-pool"),
        description=str(candidate.get("description") or ""),
        width=int(width) if isinstance(width, (int, float)) and not isinstance(width, bool) else None,
        height=int(height) if isinstance(height, (int, float)) and not isinstance(height, bool) else None,
        duration_seconds=float(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None,
        file_format=suffix,
        mime_type=str(candidate.get("mime_type") or "") or None,
        thumbnail_url=str(candidate.get("thumbnail_url") or "") or None,
        download_url=url,
        allowed_download_hosts=(host,),
    )


def _asset_entry(slot_id: str, candidate: dict, result: VisualSearchResult) -> dict:
    file_name = str(candidate.get("file") or result.suggested_file or f"{slot_id}.{result.file_format}")
    focus = candidate.get("focus") if isinstance(candidate.get("focus"), dict) else {"x": 0.5, "y": 0.5}
    return {
        "id": slot_id,
        "file": file_name,
        "url": result.download_url,
        "credit": str(candidate.get("credit") or result.attribution or result.creator or result.source),
        "license": result.license,
        "focus": focus,
    }


def _inspect_candidate(project_root: Path, slot: dict, candidate: dict, index: int):
    slot_id = str(slot.get("id") or "").strip()
    result = _candidate_result(candidate, slot_id, index)
    required = _number(slot.get("required_seconds"), DEFAULT_REQUIRED_SECONDS)
    crossfade = _number(slot.get("crossfade_seconds"), DEFAULT_CROSSFADE_SECONDS)
    start = _number(candidate.get("source_start_seconds"), 0.0)
    end = candidate.get("source_end_seconds")

    inspection = inspect_visual_result(
        project_root,
        result,
        shot_duration_seconds=required if result.kind == "video" else None,
        source_start_seconds=start if result.kind == "video" else 0.0,
        source_end_seconds=_number(end) if result.kind == "video" and end is not None else None,
        crossfade_seconds=crossfade if result.kind == "video" else 0.0,
        **_playback_options(slot, result.kind),
        repetition_history=slot.get("_repetition_history"),
        exclude_episode=slot.get("_episode"),
    )

    minimum = _number(
        slot.get("min_visual_score"),
        MIN_VIDEO_SCORE if result.kind == "video" else MIN_IMAGE_SCORE,
    )
    reasons: list[str] = []
    if inspection.visual_score < minimum:
        reasons.append(f"score_below_{minimum:.1f}")
    if result.kind == "video":
        if inspection.trim and inspection.trim.safe_for_shot is False:
            reasons.append("unsafe_trim")
        if inspection.is_practically_static is True:
            reasons.append("practically_static")

    return result, inspection, reasons


def _score_record(index: int, candidate: dict, result: VisualSearchResult, inspection, reasons: list[str]) -> dict:
    return {
        "candidate_index": index,
        "name": result.name,
        "kind": result.kind,
        "visual_score": inspection.visual_score,
        "selection_score": inspection.selection_score,
        "repetition": inspection.repetition.as_dict() if inspection.repetition else None,
        "downgraded_for_repetition": bool(inspection.repetition and inspection.repetition.is_repeated),
        "width": inspection.width,
        "height": inspection.height,
        "duration_seconds": inspection.duration_seconds,
        "fps": inspection.fps,
        "opening_motion_score": inspection.opening_motion_score,
        "motion_score": inspection.motion_score,
        "practically_static": inspection.is_practically_static,
        "trim_safe": inspection.trim_safe,
        "eligible_for_auto_selection": not reasons,
        "warnings": reasons + list(inspection.warnings),
        "editorial_rank": int(max(1, _number(candidate.get("editorial_rank"), index))),
    }


def _playback_options(slot: dict, kind: str) -> dict:
    """Keep inspection tied to the playback contract of the actual timeline shot."""
    shot = slot.get("_shot", {}) if kind == "video" else {}
    freeze = shot.get("freeze_frame") or {}
    return {
        "speed": shot.get("speed", 1.0),
        "freeze_start_seconds": freeze.get("start_seconds"),
        "freeze_duration_seconds": freeze.get("duration_seconds"),
        "output_fps": slot.get("_output_fps", 30),
    }


def _url_repetition(candidate: dict, slot: dict, history):
    kind = str(candidate.get("kind") or "").lower()
    duration = None
    start = _number(candidate.get("source_start_seconds"), 0.0) if kind == "video" else 0.0
    if kind == "video":
        assessment = assess_trim(
            1e12,
            shot_duration_seconds=_number(slot.get("required_seconds"), DEFAULT_REQUIRED_SECONDS),
            source_start_seconds=start,
            crossfade_seconds=_number(slot.get("crossfade_seconds"), DEFAULT_CROSSFADE_SECONDS),
            **_playback_options(slot, kind),
        )
        duration = assessment.required_seconds
    identity = candidate.get("url") or candidate.get("source_page_url")
    fingerprint = VisualFingerprint.from_dict({
        "kind": kind,
        "urls": [identity] if identity else [],
        "source_start_seconds": start,
        "source_duration_seconds": duration,
    })
    return assess_repetition(fingerprint, history)


def resolve_episode(
    project_root: Path,
    slug: str,
    *,
    prefer_video_candidates: bool = False,
) -> int:
    episode_dir = project_root / "episodes" / slug
    pool_path = episode_dir / "visual_candidates.json"
    if not pool_path.exists():
        print(f"Visual candidates: {slug} sem pool; mantendo assets existentes.")
        return 0

    try:
        pool = _load_json(pool_path)
        slots = pool.get("slots")
        if pool.get("schema_version") != 1 or not isinstance(slots, list) or not slots:
            raise RuntimeError("schema_version=1 e slots obrigatorios")

        assets_path = episode_dir / "assets.json"
        timeline_path = episode_dir / "timeline.json"
        assets = _load_json(assets_path)
        timeline = _load_json(timeline_path)
        original_assets = assets.get("assets")
        shots = timeline.get("shots")
        if not isinstance(original_assets, list) or not isinstance(shots, list):
            raise RuntimeError("assets.json ou timeline.json invalido")
    except Exception as exc:
        print(f"::warning::Visual candidate resolver ignorado: {exc}. Render continuara com assets existentes.")
        return 0

    original_assets_by_id = {
        str(entry.get("id")): entry
        for entry in original_assets
        if isinstance(entry, dict) and entry.get("id")
    }

    resolved_entries: dict[str, dict] = {}
    selections: dict[str, dict] = {}
    candidate_scores: dict[str, list[dict]] = {}
    failures: list[str] = []
    recorded_at = datetime.now(timezone.utc).isoformat()
    visual_usage: list[dict] = []
    reserved_candidate_sources: set[str] = set()
    try:
        history, history_warnings = load_visual_history(project_root, exclude_episode=slug)
    except Exception:
        history, history_warnings = (), ("Historico visual indisponivel; ranking tecnico mantido.",)
    try:
        render_config = _load_json(project_root / "config" / "config.json").get("render", {})
        output_fps = int(render_config.get("fps", 30))
        if output_fps <= 0:
            output_fps = 30
    except (OSError, ValueError, TypeError, RuntimeError):
        output_fps = 30

    for slot in slots:
        if not isinstance(slot, dict):
            failures.append("slot invalido ignorado")
            continue
        slot_id = str(slot.get("id") or "").strip()
        candidates = slot.get("candidates")
        if not slot_id or not isinstance(candidates, list) or not candidates:
            failures.append(f"{slot_id or '<sem_id>'}: slot sem candidatos; mantendo asset existente")
            continue

        current_asset = original_assets_by_id.get(slot_id)
        slot_shots = [shot for shot in shots if isinstance(shot, dict) and shot.get("asset") == slot_id]
        # The final post-render history records each real shot separately. Here
        # the authored slot duration provides the candidate-selection estimate.
        slot = dict(slot, _shot=slot_shots[0] if slot_shots else {},
                    _output_fps=output_fps, _repetition_history=history, _episode=slug)
        target_kind = _asset_kind(current_asset)
        if target_kind not in {"image", "video"}:
            failures.append(f"{slot_id}: tipo do asset atual nao identificado; mantendo asset existente")
            selections[slot_id] = {
                "status": "kept_existing_asset",
                "reason": "unknown_existing_asset_kind",
            }
            print(f"::warning::Visual slot {slot_id}: tipo atual desconhecido; mantendo asset existente.")
            continue

        inspect_top = int(max(1, min(MAX_INSPECT_TOP, _number(slot.get("inspect_top"), DEFAULT_INSPECT_TOP))))
        allowed_kinds = {"video", "image"} if prefer_video_candidates else {target_kind}
        indexed = [
            (index, candidate)
            for index, candidate in enumerate(candidates, start=1)
            if isinstance(candidate, dict)
            and str(candidate.get("kind") or "").strip().lower() in allowed_kinds
        ]

        if not indexed:
            candidate_scores[slot_id] = []
            selections[slot_id] = {
                "status": "kept_existing_asset",
                "kind": target_kind,
                "reason": (
                    "no_supported_candidate"
                    if prefer_video_candidates
                    else "no_same_kind_candidate"
                ),
            }
            print(
                f"::warning::Visual slot {slot_id}: nenhum candidato suportado; "
                "mantendo asset existente."
            )
            continue

        preselection_repetition = {}
        for index, candidate in indexed:
            try:
                preselection_repetition[index] = _url_repetition(candidate, slot, history)
            except Exception:
                pass
        indexed.sort(key=lambda pair: (
            preselection_repetition[pair[0]].penalty if pair[0] in preselection_repetition else 0.0,
            *_metadata_priority(pair[1], pair[0]),
        ), reverse=True)
        shortlist = _shortlist_candidates(
            indexed,
            inspect_top,
            prefer_video_candidates=prefer_video_candidates,
        )
        available_video = sum(
            str(candidate.get("kind") or "").casefold() == "video"
            for _index, candidate in indexed
        )
        available_image = sum(
            str(candidate.get("kind") or "").casefold() == "image"
            for _index, candidate in indexed
        )
        inspect_video = sum(
            str(candidate.get("kind") or "").casefold() == "video"
            for _index, candidate in shortlist
        )
        inspect_image = len(shortlist) - inspect_video
        print(
            f"Visual slot {slot_id}: {len(candidates)} candidatos totais "
            f"(video={available_video}, image={available_image}); plano de inspecao "
            f"video={inspect_video}, image={inspect_image}, inspect_top={inspect_top}"
            + (" por tipo, prioridade=video." if prefer_video_candidates else "."),
            flush=True,
        )

        inspected = []
        skipped_records: list[dict] = []
        for index, candidate in shortlist:
            candidate_kind = str(candidate.get("kind") or "").strip().casefold()
            provider_id, safe_url, downloader = _candidate_log_reference(candidate, index)
            source_key = _candidate_source_key(candidate)
            if prefer_video_candidates and source_key and source_key in reserved_candidate_sources:
                skipped_records.append({
                    "candidate_index": index,
                    "name": str(candidate.get("name") or ""),
                    "kind": candidate_kind,
                    "visual_score": None,
                    "selection_score": None,
                    "inspection_status": "skipped_duplicate_in_current_resolution",
                    "eligible_for_auto_selection": False,
                    "warnings": ["duplicate_in_current_resolution"],
                })
                if candidate_kind == "video":
                    print(
                        f"VIDEO_RESULT slot={slot_id} candidate={index} id={provider_id} "
                        f"downloader={downloader} acquisition=NOT_RUN status=SKIPPED "
                        "reason=duplicate_in_current_resolution",
                        flush=True,
                    )
                continue
            if candidate_kind == "video":
                print(
                    f"VIDEO_ATTEMPT slot={slot_id} candidate={index} id={provider_id} "
                    f"url={safe_url} downloader={downloader}",
                    flush=True,
                )
            try:
                result, inspection, reasons = _inspect_candidate(project_root, slot, candidate, index)
                record = _score_record(index, candidate, result, inspection, reasons)
                inspected.append((record["selection_score"], -record["editorial_rank"], candidate, result, inspection, reasons, record))
                status = "ELIGIBLE" if not reasons else "SCORED_ONLY"
                print(f"Visual candidate {status} {slot_id}[{index}]: {result.kind} score={inspection.visual_score:.1f}")
                if candidate_kind == "video":
                    gate_reasons = ",".join(reasons) if reasons else "none"
                    print(
                        f"VIDEO_RESULT slot={slot_id} candidate={index} id={provider_id} "
                        f"downloader={downloader} acquisition=SUCCESS "
                        f"status={status} score={inspection.visual_score:.1f} "
                        f"trim_safe={inspection.trim_safe} reasons={gate_reasons}",
                        flush=True,
                    )
            except Exception as exc:
                reason = _safe_log_text(exc)
                failures.append(f"{slot_id}[{index}]: {reason}")
                if candidate_kind == "video":
                    print(
                        f"::warning::VIDEO_RESULT slot={slot_id} candidate={index} "
                        f"id={provider_id} downloader={downloader} acquisition=FAIL "
                        f"status=FAIL reason={reason}",
                        flush=True,
                    )
                else:
                    print(f"::warning::Visual candidate FAIL {slot_id}[{index}]: {reason}")

        inspected.sort(
            key=lambda item: (
                1
                if prefer_video_candidates and item[3].kind == "video" and not item[5]
                else 0,
                item[0],
                item[1],
            ),
            reverse=True,
        )
        candidate_scores[slot_id] = [item[6] for item in inspected] + skipped_records
        shortlisted = {index for index, _candidate in shortlist}
        candidate_scores[slot_id].extend({
            "candidate_index": index,
            "name": str(candidate.get("name") or ""),
            "kind": str(candidate.get("kind") or "").strip().casefold(),
            "visual_score": None,
            "selection_score": None,
            "inspection_status": "outside_shortlist",
            "repetition": preselection_repetition[index].as_dict(),
            "downgraded_for_repetition": True,
        } for index, candidate in indexed
          if index not in shortlisted and index in preselection_repetition
          and preselection_repetition[index].is_repeated)
        eligible = [item for item in inspected if not item[5]]

        selection_mode = "eligible"
        if eligible:
            chosen = eligible[0]
        else:
            # Score baixo ou video praticamente estatico nao devem zerar o slot.
            # Mantemos apenas a trava tecnica de trim inseguro para evitar quebra no render.
            safe_fallback = [item for item in inspected if "unsafe_trim" not in item[5]]
            if not safe_fallback:
                best_score = inspected[0][0] if inspected else None
                selections[slot_id] = {
                    "status": "kept_existing_asset",
                    "kind": target_kind,
                    "visual_score": best_score,
                    "reason": (
                        "no_render_safe_candidate"
                        if prefer_video_candidates
                        else "no_render_safe_same_kind_candidate"
                    ),
                }
                if best_score is None:
                    print(f"::warning::Visual slot {slot_id}: nenhum candidato {target_kind} inspecionado; mantendo asset existente.")
                else:
                    print(
                        f"::warning::Visual slot {slot_id}: candidatos {target_kind} avaliados, "
                        "mas nenhum tem trim seguro; mantendo asset existente."
                    )
                continue

            chosen = safe_fallback[0]
            selection_mode = "best_same_kind_fallback"
            print(
                f"::warning::Visual slot {slot_id}: nenhum candidato {target_kind} atingiu todos os gates; "
                "usando o melhor candidato do mesmo tipo para nao perder o slot."
            )

        score, _rank, candidate, result, inspection, reasons, _record = chosen
        resolved_entries[slot_id] = _asset_entry(slot_id, candidate, result)
        source_key = _candidate_source_key(candidate)
        if source_key:
            reserved_candidate_sources.add(source_key)
        selections[slot_id] = {
            "status": "selected",
            "selection_mode": selection_mode,
            "kind": result.kind,
            "name": result.name,
            "visual_score": _record["visual_score"],
            "selection_score": score,
            "repetition": _record.get("repetition"),
            "downgraded_for_repetition": _record.get("downgraded_for_repetition", False),
            "width": inspection.width,
            "height": inspection.height,
            "duration_seconds": inspection.duration_seconds,
            "fps": inspection.fps,
            "opening_motion_score": inspection.opening_motion_score,
            "motion_score": inspection.motion_score,
            "practically_static": inspection.is_practically_static,
            "warnings": reasons + list(inspection.warnings),
            "source_start_seconds": candidate.get("source_start_seconds"),
            "source_end_seconds": candidate.get("source_end_seconds"),
        }
        if inspection.fingerprint is not None:
            for shot in slot_shots:
                visual_usage.append(fingerprint_to_usage(
                    inspection.fingerprint, slug, str(shot.get("id") or slot_id),
                    slot_id, recorded_at=recorded_at,
                ))
        suffix = "" if selection_mode == "eligible" else " (fallback do mesmo tipo)"
        print(f"Visual selected {slot_id}: {result.name} score={score:.1f}{suffix}")
        if result.kind == "video":
            provider_id, safe_url, _downloader = _candidate_log_reference(
                candidate,
                int(_record["candidate_index"]),
            )
            print(
                f"VIDEO_SELECTED slot={slot_id} id={provider_id} url={safe_url} "
                f"score={score:.1f} "
                f"file={_safe_log_text(resolved_entries[slot_id].get('file'), '<unknown>')}",
                flush=True,
            )

    replaced_ids = set(resolved_entries)
    assets["assets"] = [
        entry for entry in original_assets
        if not isinstance(entry, dict) or entry.get("id") not in replaced_ids
    ] + [resolved_entries[slot_id] for slot_id in resolved_entries]

    for shot in shots:
        if not isinstance(shot, dict):
            continue
        slot_id = str(shot.get("asset") or "")
        selected = selections.get(slot_id)
        if not selected or selected.get("status") != "selected":
            continue
        if selected["kind"] == "video":
            start = selected.get("source_start_seconds")
            end = selected.get("source_end_seconds")
            shot["source_start_seconds"] = float(start) if start is not None else 0.0
            if end is not None:
                shot["source_end_seconds"] = float(end)
            else:
                shot.pop("source_end_seconds", None)
        else:
            shot.pop("source_start_seconds", None)
            shot.pop("source_end_seconds", None)
            shot.pop("speed", None)
            shot.pop("freeze_frame", None)

    _write_json(assets_path, assets)
    _write_json(timeline_path, timeline)
    report = {
        "schema_version": 1,
        "episode": slug,
        "recorded_at": recorded_at,
        "visual_usage_stage": "candidate_resolution",
        "visual_usage": visual_usage,
        "repetition_history_warnings": list(history_warnings),
        "selections": selections,
        "candidate_scores": candidate_scores,
        "inspection_failures": failures,
    }
    _write_json(episode_dir / "visual_resolution_report.json", report)

    print("\n=== VISUAL SCORE SUMMARY ===")
    for slot_id, scores in candidate_scores.items():
        if not scores:
            print(f"{slot_id}: sem score tecnico")
            continue
        best = next((item for item in scores if item.get("visual_score") is not None), None)
        if best is None:
            print(f"{slot_id}: apenas avaliacao de repeticao por URL")
            continue
        selected = selections.get(slot_id, {})
        if selected.get("status") == "selected":
            mode = selected.get("selection_mode")
            suffix = "selecionado" if mode == "eligible" else "selecionado como melhor fallback do mesmo tipo"
        else:
            suffix = "apenas informativo"
        print(f"{slot_id}: melhor={best['visual_score']:.1f} ({best['name']}, {suffix})")
    print(f"Visual resolution concluida: {len(resolved_entries)} slots substituidos; demais mantidos sem bloquear render.")
    return 0


def main(*, prefer_video_candidates: bool = False) -> int:
    parser = argparse.ArgumentParser(description="Inspeciona pools visuais, informa scores e resolve candidatos sem bloquear o render.")
    parser.add_argument("episode", help="Slug do episodio")
    args = parser.parse_args()
    return resolve_episode(
        Path(__file__).resolve().parent,
        args.episode,
        prefer_video_candidates=prefer_video_candidates,
    )


if __name__ == "__main__":
    raise SystemExit(main())
