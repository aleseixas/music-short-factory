from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlparse

import requests


DOWNLOAD_HEADERS = {
    "User-Agent": (
        "MusicShortFactory/8.1 "
        "(+https://github.com/aleseixas/music-short-factory; remote-media-cache)"
    ),
    "Accept": "video/*,audio/*,application/octet-stream,*/*;q=0.5",
}
SAME_HOST_INTERVAL_SECONDS = 0.25
_HOST_SCHEDULE_LOCK = threading.Lock()
_HOST_NEXT_REQUEST_AT: dict[str, float] = {}


def media_cache_directory(
    project_root: Path,
    cache_root: Path | None,
    category: str,
) -> Path:
    if category not in {"video", "music", "sfx"}:
        raise RuntimeError(f"Categoria de cache de midia desconhecida: {category!r}.")
    project_root = project_root.resolve()
    configured_root = cache_root or Path("cache")
    root = (
        configured_root
        if configured_root.is_absolute()
        else project_root / configured_root
    ).resolve()
    try:
        root.relative_to(project_root)
    except ValueError as exc:
        raise RuntimeError(f"Cache de {category} fora do projeto: {root}") from exc
    return root / category


def download_to_cache(
    url: str,
    cache_dir: Path,
    relative_file: str,
    label: str,
    *,
    attempts: int = 3,
) -> Path:
    """Download one direct media URL atomically, or reuse its non-empty cache file."""
    if attempts < 1:
        raise RuntimeError("A quantidade de tentativas de download precisa ser positiva.")

    destination = _cache_destination(cache_dir, relative_file, label)
    _validate_direct_file_url(url, destination.suffix, label)
    if destination.is_file():
        if destination.stat().st_size <= 0:
            raise RuntimeError(f"Arquivo vazio no cache para {label}: {destination}")
        return destination
    if destination.exists():
        raise RuntimeError(f"Destino de cache invalido para {label}: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    host = (urlparse(url.strip()).hostname or "").casefold()
    last_detail = "erro desconhecido"
    for attempt in range(1, attempts + 1):
        partial.unlink(missing_ok=True)
        response = None
        retry_delay: float | None = None
        try:
            _wait_for_host_slot(host)
            response = requests.get(
                url,
                headers=DOWNLOAD_HEADERS,
                timeout=(10, 120),
                allow_redirects=True,
                stream=True,
            )
            status = int(response.status_code)
            if status == 429:
                retry_delay = _retry_after_seconds(
                    getattr(response, "headers", {}).get("Retry-After"),
                    attempt,
                )
                raise RuntimeError("HTTP 429")
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")

            written = 0
            with partial.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    written += len(chunk)
            if written <= 0:
                raise RuntimeError("resposta vazia")
            partial.replace(destination)
            return destination
        except RuntimeError as exc:
            last_detail = str(exc)
        except requests.RequestException as exc:
            last_detail = type(exc).__name__
        except OSError as exc:
            last_detail = f"{type(exc).__name__}: {exc}"
        finally:
            partial.unlink(missing_ok=True)
            if response is not None:
                response.close()
        if attempt < attempts:
            delay = retry_delay or _progressive_backoff_seconds(attempt)
            _defer_host(host, delay)
            time.sleep(delay)

    raise RuntimeError(
        f"Falha ao baixar {label} para o cache apos {attempts} tentativa(s): "
        f"{last_detail}."
    )


def _progressive_backoff_seconds(attempt: int) -> float:
    return min(8.0, float(2 ** (attempt - 1)))


def _retry_after_seconds(raw_value: object, attempt: int) -> float:
    fallback = _progressive_backoff_seconds(attempt)
    if raw_value is None:
        return fallback
    value = str(raw_value).strip()
    if not value:
        return fallback
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return fallback
    if not math.isfinite(seconds) or seconds < 0:
        return fallback
    return max(SAME_HOST_INTERVAL_SECONDS, seconds)


def _wait_for_host_slot(host: str) -> None:
    with _HOST_SCHEDULE_LOCK:
        now = time.monotonic()
        scheduled = max(now, _HOST_NEXT_REQUEST_AT.get(host, now))
        _HOST_NEXT_REQUEST_AT[host] = scheduled + SAME_HOST_INTERVAL_SECONDS
    delay = scheduled - now
    if delay > 0:
        time.sleep(delay)


def _defer_host(host: str, delay: float) -> None:
    with _HOST_SCHEDULE_LOCK:
        now = time.monotonic()
        _HOST_NEXT_REQUEST_AT[host] = max(
            _HOST_NEXT_REQUEST_AT.get(host, now),
            now + delay,
        )


def _cache_destination(cache_dir: Path, relative_file: str, label: str) -> Path:
    value = relative_file.strip()
    relative = Path(value)
    if (
        not value
        or relative.is_absolute()
        or bool(relative.drive)
        or ".." in relative.parts
        or "://" in value
    ):
        raise RuntimeError(f"Caminho de cache invalido para {label}: {relative_file!r}.")

    cache_root = cache_dir.resolve()
    destination = (cache_root / relative).resolve()
    try:
        destination.relative_to(cache_root)
    except ValueError as exc:
        raise RuntimeError(
            f"Caminho de cache fora da pasta permitida para {label}: {relative_file!r}."
        ) from exc
    return destination


def _validate_direct_file_url(url: str, expected_suffix: str, label: str) -> None:
    value = url.strip()
    parsed = urlparse(value)
    url_suffix = Path(unquote(parsed.path)).suffix.lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or not Path(unquote(parsed.path)).name
        or not expected_suffix
        or url_suffix != expected_suffix.lower()
    ):
        raise RuntimeError(
            f"URL invalida para {label}: use uma URL HTTP(S) direta para um arquivo "
            f"{expected_suffix or 'de midia'} sem credenciais embutidas."
        )
