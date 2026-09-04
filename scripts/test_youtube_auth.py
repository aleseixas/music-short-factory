from __future__ import annotations

import os

import engine.visual_search_web as web_engine
import resolve_visual_candidates_web as resolver
import resolve_visual_candidates_web_auth  # noqa: F401 - installs PO -> cookie fallback wrapper


DEFAULT_URL = "https://www.youtube.com/watch?v=yszWh_7xYrs"


def main() -> int:
    target_url = str(os.getenv("YOUTUBE_AUTH_TEST_URL") or DEFAULT_URL).strip()
    cookies_configured = bool(str(os.getenv("YOUTUBE_COOKIES") or "").strip())
    print(f"YouTube auth smoke test: cookies secret configured={'yes' if cookies_configured else 'no'}")
    if not cookies_configured:
        print("::error::YOUTUBE_COOKIES nao esta configurado; teste de fallback nao pode ser concluido.")
        return 2

    # This installs the existing primary PO-token path first and then the cookie
    # fallback wrapper. No episode is rendered and no platform publishing code runs.
    resolver._install_patches()
    YoutubeDL, DownloadError = web_engine._yt_dlp_api()

    params = {
        "noplaylist": True,
        "skip_download": True,
        "quiet": False,
        "no_warnings": False,
    }

    try:
        with YoutubeDL(params) as ydl:
            info = ydl.extract_info(target_url, download=False)
    except DownloadError as exc:
        print(f"::error::YouTube auth smoke test falhou depois dos fallbacks: {exc}")
        return 1

    title = str((info or {}).get("title") or "<sem titulo>")
    video_id = str((info or {}).get("id") or "<sem id>")
    print(f"YOUTUBE_AUTH_TEST_OK id={video_id} title={title}")
    print("Nenhum video foi baixado e nenhuma publicacao foi executada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
