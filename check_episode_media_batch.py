from __future__ import annotations

import argparse
import json
from pathlib import Path

from check_episode_media import (
    _assert_background_is_fresh,
    _classify_preflight_failure,
    _single_line,
    _visual_aliases,
)
from engine.assets import AssetManager
from engine.config import load_project_config
from engine.episode import load_episode
from engine.music import resolve_background_music


def _error_record(exc: Exception, slug: str, *, scope: str = "") -> dict[str, object]:
    code, recoverable, target, action = _classify_preflight_failure(exc, slug)
    record: dict[str, object] = {
        "code": code,
        "class": exc.__class__.__name__,
        "detail": _single_line(exc) or exc.__class__.__name__,
        "recoverable": recoverable,
        "target": target,
        "recommended_action": action,
        "same_slug": slug or "UNKNOWN",
    }
    if scope:
        record["scope"] = scope
    return record


def _emit_batch(errors: list[dict[str, object]], slug: str) -> int:
    if not errors:
        print("MEDIA_PREFLIGHT_RESULT=PASS")
        print("MEDIA_PREFLIGHT_ERROR_COUNT=0")
        print("MEDIA_PREFLIGHT_SAME_SLUG=" + slug)
        return 0

    payload = json.dumps(errors, ensure_ascii=False, separators=(",", ":"))
    first = errors[0]

    print("MEDIA_PREFLIGHT_RESULT=FAIL")
    print(f"MEDIA_PREFLIGHT_ERROR_COUNT={len(errors)}")
    print(f"MEDIA_PREFLIGHT_ERRORS_JSON={payload}")
    print(f"MEDIA_PREFLIGHT_ERROR_CODE={first['code']}")
    print(f"MEDIA_PREFLIGHT_ERROR_CLASS={first['class']}")
    print(f"MEDIA_PREFLIGHT_ERROR_DETAIL={first['detail']}")
    print(
        "MEDIA_PREFLIGHT_RECOVERABLE="
        + ("true" if bool(first.get("recoverable")) else "false")
    )
    print(f"MEDIA_PREFLIGHT_TARGET={first['target']}")
    print(f"MEDIA_PREFLIGHT_RECOMMENDED_ACTION={first['recommended_action']}")
    print(f"MEDIA_PREFLIGHT_SAME_SLUG={slug or 'UNKNOWN'}")

    for index, error in enumerate(errors, start=1):
        print(
            "MEDIA_PREFLIGHT_ERROR_ITEM="
            + json.dumps({"index": index, **error}, ensure_ascii=False, separators=(",", ":"))
        )
    return 1


def _collect_visual_structure_errors(episode, slug: str) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    seen: dict[tuple[str, str], tuple[str, str]] = {}

    for shot in episode.shots:
        asset = episode.assets.get(shot.asset_id)
        if asset is None:
            exc = RuntimeError(
                f"Shot {shot.id!r} referencia asset inexistente {shot.asset_id!r}."
            )
            errors.append(_error_record(exc, slug, scope=f"shot:{shot.id}"))
            continue

        aliases = _visual_aliases(asset)
        duplicate_found = False
        for identity in aliases:
            previous = seen.get(identity)
            if previous is None:
                continue
            previous_shot, previous_asset = previous
            identity_kind, identity_value = identity
            exc = RuntimeError(
                "INTRA_EPISODE_VISUAL_REUSE_BLOCKED: o mesmo visual foi usado "
                "mais de uma vez dentro do episodio. "
                f"Shot {shot.id!r} (asset={asset.id!r}) repete o visual de "
                f"{previous_shot!r} (asset={previous_asset!r}); "
                f"identidade={identity_kind}:{identity_value}. "
                "Cada shot principal deve usar uma imagem ou video diferente."
            )
            errors.append(_error_record(exc, slug, scope=f"shot:{shot.id}"))
            duplicate_found = True
            break

        if not duplicate_found:
            for identity in aliases:
                seen[identity] = (shot.id, asset.id)

    if not errors:
        print(
            "[preflight] intra-episode visual uniqueness OK: "
            f"{len(episode.shots)} shot(s) sem reutilizacao"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate all independent episode media checks and report failures as one batch."
    )
    parser.add_argument("episode", help="Episode slug")
    args = parser.parse_args()
    slug = args.episode.strip()

    root = Path(__file__).resolve().parent
    errors: list[dict[str, object]] = []

    try:
        config = load_project_config(root / "config" / "config.json")
        episodes_dir = Path(config.paths.episodes_dir)
        cache_dir = Path(config.paths.cache_dir)
        if not cache_dir.is_absolute():
            cache_dir = root / cache_dir
        episode = load_episode(root, config.paths.episodes_dir, slug)
    except Exception as exc:
        return _emit_batch([_error_record(exc, slug, scope="episode-load")], slug)

    work_dir = root / config.paths.work_dir / ".media-preflight" / episode.name
    video_cache = cache_dir / "video"
    try:
        manager = AssetManager(
            assets_dir=episode.assets_dir,
            work_dir=work_dir,
            width=config.render.width,
            height=config.render.height,
            scale=1,
            allowed_assets_root=episode.directory,
            video_cache_dir=video_cache,
        )
    except Exception as exc:
        return _emit_batch([_error_record(exc, slug, scope="asset-manager")], slug)

    print("[preflight] validating intra-episode visual structure...")
    errors.extend(_collect_visual_structure_errors(episode, slug))

    print(f"[preflight] validating {len(episode.assets)} episode assets independently...")
    asset_failures = 0
    for asset in episode.assets.values():
        try:
            manager.ensure(asset)
        except Exception as exc:
            asset_failures += 1
            errors.append(_error_record(exc, slug, scope=f"asset:{asset.id}"))
    if asset_failures:
        print(f"[preflight] asset failures collected: {asset_failures}")
    else:
        print("[preflight] assets OK")

    resolved_music = None
    try:
        resolved_music = resolve_background_music(
            root,
            episode.background_music,
            episode.name,
            cache_root=cache_dir,
        )
    except Exception as exc:
        errors.append(_error_record(exc, slug, scope="background-music-resolve"))

    if resolved_music is None:
        if episode.background_music is None:
            print("[preflight] background music: none")
    else:
        print(
            f"[preflight] background music OK: profile={resolved_music.profile} "
            f"file={resolved_music.path}"
        )
        if episode.background_music is not None:
            try:
                _assert_background_is_fresh(
                    root,
                    episodes_dir,
                    cache_dir,
                    episode.name,
                    episode.background_music,
                    resolved_music.path,
                )
            except Exception as exc:
                errors.append(_error_record(exc, slug, scope="background-music-freshness"))

    # De-duplicate identical diagnostics while preserving deterministic order.
    unique: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for error in errors:
        key = (
            str(error.get("code") or ""),
            str(error.get("detail") or ""),
            str(error.get("scope") or ""),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(error)

    return _emit_batch(unique, slug)


if __name__ == "__main__":
    raise SystemExit(main())
