from __future__ import annotations

import atexit
import os
from pathlib import Path
import tempfile
from urllib.parse import urlparse

import engine.visual_search_web as web_engine
import resolve_visual_candidates_web as resolver


YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
COOKIE_FALLBACK_CLIENTS = ("default", "web_embedded")
AUTH_ERROR_MARKERS = (
    "sign in to confirm you're not a bot",
    "sign in to confirm you’re not a bot",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "login_required",
    "use --cookies",
)


def _warn(message: str) -> None:
    if str(os.getenv("GITHUB_ACTIONS") or "").casefold() == "true":
        print(f"::warning::{message}")
    else:
        print(f"WARNING: {message}")


def _is_youtube_target(raw: object) -> bool:
    value = str(raw or "").strip()
    host = (urlparse(value).hostname or "").casefold()
    return host in YOUTUBE_HOSTS


def _is_auth_error(exc: BaseException) -> bool:
    text = str(exc).casefold()
    return any(marker in text for marker in AUTH_ERROR_MARKERS)


def _remove_cookie_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _prepare_cookie_file() -> Path | None:
    raw = str(os.getenv("YOUTUBE_COOKIES") or "")
    if not raw.strip():
        return None

    # GitHub Actions secrets preserve multiline values. Normalize line endings only;
    # never print or persist the secret in the repository/workspace.
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    first_line = normalized.splitlines()[0].strip() if normalized.splitlines() else ""
    if "netscape" not in first_line.casefold():
        _warn(
            "YOUTUBE_COOKIES nao parece um cookies.txt Netscape; "
            "o fallback sera tentado, mas o yt-dlp pode rejeitar o arquivo."
        )

    temp_root = Path(os.getenv("RUNNER_TEMP") or tempfile.gettempdir())
    temp_root.mkdir(parents=True, exist_ok=True)
    cookie_file = temp_root / f"music-short-factory-youtube-cookies-{os.getpid()}.txt"
    cookie_file.write_text(normalized, encoding="utf-8")
    try:
        cookie_file.chmod(0o600)
    except OSError:
        pass
    atexit.register(_remove_cookie_file, cookie_file)
    return cookie_file


def _with_cookie_fallback_options(params: dict | None, cookie_file: Path) -> dict:
    patched = dict(params or {})
    patched["cookiefile"] = str(cookie_file)

    raw_extractor_args = patched.get("extractor_args")
    extractor_args = dict(raw_extractor_args) if isinstance(raw_extractor_args, dict) else {}

    raw_youtube = extractor_args.get("youtube")
    youtube_args = dict(raw_youtube) if isinstance(raw_youtube, dict) else {}
    youtube_args["player_client"] = list(COOKIE_FALLBACK_CLIENTS)
    extractor_args["youtube"] = youtube_args

    # The cookie retry intentionally does not inherit the bgutil/mweb provider path.
    # It is a separate auth strategy after the primary PO-token attempt fails.
    extractor_args.pop("youtubepot-bgutilhttp", None)
    patched["extractor_args"] = extractor_args
    return patched


def _install_cookie_fallback() -> None:
    cookie_file = _prepare_cookie_file()
    if cookie_file is None:
        return

    # At this point the primary API is the current PO-token path when available.
    # Keep it for attempt 1, but use the original raw yt-dlp API for the cookie retry
    # so mweb/bgutil options cannot leak into attempt 2.
    primary_api = web_engine._yt_dlp_api
    cookie_api = getattr(web_engine, "_yt_dlp_api_original", primary_api)

    def patched_api():
        PrimaryYoutubeDL, DownloadError = primary_api()
        CookieYoutubeDL, _CookieDownloadError = cookie_api()

        class CookieFallbackYoutubeDL(PrimaryYoutubeDL):
            def __init__(self, params=None, auto_init=True):
                self._cookie_fallback_params = dict(params or {})
                super().__init__(params, auto_init=auto_init)

            def extract_info(self, url, *args, **kwargs):
                try:
                    return super().extract_info(url, *args, **kwargs)
                except DownloadError as exc:
                    if not _is_youtube_target(url) or not _is_auth_error(exc):
                        raise

                    print(
                        "YouTube web: tentativa primaria bloqueada; "
                        "repetindo candidato com cookies de fallback "
                        "(clients default,web_embedded)."
                    )
                    fallback_params = _with_cookie_fallback_options(
                        self._cookie_fallback_params,
                        cookie_file,
                    )
                    with CookieYoutubeDL(fallback_params) as fallback_ydl:
                        return fallback_ydl.extract_info(url, *args, **kwargs)

        return CookieFallbackYoutubeDL, DownloadError

    web_engine._yt_dlp_api = patched_api
    print(
        "YouTube web: cookies de fallback configurados; "
        "PO/default continua sendo a primeira tentativa."
    )


_original_install_youtube_po_patch = resolver._install_youtube_po_patch


def _install_po_then_cookie() -> None:
    _original_install_youtube_po_patch()
    _install_cookie_fallback()


resolver._install_youtube_po_patch = _install_po_then_cookie


if __name__ == "__main__":
    raise SystemExit(resolver.main())
