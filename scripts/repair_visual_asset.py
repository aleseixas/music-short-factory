from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from engine.visual_repetition import load_visual_history
import resolve_visual_candidates as legacy
import resolve_visual_candidates_web_auth as web_auth


VISUAL_ERROR_CODES = {
    "VISUAL_ASSET_HTTP_403",
    "VISUAL_ASSET_HTTP_404",
    "VISUAL_ASSET_INVALID",
    "MEDIA_PROBE_OR_CODEC_ERROR",
}
QUOTED_FILE_PATTERNS = (
    re.compile(r"asset de (?:imagem|video) ['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"asset ['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"arquivo ['\"]([^'\"]+)['\"]", re.IGNORECASE),
)


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON invalido em {path}: objeto esperado.")
    return data


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _diagnostic_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw_line.startswith("MEDIA_PREFLIGHT_") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key] = value.strip()
    return values


def _extract_failing_file(detail: str) -> str:
    for pattern in QUOTED_FILE_PATTERNS:
        match = pattern.search(detail)
        if match:
            return Path(match.group(1).strip()).name
    return ""


def _asset_kind(asset: dict | None) -> str | None:
    return legacy._asset_kind(asset)


def _resolve_target_slot(assets: list[dict], slots: list[dict], failing_file: str, detail: str) -> str:
    file_name = Path(failing_file).name if failing_file else ""
    file_stem = Path(file_name).stem.casefold() if file_name else ""

    exact: list[str] = []
    stem_matches: list[str] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("id") or "").strip()
        asset_file = Path(str(asset.get("file") or "").strip()).name
        if not asset_id:
            continue
        if file_name and asset_file.casefold() == file_name.casefold():
            exact.append(asset_id)
        elif file_stem and Path(asset_file).stem.casefold() == file_stem:
            stem_matches.append(asset_id)

    if len(exact) == 1:
        return exact[0]
    if len(stem_matches) == 1:
        return stem_matches[0]

    slot_ids = [str(slot.get("id") or "").strip() for slot in slots if isinstance(slot, dict)]
    if file_stem:
        direct = [slot_id for slot_id in slot_ids if slot_id.casefold() == file_stem]
        if len(direct) == 1:
            return direct[0]

    lowered = detail.casefold()
    mentioned = [slot_id for slot_id in slot_ids if slot_id and slot_id.casefold() in lowered]
    if len(mentioned) == 1:
        return mentioned[0]

    raise RuntimeError(
        f"Nao foi possivel identificar de forma unica o slot visual que falhou "
        f"(arquivo={file_name or '<desconhecido>'})."
    )


def _same_asset(left: dict, right: dict) -> bool:
    keys = ("file", "url", "id")
    return all(str(left.get(key) or "") == str(right.get(key) or "") for key in keys)


def _candidate_identity(candidate: dict) -> tuple[str, str]:
    return (
        str(candidate.get("url") or candidate.get("source_page_url") or "").strip(),
        str(candidate.get("file") or "").strip(),
    )


def _select_replacement(project_root: Path, slug: str, slot: dict, current_asset: dict, timeline: dict):
    candidates = slot.get("candidates")
    shots = timeline.get("shots")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError(f"Slot {slot.get('id')} sem candidatos de reparo.")
    if not isinstance(shots, list):
        raise RuntimeError("timeline.json invalido: shots ausente.")

    slot_id = str(slot.get("id") or "").strip()
    current_kind = _asset_kind(current_asset)
    current_url = str(current_asset.get("url") or "").strip()
    current_file = str(current_asset.get("file") or "").strip()

    try:
        history, _warnings = load_visual_history(project_root, exclude_episode=slug)
    except Exception:
        history = ()

    try:
        render = _load_json(project_root / "config" / "config.json").get("render", {})
        output_fps = int(render.get("fps", 30))
        if output_fps <= 0:
            output_fps = 30
    except Exception:
        output_fps = 30

    slot_shots = [
        shot for shot in shots
        if isinstance(shot, dict) and str(shot.get("asset") or "") == slot_id
    ]
    prepared_slot = dict(
        slot,
        _shot=slot_shots[0] if slot_shots else {},
        _output_fps=output_fps,
        _repetition_history=history,
        _episode=slug,
    )

    inspected = []
    failures: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        identity_url, identity_file = _candidate_identity(candidate)
        if current_url and identity_url and identity_url == current_url:
            failures.append(f"{slot_id}[{index}]: candidato atual quebrado ignorado")
            continue
        if not current_url and current_file and identity_file == current_file:
            failures.append(f"{slot_id}[{index}]: candidato local atual quebrado ignorado")
            continue
        try:
            result, inspection, reasons = legacy._inspect_candidate(
                project_root, prepared_slot, candidate, index
            )
            record = legacy._score_record(index, candidate, result, inspection, reasons)
            inspected.append(
                {
                    "index": index,
                    "candidate": candidate,
                    "result": result,
                    "inspection": inspection,
                    "reasons": list(reasons),
                    "record": record,
                    "same_kind": result.kind == current_kind,
                }
            )
            print(
                f"TARGETED_VISUAL_CANDIDATE slot={slot_id} index={index} "
                f"kind={result.kind} score={float(record.get('selection_score') or 0):.1f} "
                f"eligible={'true' if not reasons else 'false'}"
            )
        except Exception as exc:
            failures.append(f"{slot_id}[{index}]: {exc}")
            print(f"::warning::Targeted visual candidate FAIL {slot_id}[{index}]: {exc}")

    if not inspected:
        detail = "; ".join(failures[-5:]) or "nenhum candidato foi inspecionado"
        raise RuntimeError(f"Nenhum candidato visual acessivel para {slot_id}. {detail}")

    def ranked(items: list[dict]) -> list[dict]:
        return sorted(
            items,
            key=lambda item: (
                float(item["record"].get("selection_score") or 0.0),
                -int(item["record"].get("editorial_rank") or item["index"]),
            ),
            reverse=True,
        )

    same_kind_eligible = ranked([item for item in inspected if item["same_kind"] and not item["reasons"]])
    any_eligible = ranked([item for item in inspected if not item["reasons"]])
    same_kind_safe = ranked([
        item for item in inspected
        if item["same_kind"] and "unsafe_trim" not in item["reasons"]
    ])
    any_safe = ranked([item for item in inspected if "unsafe_trim" not in item["reasons"]])

    if same_kind_eligible:
        chosen = same_kind_eligible[0]
        mode = "same_kind_eligible"
    elif any_eligible:
        chosen = any_eligible[0]
        mode = "cross_kind_eligible"
    elif same_kind_safe:
        chosen = same_kind_safe[0]
        mode = "same_kind_safe_fallback"
    elif any_safe:
        chosen = any_safe[0]
        mode = "cross_kind_safe_fallback"
    else:
        raise RuntimeError(f"Nenhum candidato com trim seguro para {slot_id}.")

    return chosen, mode, failures, slot_shots


