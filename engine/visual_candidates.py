"""Network-free authorship gate for reusable, resolvable visual candidate pools."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import VIDEO_ASSET_EXTENSIONS
from .youtube import YOUTUBE_HOSTS, extract_youtube_video_id, is_youtube_video_id


IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def _https_url(raw: object) -> str:
    value = str(raw or "").strip()
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
        if (parsed.scheme.casefold() == "https" and host
                and not parsed.username and not parsed.password
                and host not in {"localhost", "localhost.localdomain"}
                and not host.endswith(".local")):
            return value
    except ValueError:
        pass
    return ""


def normalize_visual_candidate(candidate: dict) -> dict:
    """Preserve discovery metadata and provide an acquisition locator or fail early.

    A search title/internal ordinal is not a locator. Only an explicit YouTube
    provider/video ID may reconstruct a missing public URL; no search is invented.
    Runtime resolvers call this per candidate, so one bad record cannot hide the
    remaining candidates. The authorship gate validates *all* authored records.
    """
    if not isinstance(candidate, dict):
        raise ValueError("candidato deve ser objeto")
    normalized = dict(candidate)
    kind = str(candidate.get("kind") or "").strip().casefold()
    if kind not in {"image", "video"}:
        raise ValueError("kind deve ser image ou video")
    normalized["kind"] = kind
    technical = candidate.get("technical_metadata")
    if isinstance(technical, dict):
        for field in ("width", "height", "duration_seconds", "file_format", "mime_type"):
            if technical.get(field) is not None:
                normalized.setdefault(field, technical[field])
    asset_entry = candidate.get("candidate_asset_entry")
    if isinstance(asset_entry, dict):
        for field in ("url", "file", "credit", "license", "focus"):
            if asset_entry.get(field) is not None:
                normalized.setdefault(field, asset_entry[field])
    url = _https_url(normalized.get("url") or candidate.get("download_url"))
    page = _https_url(candidate.get("source_page_url"))
    provider = str(candidate.get("search_provider") or candidate.get("provider") or "").casefold()
    source = str(candidate.get("source") or "").casefold()
    urls = [item for item in (url, page) if item]
    youtube = kind == "video" and (
        provider in {"youtube", "youtube_web"} or source == "youtube"
        or any((urlsplit(item).hostname or "").casefold() in YOUTUBE_HOSTS for item in urls)
    )
    if youtube:
        ids = {video_id for item in urls if (video_id := extract_youtube_video_id(item))}
        for explicit_id in (candidate.get("video_id"), candidate.get("provider_id")):
            if is_youtube_video_id(explicit_id):
                ids.add(explicit_id)
        if len(ids) != 1:
            raise ValueError("candidato YouTube exige URL de video ou VIDEO_ID valido e consistente (11 caracteres); id interno/name nao e localizador")
        video_id = ids.pop()
        canonical = f"https://www.youtube.com/watch?v={video_id}"
        normalized.update(url=canonical, source_page_url=canonical,
                          provider_id=video_id, video_id=video_id,
                          search_provider="youtube_web")
        normalized.setdefault("source", "youtube")
        return normalized
    if not url and page:
        url = page
    if not url:
        raise ValueError(f"candidato {kind} exige URL HTTPS direta ou localizador web suportado; id interno/name nao e localizador")
    suffix = Path(str(normalized.get("file") or unquote(urlsplit(url).path))).suffix.casefold()
    supported = VIDEO_ASSET_EXTENSIONS if kind == "video" else IMAGE_SUFFIXES
    if suffix not in supported:
        raise ValueError(f"candidato {kind} exige arquivo de midia suportado, nao pagina HTML sem provider")
    normalized["url"] = url
    normalized.setdefault("source_page_url", page or url)
    normalized.setdefault("source", "web")
    normalized.setdefault("search_provider", "authoring-pool")
    return normalized


def validate_visual_candidate_pool(pool: dict) -> dict:
    """Return a normalized copy. Reject placeholder pools before downloading."""
    slots = pool.get("slots") if isinstance(pool, dict) else None
    if (not isinstance(pool, dict) or pool.get("schema_version") != 1
            or not isinstance(slots, list) or not slots):
        raise ValueError("schema_version=1 e slots nao vazios sao obrigatorios")
    normalized_slots = []
    seen = set()
    for slot_index, slot in enumerate(slots, start=1):
        slot_id = str(slot.get("id") or "").strip() if isinstance(slot, dict) else ""
        if not slot_id or slot_id in seen:
            raise ValueError(f"slot {slot_index}: id unico nao vazio obrigatorio")
        seen.add(slot_id)
        candidates = slot.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"slot {slot_id}: candidatos reais nao vazios obrigatorios")
        normalized_candidates = []
        for index, candidate in enumerate(candidates, start=1):
            try:
                normalized_candidates.append(normalize_visual_candidate(candidate))
            except ValueError as exc:
                raise ValueError(f"slot {slot_id}, candidato {index}: {exc}") from exc
        normalized_slots.append(dict(slot, candidates=normalized_candidates))
    return dict(pool, slots=normalized_slots)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pool", type=Path, help="episodes/<slug>/visual_candidates.json")
    args = parser.parse_args()
    try:
        pool = validate_visual_candidate_pool(json.loads(args.pool.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as exc:
        print(f"VISUAL_CANDIDATE_POOL_INVALID: {exc}")
        return 1
    print(f"VISUAL_CANDIDATE_POOL_VALID: {len(pool['slots'])} slot(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
