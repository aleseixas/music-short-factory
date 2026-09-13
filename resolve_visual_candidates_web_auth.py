from __future__ import annotations

import atexit
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import re
from urllib.parse import parse_qs, urlparse

import engine.visual_search_web as web_engine
import resolve_visual_candidates_web as resolver
from engine.visual_resolution_policy import (
    reuse_penalty_for_prior_uses,
    video_fallback_block_reason,
)
from engine.youtube import extract_youtube_video_id


YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
COOKIE_FALLBACK_CLIENTS = ("web_embedded",)
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


def _youtube_log_id(raw: object) -> str:
    canonical_id = extract_youtube_video_id(str(raw or ""))
    if canonical_id:
        return canonical_id
    parsed = urlparse(str(raw or "").strip())
    host = (parsed.hostname or "").casefold()
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
    else:
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    return cleaned[:100] or "unknown"


def _candidate_log_id(candidate: dict, index: int) -> str:
    cleaned = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        str(candidate.get("provider_id") or index),
    ).strip("._")
    return cleaned[:100] or str(index)


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
    # yt-dlp's cookie parser can print an invalid Netscape row directly to stderr
    # (outside its configured logger). Validate quietly before passing the file
    # into the downloader; never echo a rejected row containing credentials.
    try:
        from yt_dlp.cookies import YoutubeDLCookieJar

        diagnostic = io.StringIO()
        with redirect_stdout(diagnostic), redirect_stderr(diagnostic):
            jar = YoutubeDLCookieJar(str(cookie_file))
            jar.load(ignore_discard=True, ignore_expires=True)
        if not list(jar) or diagnostic.getvalue():
            raise ValueError("invalid cookie file")
    except Exception:
        _remove_cookie_file(cookie_file)
        _warn("YOUTUBE_COOKIES presente, mas nao parseavel pelo yt-dlp; fallback ignorado.")
        return None
    atexit.register(_remove_cookie_file, cookie_file)
    return cookie_file


def _with_node_ejs_options(params: dict | None) -> dict:
    patched = dict(params or {})
    raw_runtimes = patched.get("js_runtimes")
    runtimes = dict(raw_runtimes) if isinstance(raw_runtimes, dict) else {}
    runtimes["node"] = {}
    patched["js_runtimes"] = runtimes
    return patched


def _with_cookie_fallback_options(params: dict | None, cookie_file: Path) -> dict:
    patched = _with_node_ejs_options(params)
    patched["cookiefile"] = str(cookie_file)
    # Never resume a fragment/partial payload from the failed primary strategy.
    patched["continuedl"] = False
    patched["overwrites"] = True
    patched["skip_unavailable_fragments"] = False

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
                super().__init__(
                    _with_node_ejs_options(params),
                    auto_init=auto_init,
                )

            def extract_info(self, url, *args, **kwargs):
                try:
                    return super().extract_info(url, *args, **kwargs)
                except DownloadError as exc:
                    if not _is_youtube_target(url) or not _is_auth_error(exc):
                        raise

                    video_id = _youtube_log_id(url)
                    primary_detail = web_engine._safe_yt_dlp_diagnostic(exc)
                    print(
                        f"YT_DLP_AUTH id={video_id} primary=FAIL reason="
                        f"{primary_detail}; fallback=cookies_web_embedded",
                        flush=True,
                    )
                    fallback_params = _with_cookie_fallback_options(
                        self._cookie_fallback_params,
                        cookie_file,
                    )
                    try:
                        with CookieYoutubeDL(fallback_params) as fallback_ydl:
                            result = fallback_ydl.extract_info(url, *args, **kwargs)
                            return_code = getattr(fallback_ydl, "_download_retcode", 0)
                            if return_code:
                                raise _CookieDownloadError(f"downloader exit code={return_code}")
                            self._download_retcode = return_code
                    except _CookieDownloadError as fallback_exc:
                        fallback_detail = web_engine._safe_yt_dlp_diagnostic(fallback_exc)
                        print(
                            f"YT_DLP_AUTH id={video_id} fallback=FAIL reason="
                            f"{fallback_detail}",
                            flush=True,
                        )
                        raise DownloadError(
                            "tentativa primaria bloqueada: "
                            f"{primary_detail}; fallback com cookies falhou: "
                            f"{fallback_detail}"
                        ) from fallback_exc
                    except Exception as fallback_exc:
                        fallback_detail = web_engine._safe_yt_dlp_diagnostic(fallback_exc)
                        print(
                            f"YT_DLP_AUTH id={video_id} fallback=FAIL reason="
                            f"{fallback_detail}",
                            flush=True,
                        )
                        raise DownloadError(
                            "tentativa primaria bloqueada: "
                            f"{primary_detail}; fallback com cookies falhou: "
                            f"{fallback_detail}"
                        ) from fallback_exc
                    print(f"YT_DLP_AUTH id={video_id} fallback=SUCCESS", flush=True)
                    return result

        return CookieFallbackYoutubeDL, DownloadError

    web_engine._yt_dlp_api = patched_api
    print(
        "YouTube web: cookies de fallback configurados; "
        "PO/default continua sendo a primeira tentativa; Node EJS habilitado."
    )


