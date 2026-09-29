from __future__ import annotations

import argparse
import json
import os
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
from engine.visual_candidates import validate_visual_candidate_pool, validate_visual_candidate_coverage
from engine.media_validation import collect_partial_episode
from engine.models import TimelineScene
from engine.visual_search import assess_trim
from engine.utils import validate_slug


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


def _explicit_error_record(
    *,
    code: str,
    slug: str,
    detail: str,
    target: str,
    recommended_action: str,
    scope: str,
    recoverable: bool = True,
) -> dict[str, object]:
    return {
        "code": code,
        "class": "RuntimeError",
        "detail": _single_line(detail),
        "recoverable": recoverable,
        "target": target,
        "recommended_action": recommended_action,
        "same_slug": slug or "UNKNOWN",
        "scope": scope,
    }


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


def _collect_visual_authoring_errors(episode, slug: str) -> list[dict[str, object]]:
    errors: list[dict[str, object]] = []
    episode_root = f"episodes/{slug}"
    pool_path = episode.directory / "visual_candidates.json"

    # A diagnostic-only partial episode may omit a broken first shot. Preserve
    # the authored order so it never turns the second shot into the opening.
    shot_slots = [(shot.id, shot.asset_id) for shot in episode.shots]
    try:
        authored = json.loads((episode.directory / "timeline.json").read_text(encoding="utf-8"))["shots"]
        if isinstance(authored, list) and authored and all(isinstance(shot, dict) and shot.get("id") and shot.get("asset") for shot in authored):
            shot_slots = [(str(shot["id"]), str(shot["asset"])) for shot in authored]
    except (OSError, ValueError, KeyError, TypeError):
        pass  # The original parser failure stays in the diagnostic batch.

    pool_slots: list[dict] = []
    if not pool_path.exists():
        errors.append(
            _explicit_error_record(
                code="VISUAL_CANDIDATE_POOL_MISSING",
                slug=slug,
                detail=(
                    "visual_candidates.json ausente. O episodio nao pode usar apenas assets-base "
                    "e seguir para publicacao sem passar pela selecao visual estruturada."
                ),
                target=f"{episode_root}/visual_candidates.json",
                recommended_action=(
                    "Create a real web-first visual_candidates.json for this same episode, including video candidates, "
                    "then rerun media preflight. Do not create the publish queue yet."
                ),
                scope="visual-candidate-pool",
            )
        )
    else:
        try:
            pool = validate_visual_candidate_pool(json.loads(pool_path.read_text(encoding="utf-8-sig")))
            pool_slots = pool["slots"]
            validate_visual_candidate_coverage(pool, shot_slots)
            print(f"[preflight] visual candidate pool OK: {len(pool_slots)} slot(s)")
        except Exception as exc:
            errors.append(
                _explicit_error_record(
                    code="VISUAL_CANDIDATE_POOL_INVALID",
                    slug=slug,
                    detail=f"visual_candidates.json invalido: {_single_line(exc)}",
                    target=f"{episode_root}/visual_candidates.json",
                    recommended_action=(
                        "Fix visual_candidates.json for this same episode with real usable media URLs/locators, "
                        "then rerun media preflight."
                    ),
                    scope="visual-candidate-pool",
                )
            )

    if not episode.shots:
        return errors

    first_shot = next((shot for shot in episode.shots if shot_slots and shot.id == shot_slots[0][0]), None)
    if first_shot is None:
        return errors  # The actual opening's parser/reference error is already reported.
    first_asset = episode.assets.get(first_shot.asset_id)
    if first_asset is None or first_asset.is_video:
        if first_asset is not None:
            print(
                "[preflight] opening editorial visual OK: "
                f"shot={first_shot.id} asset={first_asset.id} media_type=video"
            )
        return errors

    matching_slot = next(
        (
            slot
            for slot in pool_slots
            if str(slot.get("id") or "").strip() in {first_shot.id, first_shot.asset_id}
        ),
        None,
    )
    video_candidate_count = 0
    if matching_slot is not None:
        candidates = matching_slot.get("candidates")
        if isinstance(candidates, list):
            video_candidate_count = sum(
                1
                for candidate in candidates
                if isinstance(candidate, dict)
                and str(candidate.get("kind") or "").strip().casefold() == "video"
            )

    errors.append(
        _explicit_error_record(
            code="FIRST_EDITORIAL_VISUAL_NOT_VIDEO",
            slug=slug,
            detail=(
                "O primeiro take editorial depois da capa tecnica precisa ser video real, mas "
                f"shot={first_shot.id!r} usa asset={first_asset.id!r} ({first_asset.file}) "
                f"do tipo imagem. opening_video_candidates={video_candidate_count}."
            ),
            target=(
                f"{episode_root}/visual_candidates.json + {episode_root}/assets.json + "
                f"{episode_root}/timeline.json"
            ),
            recommended_action=(
                "Select and resolve a real video candidate for the first editorial shot of this same episode. "
                "If the opening slot has no video candidate, add relevant video candidates first; then rerun media preflight."
            ),
            scope=f"shot:{first_shot.id}",
        )
    )
    return errors


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


