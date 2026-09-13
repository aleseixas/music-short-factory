"""Read-only PO/default and optional cookie probe; print classifications only."""
from __future__ import annotations

import os
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import time

import engine.visual_search_web as web_engine
from engine.youtube import canonical_youtube_url


DEFAULT_URL = "https://www.youtube.com/watch?v=yszWh_7xYrs"


class ProbeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, _message: object) -> None:
        pass

    def warning(self, message: object) -> None:
        self.messages.append(str(message))

    error = warning


def failure_reason(messages: list[str]) -> str:
    text = " ".join(messages).casefold()
    for reason, markers in (
        ("COOKIES_REJECTED", ("cookies are no longer valid", "cookies have expired", "cookies expired", "cookies have likely been rotated", "invalid netscape")),
        ("BOT_CHALLENGE", ("not a bot", "bot challenge")),
        ("SIGN_IN_REQUIRED", ("sign in", "login_required", "use --cookies")),
        ("PO_TOKEN", ("po token", "po_token")),
        ("FORMAT_UNAVAILABLE", ("format is not available", "format is unavailable", "no video formats")),
        ("FRAGMENT_FAILURE", ("fragment",)),
        ("HTTP_403", ("http error 403", "http 403")),
        ("HTTP_429", ("http error 429", "http 429")),
        ("NETWORK", ("timed out", "unable to download", "connection")),
    ):
        if any(marker in text for marker in markers):
            return reason
    return "UNKNOWN" if messages else "NONE"


def cookie_file_state(path: Path | None) -> tuple[bool, bool, bool]:
    """Return parseable, accepted by yt-dlp's jar, all Google cookies expired."""
    if path is None:
        return False, False, False
    try:
        from yt_dlp.cookies import YoutubeDLCookieJar

        diagnostic = io.StringIO()
        with redirect_stdout(diagnostic), redirect_stderr(diagnostic):
            jar = YoutubeDLCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
        if diagnostic.getvalue():
            return False, False, False
        cookies = list(jar)
        google = [cookie for cookie in cookies if cookie.domain.lstrip(".").endswith(("youtube.com", "google.com"))]
        expired = bool(google) and all(cookie.expires is not None and cookie.expires > 0 and cookie.expires <= time.time() for cookie in google)
        return bool(cookies), True, expired
    except Exception:
        # The cookie parser exception itself may contain secret rows.
        return False, False, False


def probe_path(api, params: dict, target: str) -> tuple[bool, str]:
    logger = ProbeLogger()
    options = {"noplaylist": True, "skip_download": True, "quiet": True,
               "no_warnings": False, "logger": logger, "socket_timeout": 20,
               "retries": 1, "extractor_retries": 1, **params}
    try:
        YoutubeDL, _DownloadError = api()
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(target, download=False)
            success = bool(isinstance(info, dict) and (info.get("formats") or info.get("url")))
            success = success and not bool(getattr(ydl, "_download_retcode", 0))
    except Exception as exc:
        logger.messages.append(str(exc))
        success = False
    return success, failure_reason(logger.messages)


def cookie_status(*, primary_ok: bool, present: bool, parseable: bool,
                  all_expired: bool, fallback_ok: bool, fallback_reason: str) -> str:
    if primary_ok:
        return "NOT_REQUIRED"
    if present and (not parseable or all_expired or fallback_reason == "COOKIES_REJECTED"):
        return "INVALID"
    if fallback_ok:
        return "VALID"
    return "UNKNOWN"


def main() -> int:
    # The CLI auth adapter installs resolver monkeypatches when imported. Keep
    # that side effect out of test discovery and callers of pure probe helpers.
    import resolve_visual_candidates_web_auth as youtube_auth

    target = canonical_youtube_url(str(os.getenv("YOUTUBE_AUTH_TEST_URL") or DEFAULT_URL))
    if not target:
        print("YOUTUBE_COOKIE_STATUS=UNKNOWN")
        print("YOUTUBE_PROBE_REASON=INVALID_VIDEO_LOCATOR")
        return 2
    present = bool(str(os.getenv("YOUTUBE_COOKIES") or "").strip())
    cookie_file = youtube_auth._prepare_cookie_file() if present else None
    try:
        parseable, accepted, expired = cookie_file_state(cookie_file)
        # Install only the current primary path, so successes are attributable
        # objectively instead of being inferred from a combined fallback wrapper.
        youtube_auth._original_install_youtube_po_patch()
        primary_ok, primary_reason = probe_path(web_engine._yt_dlp_api, {}, target)
        fallback_ok, fallback_reason = False, "NOT_ATTEMPTED"
        # This diagnostic intentionally tests a supplied cookie jar independently
        # even if the normal path worked. Production still uses it only on a
        # compatible auth failure; status remains NOT_REQUIRED on primary success.
        if parseable:
            raw_api = getattr(web_engine, "_yt_dlp_api_original", web_engine._yt_dlp_api)
            fallback_ok, fallback_reason = probe_path(
                raw_api, youtube_auth._with_cookie_fallback_options({}, cookie_file), target,
            )
        status = cookie_status(primary_ok=primary_ok, present=present, parseable=parseable,
                               all_expired=expired, fallback_ok=fallback_ok, fallback_reason=fallback_reason)
        cookie_tested = fallback_reason != "NOT_ATTEMPTED"
        rejected = ("TRUE" if fallback_reason == "COOKIES_REJECTED" else
                    "FALSE" if fallback_ok else "UNKNOWN" if cookie_tested else "NOT_TESTED")
        youtube_accepted = ("FALSE" if rejected == "TRUE" else
                            "TRUE" if fallback_ok else "UNKNOWN" if cookie_tested else "NOT_TESTED")
        for key, value in (
            ("YOUTUBE_COOKIES_PRESENT", present),
            ("YOUTUBE_COOKIES_PARSEABLE", parseable),
            ("YOUTUBE_COOKIES_YTDLP_ACCEPTED", accepted),
            ("YOUTUBE_COOKIES_ALL_EXPIRED", expired),
            ("YOUTUBE_COOKIES_REJECTED", rejected),
            ("YOUTUBE_COOKIES_YOUTUBE_ACCEPTED", youtube_accepted),
            ("YOUTUBE_PRIMARY_OK", primary_ok),
            ("YOUTUBE_PRIMARY_REASON", primary_reason),
            ("YOUTUBE_COOKIE_FALLBACK_REASON", fallback_reason),
            ("YOUTUBE_COOKIE_STATUS", status),
        ):
            print(f"{key}={str(value).upper() if isinstance(value, bool) else value}")
        return 0 if status in {"NOT_REQUIRED", "VALID"} else 1
    finally:
        if cookie_file is not None:
            youtube_auth._remove_cookie_file(cookie_file)


if __name__ == "__main__":
    raise SystemExit(main())
