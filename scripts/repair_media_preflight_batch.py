from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

VISUAL_TARGETED_CODES = {
    "VISUAL_ASSET_HTTP_403",
    "VISUAL_ASSET_HTTP_404",
    "VISUAL_ASSET_INVALID",
    "MEDIA_PROBE_OR_CODEC_ERROR",
    "VIDEO_SOURCE_WINDOW_INVALID",
    "INTRA_EPISODE_VISUAL_REUSE",
    "SHOT_REFERENCES_MISSING_ASSET",
    "FIRST_EDITORIAL_VISUAL_NOT_VIDEO",
}
VISUAL_GENERAL_CODES = {
    "INTRA_EPISODE_VISUAL_REUSE",
    "SHOT_REFERENCES_MISSING_ASSET",
    "FIRST_EDITORIAL_VISUAL_NOT_VIDEO",
    "VISUAL_CANDIDATE_POOL_MISSING",
    "VISUAL_CANDIDATE_POOL_INVALID",
}
BACKGROUND_CODES = {
    "BACKGROUND_MUSIC_REUSE",
    "BACKGROUND_MUSIC_INVALID",
}


def _load_errors(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if text.lstrip().startswith("{"):
        data = json.loads(text).get("errors")
        if not isinstance(data, list):
            raise RuntimeError("Diagnostic errors must be a list.")
        return [item for item in data if isinstance(item, dict)]
    for raw_line in text.splitlines():
        if not raw_line.startswith("MEDIA_PREFLIGHT_ERRORS_JSON="):
            continue
        payload = raw_line.split("=", 1)[1].strip()
        data = json.loads(payload)
        if not isinstance(data, list):
            raise RuntimeError("MEDIA_PREFLIGHT_ERRORS_JSON nao e uma lista.")
        return [item for item in data if isinstance(item, dict)]
    raise RuntimeError("MEDIA_PREFLIGHT_ERRORS_JSON ausente no log.")


def _run(command: list[str]) -> bool:
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    return completed.returncode == 0


def _run_repair(root: Path, command: list[str]) -> bool:
    from engine.coordination_runtime import in_private_staging
    if not in_private_staging(root):
        return _run(command)
    # Keep private staged repairs in this trusted context. A subprocess must not
    # mistake disposable files for a new authoritative checkout.
    try:
        if command[1].endswith("repair_visual_asset.py"):
            from scripts.repair_visual_asset import repair
            return repair(root, command[2], Path(command[4])) == 0
        from scripts.repair_background_music import repair_background
        return repair_background(root, command[2]) == 0
    except Exception as exc:
        print(f"BATCH_REPAIR_FAILED={type(exc).__name__}")
        return False


def _independent_errors(errors: list[dict[str, object]]) -> list[dict[str, object]]:
    """Prefer an exact repair scope over aggregate copies of the same failure.

    A fail-fast loader and its independent entry parser can report the same
    exception. Retain separate asset/shot scopes even when their generic HTTP
    detail happens to match: those are independent targets.
    """
    def signature(error):
        return (str(error.get("code") or error.get("error_code") or ""),
                " ".join(str(error.get("detail") or "").split()))

    def exact_scope(error):
        return str(error.get("scope") or "").startswith(("asset:", "shot:"))

    scoped = {signature(error) for error in errors if exact_scope(error)}
    seen = set()
    independent = []
    for error in errors:
        identity = signature(error)
        if not exact_scope(error) and identity in scoped:
            continue
        key = (*identity, str(error.get("scope") or ""))
        if key not in seen:
            seen.add(key)
            independent.append(error)
    return independent


def _legacy_log(error: dict[str, object], slug: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="media-preflight-error-",
        suffix=".log",
        delete=False,
    )
    path = Path(handle.name)
    try:
        handle.write(f"MEDIA_PREFLIGHT_ERROR_CODE={error.get('code', '')}\n")
        handle.write(f"MEDIA_PREFLIGHT_SCOPE={error.get('scope', '')}\n")
        handle.write(f"MEDIA_PREFLIGHT_ERROR_CLASS={error.get('class', '')}\n")
        handle.write(f"MEDIA_PREFLIGHT_ERROR_DETAIL={error.get('detail', '')}\n")
        handle.write(
            "MEDIA_PREFLIGHT_RECOVERABLE="
            + ("true" if bool(error.get("recoverable")) else "false")
            + "\n"
        )
        handle.write(f"MEDIA_PREFLIGHT_TARGET={error.get('target', '')}\n")
        handle.write(
            f"MEDIA_PREFLIGHT_RECOMMENDED_ACTION={error.get('recommended_action', '')}\n"
        )
        handle.write(f"MEDIA_PREFLIGHT_SAME_SLUG={slug}\n")
    finally:
        handle.close()
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply all safe automatic repairs from one batch media preflight pass."
    )
    parser.add_argument("episode", help="Episode slug")
    parser.add_argument("--diagnostic-log", required=True, help="Batch preflight log")
    args = parser.parse_args()

    slug = args.episode.strip()
    return repair_batch(PROJECT_ROOT, slug, Path(args.diagnostic_log))