def validate_episode_media(
    root: Path, slug: str, *, request_id: str = "", stage: str = "LOCAL_VALIDATION",
    commit_sha: str = "",
) -> list[dict[str, object]]:
    """The single acceptance gate for authoring, local preflight and CI.

    A load failure remains a failure, but valid independent entries are checked
    too so all usable repair targets are returned in the same pass.
    """
    root = root.resolve()
    errors: list[dict[str, object]] = []
    def finish(items):
        return [_with_context(item, slug, request_id, stage, commit_sha) for item in items]
    try:
        slug = validate_slug(slug)
        config = load_project_config(root / "config" / "config.json")
        episodes_dir = Path(config.paths.episodes_dir)
        cache_dir = Path(config.paths.cache_dir)
        if not cache_dir.is_absolute():
            cache_dir = root / cache_dir
    except Exception as exc:
        return finish([_error_record(exc, slug, scope="configuration")])
    try:
        episode = load_episode(root, config.paths.episodes_dir, slug)
    except Exception as exc:
        errors.append(_error_record(exc, slug, scope="episode-load"))
        try:
            episode, independent = collect_partial_episode(root, config.paths.episodes_dir, slug)
            errors.extend(_error_record(error, slug, scope=scope) for scope, error in independent)
        except Exception as partial_exc:
            errors.append(_error_record(partial_exc, slug, scope="episode-inputs"))
            return finish(errors)

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
        return finish(errors + [_error_record(exc, slug, scope="asset-manager")])

    print("[preflight] validating visual authoring contract...")
    errors.extend(_collect_visual_authoring_errors(episode, slug))

    print("[preflight] validating intra-episode visual structure...")
    errors.extend(_collect_visual_structure_errors(episode, slug))

    print(f"[preflight] validating {len(episode.assets)} episode assets independently...")
    asset_failures = 0
    failed_assets = set()
    for asset in episode.assets.values():
        try:
            manager.ensure(asset)
        except Exception as exc:
            asset_failures += 1
            failed_assets.add(asset.id)
            errors.append(_error_record(exc, slug, scope=f"asset:{asset.id}"))
    if asset_failures:
        print(f"[preflight] asset failures collected: {asset_failures}")
    else:
        print("[preflight] assets OK")

    # Reuse the renderer's exact source bounds checks before a render is queued.
    # Bounds and authored duration are known locally; final TTS timing remains
    # checked by this renderer method after synthesis.
    try:
        pool_slots = json.loads((episode.directory / "visual_candidates.json").read_text(encoding="utf-8")).get("slots", [])
    except (OSError, ValueError, AttributeError):
        pool_slots = []  # The independent authoring gate retains the original failure.
    for index, shot in enumerate(episode.shots):
        asset = episode.assets.get(shot.asset_id)
        if asset is None or not asset.is_video or asset.id in failed_assets:
            continue
        try:
            scene = TimelineScene(index, shot, asset, 0, 0, 0, 0)
            info = manager.preflight_video_scene(scene, config.render.fps)
            slot = next((item for item in pool_slots if isinstance(item, dict) and item.get("id") in {shot.id, shot.asset_id}), {})
            freeze = shot.freeze_frame
            trim = assess_trim(info.duration,
                               shot_duration_seconds=slot.get("required_seconds", 8.0),
                               source_start_seconds=shot.source_start_seconds,
                               source_end_seconds=shot.source_end_seconds,
                               crossfade_seconds=slot.get("crossfade_seconds", 0.35),
                               speed=shot.speed, output_fps=config.render.fps,
                               freeze_start_seconds=freeze.start_seconds if freeze else None,
                               freeze_duration_seconds=freeze.duration_seconds if freeze else None)
            if not trim.safe_for_shot:
                raise RuntimeError(f"unsafe_trim shot {shot.id!r}: {trim.reason}; disponivel={trim.available_seconds}s; necessario={trim.required_seconds}s")
            manager.preflight_video_decode(asset, shot.source_start_seconds, trim.required_seconds)
        except Exception as exc:
            errors.append(_error_record(exc, slug, scope=f"shot:{shot.id}"))

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

    return finish(unique)



def _with_context(error, slug, request_id, stage, commit_sha):
    code = str(error.get("code") or "UNCLASSIFIED_ENGINE_ERROR")
    if code in {"REMOTE_MEDIA_RATE_LIMITED", "REMOTE_MEDIA_PROVIDER_ERROR", "REMOTE_MEDIA_TEMPORARY_FAILURE"}:
        error_class = "transient_external"
        prevention = "external"
    elif code in {"MEDIA_TOOLCHAIN_UNAVAILABLE", "UNCLASSIFIED_ENGINE_ERROR"}:
        error_class = "non_recoverable"
        prevention = "local_preflight"
    elif code in {"SHOT_REFERENCES_MISSING_ASSET", "VIDEO_SOURCE_WINDOW_INVALID", "INTRA_EPISODE_VISUAL_REUSE", "EPISODE_SCHEMA_OR_TIMELINE_INVALID"}:
        error_class = "preventable_during_timeline_construction"
        prevention = "timeline_construction"
    else:
        error_class = "preventable_during_asset_selection"
        prevention = "asset_selection"
    return {**error, "error_code": code, "error_class": error_class,
            "prevention_stage": prevention, "stage": stage, "slug": slug,
            "request_id": request_id, "commit_sha": commit_sha}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate every independent media invariant before publication.")
    parser.add_argument("episode", help="Episode slug")
    parser.add_argument("--request-id", default=os.environ.get("PIPELINE_REQUEST_ID", ""))
    parser.add_argument("--commit-sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--stage", default="MEDIA_PREFLIGHT")
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()
    slug = args.episode.strip()
    errors = validate_episode_media(Path(__file__).resolve().parent, slug,
                                    request_id=args.request_id, stage=args.stage,
                                    commit_sha=args.commit_sha)
    report = {"schema_version": 1, "result": "FAIL" if errors else "PASS", "errors": errors,
              "slug": slug, "request_id": args.request_id, "stage": args.stage,
              "commit_sha": args.commit_sha}
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("MEDIA_PREFLIGHT_DIAGNOSTIC_JSON=" + json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return _emit_batch(errors, slug)


if __name__ == "__main__":
    raise SystemExit(main())
