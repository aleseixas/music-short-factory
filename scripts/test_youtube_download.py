from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

import engine.visual_search_web as web_engine
import resolve_visual_candidates_web as resolver
import resolve_visual_candidates_web_auth  # noqa: F401 - installs auth fallback patch
from engine.models import VIDEO_ASSET_EXTENSIONS
from engine.youtube import canonical_youtube_url


DEFAULT_URL = "https://www.youtube.com/watch?v=DKZV_DbmWQo"
DEFAULT_SECONDS = 8


def _probe(path: Path) -> dict:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(completed.stdout)


def main() -> int:
    target_url = canonical_youtube_url(str(os.getenv("YOUTUBE_DOWNLOAD_TEST_URL") or DEFAULT_URL))
    if not target_url:
        print("YOUTUBE_REAL_DOWNLOAD_FAIL reason=INVALID_VIDEO_LOCATOR")
        return 2
    seconds = int(str(os.getenv("YOUTUBE_DOWNLOAD_TEST_SECONDS") or DEFAULT_SECONDS))
    cookies_configured = bool(str(os.getenv("YOUTUBE_COOKIES") or "").strip())

    print(f"YouTube real download smoke test: {target_url}")
    print(f"Requested clip length: {seconds}s")
    print(f"Cookies configured: {'yes' if cookies_configured else 'no'}")
    print(
        "Quality policy: "
        f"{web_engine.YOUTUBE_QUALITY_POLICY_VERSION} "
        f"format={web_engine.YOUTUBE_FORMAT_SELECTOR} "
        f"sort={','.join(web_engine.YOUTUBE_FORMAT_SORT)}"
    )

    resolver._install_patches()
    YoutubeDL, DownloadError = web_engine._yt_dlp_api()

    try:
        from yt_dlp.utils import download_range_func
    except Exception as exc:
        print(f"::error::Could not import yt-dlp download_range_func: {exc}")
        return 2

    with tempfile.TemporaryDirectory(prefix="music-short-factory-ytdlp-") as tmp:
        temp_root = Path(tmp)
        outtmpl = str(temp_root / "clip.%(ext)s")
        params = {
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
            "logger": web_engine._SafeYoutubeLogger(),
            "continuedl": False,
            "overwrites": True,
            "skip_unavailable_fragments": False,
            "outtmpl": outtmpl,
            "max_filesize": web_engine.MAX_EXTERNAL_VIDEO_BYTES,
            "format": web_engine.YOUTUBE_FORMAT_SELECTOR,
            "format_sort": list(web_engine.YOUTUBE_FORMAT_SORT),
            "download_ranges": download_range_func(None, [(0, seconds)]),
            "force_keyframes_at_cuts": True,
        }

        try:
            with YoutubeDL(params) as ydl:
                info = ydl.extract_info(target_url, download=True)
                if getattr(ydl, "_download_retcode", 0):
                    raise DownloadError(f"downloader exit code={ydl._download_retcode}")
        except DownloadError as exc:
            print(f"::error::Real YouTube download failed after configured fallbacks: {web_engine._safe_yt_dlp_diagnostic(exc)}")
            return 1

        files = [path for path in temp_root.iterdir() if path.is_file() and path.suffix.casefold() in VIDEO_ASSET_EXTENSIONS and path.stat().st_size >= web_engine.MIN_WEB_VIDEO_BYTES]
        if not files:
            print("::error::yt-dlp reported success but no downloaded media file was produced.")
            return 1

        media = max(files, key=lambda path: path.stat().st_size)
        probe = _probe(media)
        duration = float((probe.get("format") or {}).get("duration") or 0.0)
        size = int((probe.get("format") or {}).get("size") or media.stat().st_size)
        streams = probe.get("streams") or []
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]

        if duration <= 0:
            print("::error::ffprobe found no valid duration in downloaded media.")
            return 1
        if not video_streams:
            print("::error::ffprobe found no video stream in downloaded media.")
            return 1

        title = str((info or {}).get("title") or "<sem titulo>")
        video_id = str((info or {}).get("id") or "<sem id>")
        codec = str(video_streams[0].get("codec_name") or "unknown")
        width = video_streams[0].get("width") or "?"
        height = video_streams[0].get("height") or "?"

        print(
            "YOUTUBE_REAL_DOWNLOAD_OK "
            f"id={video_id} title={title!r} duration={duration:.2f}s "
            f"size={size} video={codec} {width}x{height} "
            f"quality_policy={web_engine.YOUTUBE_QUALITY_POLICY_VERSION}"
        )
        print("Temporary downloaded media validated by ffprobe and will now be deleted.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