def repair(project_root: Path, slug: str, diagnostic_log: Path) -> int:
    diagnostics = _diagnostic_values(diagnostic_log)
    error_code = diagnostics.get("MEDIA_PREFLIGHT_ERROR_CODE", "")
    detail = diagnostics.get("MEDIA_PREFLIGHT_ERROR_DETAIL", "")
    if error_code not in VISUAL_ERROR_CODES:
        raise RuntimeError(f"Erro {error_code or '<sem_codigo>'} nao e suportado pelo reparo visual direcionado.")

    episode_dir = project_root / "episodes" / slug
    assets_path = episode_dir / "assets.json"
    timeline_path = episode_dir / "timeline.json"
    pool_path = episode_dir / "visual_candidates.json"

    assets_doc = _load_json(assets_path)
    timeline = _load_json(timeline_path)
    pool = _load_json(pool_path)
    assets = assets_doc.get("assets")
    slots = pool.get("slots")
    if not isinstance(assets, list) or not isinstance(slots, list):
        raise RuntimeError("assets.json ou visual_candidates.json invalido.")

    failing_file = _extract_failing_file(detail)
    target_slot = _resolve_target_slot(assets, slots, failing_file, detail)
    slot = next((entry for entry in slots if isinstance(entry, dict) and entry.get("id") == target_slot), None)
    current_asset = next((entry for entry in assets if isinstance(entry, dict) and entry.get("id") == target_slot), None)
    if slot is None or current_asset is None:
        raise RuntimeError(f"Slot/asset alvo {target_slot} nao encontrado.")

    print(f"VISUAL_REPAIR_TARGET_SLOT={target_slot}")
    print(f"VISUAL_REPAIR_FAILING_FILE={failing_file or '<desconhecido>'}")

    # Install the web resolver plus YouTube PO-token/cookie fallback, but repair only
    # the failing slot instead of rewriting unrelated visuals.
    web_auth.resolver.CURRENT_EPISODE = slug
    web_auth.resolver._install_patches()

    chosen, mode, failures, slot_shots = _select_replacement(
        project_root, slug, slot, current_asset, timeline
    )
    candidate = chosen["candidate"]
    result = chosen["result"]
    inspection = chosen["inspection"]
    new_asset = legacy._asset_entry(target_slot, candidate, result)

    if _same_asset(current_asset, new_asset):
        raise RuntimeError(f"Reparo de {target_slot} selecionou o mesmo asset que falhou.")

    assets_doc["assets"] = [
        new_asset if isinstance(entry, dict) and entry.get("id") == target_slot else entry
        for entry in assets
    ]

    for shot in slot_shots:
        if result.kind == "video":
            start = candidate.get("source_start_seconds")
            end = candidate.get("source_end_seconds")
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

    _write_json(assets_path, assets_doc)
    _write_json(timeline_path, timeline)
    report = {
        "schema_version": 1,
        "episode": slug,
        "error_code": error_code,
        "error_detail": detail,
        "target_slot": target_slot,
        "failing_file": failing_file,
        "selection_mode": mode,
        "selected_kind": result.kind,
        "selected_name": result.name,
        "selected_file": new_asset.get("file"),
        "selected_url": new_asset.get("url"),
        "visual_score": chosen["record"].get("visual_score"),
        "selection_score": chosen["record"].get("selection_score"),
        "inspection_failures": failures,
    }
    _write_json(episode_dir / "visual_auto_repair_report.json", report)

    print(f"VISUAL_REPAIR_CHANGED=true")
    print(f"VISUAL_REPAIR_SELECTED_KIND={result.kind}")
    print(f"VISUAL_REPAIR_SELECTED_FILE={new_asset.get('file') or ''}")
    print(f"VISUAL_REPAIR_MODE={mode}")
    print(
        f"Targeted visual repair applied: {target_slot} -> "
        f"{result.name} ({result.kind}, score={float(chosen['record'].get('selection_score') or 0):.1f})."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repara somente o asset visual que causou um media preflight estruturado."
    )
    parser.add_argument("episode", help="Slug do episodio")
    parser.add_argument("--diagnostic-log", required=True, help="Log da tentativa de media preflight que falhou")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    try:
        return repair(project_root, args.episode, Path(args.diagnostic_log))
    except Exception as exc:
        print(f"::warning::Targeted visual repair failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