_original_install_youtube_po_patch = resolver._install_youtube_po_patch
_original_resolver_inspect_candidate = resolver._inspect_candidate
_original_resolver_score_record = resolver._score_record


def _install_po_then_cookie() -> None:
    if getattr(web_engine, "_youtube_auth_configured", False):
        return
    _original_install_youtube_po_patch()
    _install_cookie_fallback()
    web_engine._youtube_auth_configured = True


def _inspect_candidate_with_safe_video_fallback(
    project_root: Path,
    slot: dict,
    candidate: dict,
    index: int,
):
    result, inspection, reasons = _original_resolver_inspect_candidate(
        project_root,
        slot,
        candidate,
        index,
    )
    if result.kind != "video":
        return result, inspection, reasons

    block_reason = video_fallback_block_reason(
        semantic_fit=resolver._semantic_fit(candidate),
        best_semantic_rank=resolver._best_explicit_semantic_rank(slot),
        practically_static=bool(inspection.is_practically_static),
    )
    if block_reason:
        print(
            f"VIDEO_FALLBACK_BLOCK slot={slot.get('id', '<sem_id>')} "
            f"id={_candidate_log_id(candidate, index)} reason={block_reason}",
            flush=True,
        )
        raise RuntimeError(block_reason)

    return result, inspection, reasons


def _score_record_with_episode_reuse_penalty(
    index: int,
    candidate: dict,
    result,
    inspection,
    reasons: list[str],
) -> dict:
    record = _original_resolver_score_record(
        index,
        candidate,
        result,
        inspection,
        reasons,
    )
    if result.kind != "video":
        return record

    source_identity = resolver._video_source_identity(candidate)
    prior_uses = len(resolver.SELECTED_VIDEO_SEGMENTS.get(source_identity, [])) if source_identity else 0
    penalty = reuse_penalty_for_prior_uses(prior_uses)
    score_before_penalty = float(record.get("selection_score") or 0.0)
    score_after_penalty = round(max(0.0, score_before_penalty - penalty), 2)

    record["selection_score_before_episode_reuse_penalty"] = score_before_penalty
    record["current_episode_reuse_count"] = prior_uses
    record["current_episode_reuse_penalty"] = -penalty
    record["selection_score"] = score_after_penalty

    if penalty > 0:
        print(
            f"VIDEO_REUSE_PENALTY episode={resolver.CURRENT_EPISODE or '<episode>'} "
            f"id={_candidate_log_id(candidate, index)} prior_uses={prior_uses} "
            f"penalty=-{penalty:.1f} score={score_before_penalty:.2f}->{score_after_penalty:.2f}",
            flush=True,
        )

    return record


resolver._install_youtube_po_patch = _install_po_then_cookie
resolver._inspect_candidate = _inspect_candidate_with_safe_video_fallback
resolver._score_record = _score_record_with_episode_reuse_penalty


if __name__ == "__main__":
    raise SystemExit(resolver.main())
