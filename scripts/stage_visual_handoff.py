from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse


VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"})
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON invalido no handoff visual: {path}")
    return data


def _asset_kind(asset: dict | None) -> str | None:
    if not isinstance(asset, dict):
        return None
    explicit = str(asset.get("kind") or "").strip().casefold()
    if explicit in {"image", "video"}:
        return explicit
    raw = str(asset.get("file") or asset.get("url") or "").strip()
    suffix = Path(urlparse(raw).path if "://" in raw else raw).suffix.casefold()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return None


def _final_asset_paths(episode_rel: Path, episode_dir: Path) -> set[Path]:
    assets_data = _load_json(episode_dir / "assets.json")
    assets = assets_data.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("VISUAL_HANDOFF_INVALID_ASSETS: assets.json sem lista assets valida.")

    final_paths: set[Path] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        raw_file = str(asset.get("file") or "").strip()
        if not raw_file:
            continue

        asset_path = Path(raw_file)
        if asset_path.is_absolute() or ".." in asset_path.parts:
            raise RuntimeError(
                "VISUAL_HANDOFF_INVALID_ASSET_PATH: caminho de asset inseguro em assets.json: "
                f"{raw_file}"
            )

        if asset_path.parts and asset_path.parts[0] == "assets":
            episode_asset_rel = asset_path
        else:
            episode_asset_rel = Path("assets") / asset_path

        final_paths.add(episode_rel / episode_asset_rel)

    return final_paths


def _validate_final_video_semantics(episode_dir: Path) -> None:
    """Never hand an unverified/generic video to the publish runner.

    The web-auth resolver now accepts video only when semantic_fit is direct/exact.
    Therefore every final video used by a shot must have a fresh successful selection
    in the current visual_resolution_report. Existing/base videos are not trusted as
    fallbacks: if resolution fails, a normal slot should stay/use an image instead;
    the opening must keep searching for a relevant direct/exact video.
    """
    assets_path = episode_dir / "assets.json"
    timeline_path = episode_dir / "timeline.json"
    report_path = episode_dir / "visual_resolution_report.json"
    if not assets_path.is_file() or not timeline_path.is_file() or not report_path.is_file():
        raise RuntimeError(
            "FINAL_VISUAL_SEMANTIC_GATE: assets.json, timeline.json e "
            "visual_resolution_report.json sao obrigatorios antes do handoff."
        )

    assets_data = _load_json(assets_path)
    timeline = _load_json(timeline_path)
    report = _load_json(report_path)
    assets = assets_data.get("assets")
    shots = timeline.get("shots")
    selections = report.get("selections")
    if not isinstance(assets, list) or not isinstance(shots, list) or not isinstance(selections, dict):
        raise RuntimeError("FINAL_VISUAL_SEMANTIC_GATE: payload visual final invalido.")

    assets_by_id = {
        str(asset.get("id")): asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("id")
    }
    real_shots = [shot for shot in shots if isinstance(shot, dict) and shot.get("asset")]
    if not real_shots:
        raise RuntimeError("FINAL_VISUAL_SEMANTIC_GATE: timeline sem shots editoriais.")

    opening_asset_id = str(real_shots[0].get("asset") or "").strip()
    opening_asset = assets_by_id.get(opening_asset_id)
    if _asset_kind(opening_asset) != "video":
        raise RuntimeError(
            "FINAL_VISUAL_SEMANTIC_GATE: primeiro visual editorial precisa ser video; "
            f"asset={opening_asset_id or '<ausente>'}."
        )

    checked_video_assets: set[str] = set()
    for shot in real_shots:
        asset_id = str(shot.get("asset") or "").strip()
        asset = assets_by_id.get(asset_id)
        if asset is None:
            raise RuntimeError(
                f"FINAL_VISUAL_SEMANTIC_GATE: shot referencia asset ausente: {asset_id}."
            )
        if _asset_kind(asset) != "video" or asset_id in checked_video_assets:
            continue
        checked_video_assets.add(asset_id)

        selection = selections.get(asset_id)
        if not isinstance(selection, dict):
            raise RuntimeError(
                "FINAL_VISUAL_SEMANTIC_GATE: video final nao possui selecao semantica "
                f"do resolver atual: asset={asset_id}."
            )
        if selection.get("status") != "selected" or selection.get("kind") != "video":
            raise RuntimeError(
                "FINAL_VISUAL_SEMANTIC_GATE: video existente/base nao pode sobreviver "
                "como fallback sem validacao direct/exact; "
                f"asset={asset_id} status={selection.get('status')} kind={selection.get('kind')}."
            )

    opening_selection = selections.get(opening_asset_id)
    if not isinstance(opening_selection, dict) or opening_selection.get("status") != "selected" or opening_selection.get("kind") != "video":
        raise RuntimeError(
            "FINAL_VISUAL_SEMANTIC_GATE: abertura nao foi resolvida nesta execucao "
            "como video direct/exact; nao publicar fallback generico."
        )

    print(
        "FINAL_VISUAL_SEMANTIC_GATE=PASS "
        f"opening={opening_asset_id} verified_video_assets={len(checked_video_assets)}"
    )


def main() -> int:
    root = Path.cwd()
    slug = str(os.environ.get("EPISODE") or "").strip()
    if not slug:
        raise RuntimeError("EPISODE nao definido para o handoff visual.")

    episode_rel = Path("episodes") / slug
    episode_dir = root / episode_rel
    if not episode_dir.is_dir():
        raise RuntimeError(f"Diretorio do episodio nao encontrado: {episode_rel}")

    _validate_final_video_semantics(episode_dir)
    final_asset_paths = _final_asset_paths(episode_rel, episode_dir)

    handoff_root = root / ".visual-handoff"
    if handoff_root.exists():
        shutil.rmtree(handoff_root)
    handoff_episode = handoff_root / episode_rel
    handoff_episode.mkdir(parents=True, exist_ok=True)

    copied: set[Path] = set()

    for name in ("assets.json", "timeline.json", "visual_resolution_report.json"):
        source = episode_dir / name
        if not source.is_file():
            continue
        destination = handoff_episode / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.add(source)

    changed = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--modified",
            "--exclude-standard",
            "-z",
            "--",
            str(episode_rel / "assets"),
        ],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.decode("utf-8", errors="surrogateescape")

    skipped = 0
    for raw in changed.split("\0"):
        if not raw:
            continue
        rel = Path(raw)
        if rel not in final_asset_paths:
            skipped += 1
            print(f"Handoff skip non-final candidate: {rel}")
            continue

        source = root / rel
        if not source.is_file():
            continue
        destination = handoff_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.add(source)
        print(f"Handoff media: {rel}")

    print(
        "Visual handoff preparado "
        f"com {len(copied)} arquivo(s); {skipped} candidato(s) nao finais ignorados."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
