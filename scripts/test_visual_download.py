from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import resolve_visual_candidates_web as resolver
import resolve_visual_candidates_web_auth  # noqa: F401  # installs auth fallback hook


DEFAULT_URL = "https://www.youtube.com/watch?v=DKZV_DbmWQo"
DEFAULT_START_SECONDS = 20.0
DEFAULT_REQUIRED_SECONDS = 8.0


def _ffprobe(path: Path) -> dict:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = json.loads(completed.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("ffprobe returned a non-object JSON payload")
    return payload


def main() -> int:
    target_url = str(os.getenv("VISUAL_DOWNLOAD_TEST_URL") or DEFAULT_URL).strip()
    start_seconds = float(os.getenv("VISUAL_DOWNLOAD_TEST_START") or DEFAULT_START_SECONDS)
    required_seconds = float(os.getenv("VISUAL_DOWNLOAD_TEST_SECONDS") or DEFAULT_REQUIRED_SECONDS)

    if required_seconds <= 0 or required_seconds > 15:
        raise RuntimeError("VISUAL_DOWNLOAD_TEST_SECONDS must be > 0 and <= 15")

    print(f"Visual download smoke test: {target_url}")
    print(f"Requested clip window: start={start_seconds:.1f}s duration={required_seconds:.1f}s")

    resolver.CURRENT_EPISODE = "visual_download_smoke_test"
    resolver._install_patches()

    slot = {
        "id": "youtube_smoke",
        "required_seconds": required_seconds,
        "crossfade_seconds": 0.0,
        "min_visual_score": 0.0,
    }
    candidate = {
        "kind": "video",
        "name": "Luisa Sonza / Roberto Menescal smoke test",
        "source": "youtube",
        "search_provider": "youtube_web",
        "source_page_url": target_url,
        "url": target_url,
        "file": "youtube_smoke.mp4",
        "source_start_seconds": start_seconds,
        "editorial_rank": 1,
    }

    result, inspection, reasons = resolver._inspect_candidate(
        Path.cwd(),
        slot,
        candidate,
        1,
    )

    path = Path(inspection.path)
    if not path.is_file():
        raise RuntimeError(f"Resolver returned missing media file: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Resolver returned empty media file: {path}")

    probe = _ffprobe(path)
    streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
    video_streams = [
        stream
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    ]
    if not video_streams:
        raise RuntimeError("Downloaded candidate has no video stream according to ffprobe")

    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    duration = float(fmt.get("duration") or 0.0)
    size = int(float(fmt.get("size") or path.stat().st_size))

    if duration <= 0:
        raise RuntimeError("Downloaded candidate has invalid duration")

    print(
        "VISUAL_DOWNLOAD_SMOKE_OK "
        f"provider={result.search_provider} "
        f"path={path} size={size} duration={duration:.3f}s "
        f"score={inspection.technical_visual_score:.2f} "
        f"warnings={len(reasons)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
