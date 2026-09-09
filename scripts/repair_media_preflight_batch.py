from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

VISUAL_TARGETED_CODES = {
    "VISUAL_ASSET_HTTP_403",
    "VISUAL_ASSET_HTTP_404",
    "VISUAL_ASSET_INVALID",
    "MEDIA_PROBE_OR_CODEC_ERROR",
}
VISUAL_GENERAL_CODES = {
    "INTRA_EPISODE_VISUAL_REUSE",
    "SHOT_REFERENCES_MISSING_ASSET",
}
BACKGROUND_CODES = {
    "BACKGROUND_MUSIC_REUSE",
    "BACKGROUND_MUSIC_INVALID",
}


def _load_errors(path: Path) -> list[dict[str, object]]:
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
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
    errors = _load_errors(Path(args.diagnostic_log))
    if not errors:
        print("BATCH_AUTO_REPAIR_NOT_NEEDED=true")
        return 0

    print(f"BATCH_AUTO_REPAIR_ERROR_COUNT={len(errors)}")

    nonrecoverable = [error for error in errors if not bool(error.get("recoverable"))]
    if nonrecoverable:
        codes = ",".join(str(error.get("code") or "UNKNOWN") for error in nonrecoverable)
        print(f"BATCH_AUTO_REPAIR_BLOCKED_NONRECOVERABLE={codes}")
        return 2

    handled = 0
    failures: list[str] = []

    # Repair exact broken assets first while their failing file names still match
    # assets.json. Each error gets a tiny legacy-format log so the targeted repairer
    # can keep its strict single-slot semantics.
    targeted_seen: set[tuple[str, str]] = set()
    for error in errors:
        code = str(error.get("code") or "")
        if code not in VISUAL_TARGETED_CODES:
            continue
        identity = (code, str(error.get("detail") or ""))
        if identity in targeted_seen:
            continue
        targeted_seen.add(identity)
        mini_log = _legacy_log(error, slug)
        try:
            print(f"BATCH_REPAIR_ITEM=visual_targeted code={code}")
            if _run(
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

    # A general reselection can repair duplicate/missing references in one sweep.
    if any(str(error.get("code") or "") in VISUAL_GENERAL_CODES for error in errors):
        print("BATCH_REPAIR_ITEM=visual_general")
        before = subprocess.run(
            ["git", "diff", "--", f"episodes/{slug}"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        _run([sys.executable, "resolve_visual_candidates.py", slug])
        _run([sys.executable, "resolve_visual_candidates_web_auth.py", slug])
        after = subprocess.run(
            ["git", "diff", "--", f"episodes/{slug}"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        if after != before:
            handled += 1
        else:
            failures.append("visual_general:no_change")

    # Background repair now chooses a fresh usable local profile directly instead
    # of rotating one profile per full preflight pass.
    if any(str(error.get("code") or "") in BACKGROUND_CODES for error in errors):
        print("BATCH_REPAIR_ITEM=background")
        if _run([sys.executable, "scripts/repair_background_music.py", slug]):
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

    if failures:
        print("BATCH_AUTO_REPAIR_FAILURES=" + ";".join(failures))
        return 2

    if handled == 0:
        print("BATCH_AUTO_REPAIR_CHANGED=false")
        return 2

    print(f"BATCH_AUTO_REPAIR_HANDLED={handled}")
    print("BATCH_AUTO_REPAIR_CHANGED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