def repair_batch(root: Path, slug: str, diagnostic_log: Path) -> int:
    from engine.coordination_runtime import acquire_token
    acquire_token(root, slug)
    from engine.pipeline_state import PipelineStore
    PipelineStore(root).assert_mutation_allowed(slug)
    errors = _independent_errors(_load_errors(diagnostic_log))
    if not errors:
        print("BATCH_AUTO_REPAIR_NOT_NEEDED=true")
        return 0

    print(f"BATCH_AUTO_REPAIR_ERROR_COUNT={len(errors)}")

    nonrecoverable = [error for error in errors if not bool(error.get("recoverable"))]
    if nonrecoverable:
        codes = ",".join(str(error.get("code") or "UNKNOWN") for error in nonrecoverable)
        print(f"BATCH_AUTO_REPAIR_UNRESOLVED_NONRECOVERABLE={codes}")

    handled = 0
    failures: list[str] = [f"nonrecoverable:{error.get('code', 'UNKNOWN')}" for error in nonrecoverable]

    # Repair exact broken assets first while their failing file names still match
    # assets.json. Each error gets a tiny legacy-format log so the targeted repairer
    # can keep its strict single-slot semantics.
    targeted_seen: set[tuple[str, str]] = set()
    for error in errors:
        code = str(error.get("code") or "")
        if code not in VISUAL_TARGETED_CODES or error.get("recoverable") is not True:
            continue
        identity = (str(error.get("scope") or ""), str(error.get("target") or error.get("detail") or ""))
        if identity in targeted_seen:
            continue
        targeted_seen.add(identity)
        mini_log = _legacy_log(error, slug)
        try:
            print(f"BATCH_REPAIR_ITEM=visual_targeted code={code}")
            if _run_repair(root,
                [
                    sys.executable,
                    "scripts/repair_visual_asset.py",
                    slug,
                    "--diagnostic-log",
                    str(mini_log),
                ]
            ):
                handled += 1
            else:
                failures.append(f"{code}:{error.get('scope', '')}")
        finally:
            mini_log.unlink(missing_ok=True)

    # Never invent a candidate pool or repeat an unchanged broad resolver. A
    # missing/invalid pool needs authored alternatives, while independent safe
    # asset and background repairs must still complete in this batch.
    for error in errors:
        code = str(error.get("code") or "")
        if code in {"VISUAL_CANDIDATE_POOL_MISSING", "VISUAL_CANDIDATE_POOL_INVALID"}:
            failures.append(f"requires_candidates:{code}")

    # Background repair now chooses a fresh usable local profile directly instead
    # of rotating one profile per full preflight pass.
    if any(str(error.get("code") or "") in BACKGROUND_CODES and error.get("recoverable") is True for error in errors):
        print("BATCH_REPAIR_ITEM=background")
        if _run_repair(root, [sys.executable, "scripts/repair_background_music.py", slug]):
            handled += 1
        else:
            failures.append("background")

    supported_codes = VISUAL_TARGETED_CODES | VISUAL_GENERAL_CODES | BACKGROUND_CODES
    unsupported = sorted(
        {
            str(error.get("code") or "UNKNOWN")
            for error in errors
            if str(error.get("code") or "") not in supported_codes
        }
    )
    if unsupported:
        failures.extend(f"unsupported:{code}" for code in unsupported)

    print(f"BATCH_AUTO_REPAIR_HANDLED={handled}")
    print("BATCH_AUTO_REPAIR_CHANGED=" + ("true" if handled else "false"))
    if failures:
        print("BATCH_AUTO_REPAIR_FAILURES=" + ";".join(failures))
        return 2

    if handled == 0:
        print("BATCH_AUTO_REPAIR_CHANGED=false")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
