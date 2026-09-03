from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from engine.visual_search import VisualSearchError, VisualSearchResult, inspect_visual_result


APPROVED_VISUAL_HOSTS = frozenset({"upload.wikimedia.org", "live.staticflickr.com"})
DEFAULT_REQUIRED_SECONDS = 8.0
DEFAULT_CROSSFADE_SECONDS = 0.35
MIN_IMAGE_SCORE = 35.0
MIN_VIDEO_SCORE = 45.0


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Nao foi possivel ler {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON invalido em {path}: objeto esperado.")
    return data


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _candidate_result(candidate: dict, slot_id: str, index: int) -> VisualSearchResult:
    url = str(candidate.get("url") or "").strip()
    if not url.startswith("https://"):
        raise RuntimeError(f"{slot_id}[{index}]: url HTTPS direta obrigatoria.")
    host = (urlparse(url).hostname or "").casefold()
    if host not in APPROVED_VISUAL_HOSTS:
        raise RuntimeError(
            f"{slot_id}[{index}]: host visual nao aprovado: {host or '<vazio>'}."
        )

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
    focus = candidate.get("focus")
    if not isinstance(focus, dict):
        focus = {"x": 0.5, "y": 0.5}
    return {
        "id": slot_id,
        "file": file_name,
        "url": result.download_url,
        "credit": str(candidate.get("credit") or result.attribution or result.creator or result.source),
        "license": result.license,
        "focus": focus,
    }


def _score_candidate(project_root: Path, slot: dict, candidate: dict, index: int):
    slot_id = str(slot.get("id") or "").strip()
    result = _candidate_result(candidate, slot_id, index)
    required = slot.get("required_seconds", DEFAULT_REQUIRED_SECONDS)
    crossfade = slot.get("crossfade_seconds", DEFAULT_CROSSFADE_SECONDS)
    start = candidate.get("source_start_seconds", 0.0)
    end = candidate.get("source_end_seconds")

    inspection = inspect_visual_result(
        project_root,
        result,
        shot_duration_seconds=float(required) if result.kind == "video" else None,
        source_start_seconds=float(start) if result.kind == "video" else 0.0,
        source_end_seconds=float(end) if result.kind == "video" and end is not None else None,
        crossfade_seconds=float(crossfade) if result.kind == "video" else 0.0,
    )
    minimum = slot.get(
        "min_visual_score",
        MIN_VIDEO_SCORE if result.kind == "video" else MIN_IMAGE_SCORE,
    )
    if inspection.visual_score < float(minimum):
        raise VisualSearchError(
            f"visual_score {inspection.visual_score:.1f} abaixo do minimo {float(minimum):.1f}"
        )
    if result.kind == "video":
        if inspection.trim and inspection.trim.safe_for_shot is False:
            raise VisualSearchError("trim insuficiente para o shot sem loop")
        if inspection.is_practically_static is True:
            raise VisualSearchError("video praticamente estatico")
    return result, inspection


def resolve_episode(project_root: Path, slug: str) -> int:
    episode_dir = project_root / "episodes" / slug
    pool_path = episode_dir / "visual_candidates.json"
    if not pool_path.exists():
        print(f"Visual candidates: {slug} sem pool; mantendo assets existentes.")
        return 0

    pool = _load_json(pool_path)
    slots = pool.get("slots")
    if pool.get("schema_version") != 1 or not isinstance(slots, list) or not slots:
        raise RuntimeError("visual_candidates.json invalido: schema_version=1 e slots obrigatorios.")

    assets_path = episode_dir / "assets.json"
    timeline_path = episode_dir / "timeline.json"
    assets = _load_json(assets_path)
    timeline = _load_json(timeline_path)
    original_assets = assets.get("assets")
    shots = timeline.get("shots")
    if not isinstance(original_assets, list) or not isinstance(shots, list):
        raise RuntimeError("assets.json ou timeline.json invalido para resolucao visual.")

    resolved_entries: dict[str, dict] = {}
    selections: dict[str, dict] = {}
    failures: list[str] = []

    for slot in slots:
        if not isinstance(slot, dict):
            raise RuntimeError("Slot visual invalido.")
        slot_id = str(slot.get("id") or "").strip()
        candidates = slot.get("candidates")
        if not slot_id or not isinstance(candidates, list) or not candidates:
            raise RuntimeError("Cada slot visual precisa de id e candidates.")

        ranked = []
        for index, candidate in enumerate(candidates, start=1):
            if not isinstance(candidate, dict):
                failures.append(f"{slot_id}[{index}]: candidato invalido")
                continue
            try:
                result, inspection = _score_candidate(project_root, slot, candidate, index)
                editorial_rank = candidate.get("editorial_rank", index)
                try:
                    editorial_rank = int(editorial_rank)
                except (TypeError, ValueError):
                    editorial_rank = index
                ranked.append((inspection.visual_score, -editorial_rank, candidate, result, inspection))
                print(
                    f"Visual candidate OK {slot_id}[{index}]: "
                    f"{result.kind} score={inspection.visual_score:.1f}"
                )
            except Exception as exc:
                failures.append(f"{slot_id}[{index}]: {exc}")
                print(f"Visual candidate FAIL {slot_id}[{index}]: {exc}")

        if not ranked:
            details = "; ".join(item for item in failures if item.startswith(f"{slot_id}["))
            raise RuntimeError(f"Nenhum candidato visual valido para {slot_id}. {details}")

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        score, _rank, candidate, result, inspection = ranked[0]
        resolved_entries[slot_id] = _asset_entry(slot_id, candidate, result)
        selections[slot_id] = {
            "kind": result.kind,
            "visual_score": score,
            "width": inspection.width,
            "height": inspection.height,
            "duration_seconds": inspection.duration_seconds,
            "fps": inspection.fps,
            "opening_motion_score": inspection.opening_motion_score,
            "motion_score": inspection.motion_score,
            "practically_static": inspection.is_practically_static,
            "source_start_seconds": candidate.get("source_start_seconds"),
            "source_end_seconds": candidate.get("source_end_seconds"),
        }
        print(f"Visual selected {slot_id}: {result.name} score={score:.1f}")

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
        if not selected:
            continue
        if selected["kind"] == "video":
            start = selected.get("source_start_seconds")
            end = selected.get("source_end_seconds")
            if start is not None:
                shot["source_start_seconds"] = float(start)
            else:
                shot.setdefault("source_start_seconds", 0.0)
            if end is not None:
                shot["source_end_seconds"] = float(end)
        else:
            shot.pop("source_start_seconds", None)
            shot.pop("source_end_seconds", None)
            shot.pop("speed", None)

    _write_json(assets_path, assets)
    _write_json(timeline_path, timeline)
    report_path = episode_dir / "visual_resolution_report.json"
    _write_json(
        report_path,
        {
            "schema_version": 1,
            "episode": slug,
            "selections": selections,
            "rejected_candidates": failures,
        },
    )
    print(f"Visual resolution concluida: {len(selections)} slots selecionados.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspeciona pools visuais e resolve o melhor candidato tecnico por slot."
    )
    parser.add_argument("episode", help="Slug do episodio")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent
    return resolve_episode(project_root, args.episode)


if __name__ == "__main__":
    raise SystemExit(main())
