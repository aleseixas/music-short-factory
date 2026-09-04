from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.parse import urlparse

from engine.visual_search import OpenverseImageProvider, WikimediaCommonsProvider, search_visual
from engine.visual_search_web import DuckDuckGoWebImageProvider, YouTubeWebVideoProvider, candidate_rights_status

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SLOTS = [
    {"id":"img_hook","kind":"image","queries":["Michael Jackson 1980s portrait performance","Michael Jackson Beat It era"],"baseline":"Michael Jackson 1988"},
    {"id":"img_quincy","kind":"image","queries":["Quincy Jones recording studio 1982","Quincy Jones Thriller studio"],"baseline":"Quincy Jones studio"},
    {"id":"img_michael","kind":"image","queries":["Michael Jackson Thriller era portrait","Michael Jackson 1982 studio"],"baseline":"Michael Jackson"},
    {"id":"vid_message","kind":"video","queries":["Michael Jackson Beat It anti violence gangs music video","Beat It Michael Jackson street fight scene"],"baseline":"street fight film"},
    {"id":"vid_twist","kind":"video","queries":["Michael Jackson Beat It studio making of","Thriller recording studio Michael Jackson"],"baseline":"recording studio music"},
    {"id":"vid_eddie_call","kind":"video","queries":["Eddie Van Halen Beat It story interview","Eddie Van Halen Michael Jackson Beat It solo"],"baseline":"electric guitar performance"},
    {"id":"img_trote","kind":"image","queries":["Eddie Van Halen 1984 live photo","Eddie Van Halen portrait 1980s"],"baseline":"Eddie Van Halen 1984"},
    {"id":"vid_free","kind":"video","queries":["Eddie Van Halen Beat It no payment interview","Eddie Van Halen Beat It recording story"],"baseline":"guitar recording studio"},
    {"id":"vid_edit","kind":"video","queries":["Eddie Van Halen changed Beat It solo section","Beat It Eddie Van Halen solo recording"],"baseline":"guitar solo performance"},
    {"id":"vid_tape","kind":"video","queries":["Steve Lukather Beat It tape story","Beat It tape edit recording story"],"baseline":"audio tape recorder"},
    {"id":"vid_lukather","kind":"video","queries":["Steve Lukather Beat It recording interview","Steve Lukather Michael Jackson Beat It story"],"baseline":"guitar studio recording"},
    {"id":"vid_porcaro","kind":"video","queries":["Jeff Porcaro Beat It recording","Jeff Porcaro Michael Jackson studio"],"baseline":"drummer recording studio"},
    {"id":"vid_heavy","kind":"video","queries":["Steve Lukather Beat It guitar too heavy Quincy Jones","Beat It heavy guitar Lukather interview"],"baseline":"heavy metal guitar performance"},
    {"id":"vid_reduce","kind":"video","queries":["Beat It guitar arrangement Steve Lukather","Quincy Jones Beat It guitar production"],"baseline":"guitar amplifier performance"},
    {"id":"vid_fusion","kind":"video","queries":["Michael Jackson Beat It live rock performance","Beat It live concert Michael Jackson guitar"],"baseline":"rock pop concert"},
    {"id":"img_drumcase","kind":"image","queries":["Michael Jackson Thriller credits drum case beater","Thriller album recording credits Michael Jackson"],"baseline":"drum case music"},
    {"id":"vid_meaning","kind":"video","queries":["Michael Jackson Beat It choreography live","Beat It dance performance Michael Jackson"],"baseline":"dance performance stage"},
    {"id":"vid_live","kind":"video","queries":["Eddie Van Halen Michael Jackson Beat It live 1984","Beat It Victory Tour Eddie Van Halen Michael Jackson"],"baseline":"electric guitar live concert"},
    {"id":"vid_crossover","kind":"video","queries":["Michael Jackson Beat It concert crowd","Beat It live crowd Michael Jackson"],"baseline":"concert crowd music"},
    {"id":"vid_number_one","kind":"video","queries":["Michael Jackson Beat It 1983 performance","Beat It Michael Jackson television performance"],"baseline":"music performance stage"},
    {"id":"img_grammy","kind":"image","queries":["Michael Jackson Grammy Awards 1984","Michael Jackson Grammys 1984 photo"],"baseline":"Michael Jackson Grammy 1984"},
    {"id":"img_payoff","kind":"image","queries":["Michael Jackson live 1988 portrait","Michael Jackson performance photo 1980s"],"baseline":"Michael Jackson performance"},
    {"id":"vid_final_thought","kind":"video","queries":["Michael Jackson Beat It live finale","Beat It live Michael Jackson final guitar"],"baseline":"concert finale music"},
]

GENERIC_FALLBACKS = {
    "video": ["rock concert live","electric guitar performance","recording studio music","drummer live performance","concert crowd stage","music rehearsal band","guitar solo live","dance performance stage"],
    "image": ["music artist portrait","recording studio musician","guitarist concert","music awards"],
}


def result_key(result) -> str:
    return "|".join([str(result.kind), str(result.search_provider), str(result.provider_id or ""), str(result.source_page_url or ""), str(result.download_url or "")])


def candidate_payload(result, slot_id: str, rank: int, start_seconds: float = 0.0) -> dict:
    youtube = str(result.search_provider) == "youtube_web"
    suffix = ".mp4" if result.kind == "video" else (Path(result.suggested_file or "").suffix or ".jpg")
    file_name = f"{slot_id}-{rank}{suffix}"
    direct_or_page = result.source_page_url if youtube else result.download_url
    payload = {
        "editorial_rank": rank,
        "name": str(result.name or f"{slot_id}-{rank}"),
        "kind": result.kind,
        "file": file_name,
        "source_page_url": str(result.source_page_url or direct_or_page or ""),
        "source": str(result.source or "web"),
        "search_provider": str(result.search_provider or "authoring-pool"),
        "provider_id": str(result.provider_id or f"{slot_id}-{rank}"),
        "creator": str(result.creator or ""),
        "credit": str(result.attribution or result.creator or result.source or "web"),
        "license": str(result.license or ""),
        "rights_status": candidate_rights_status(result),
        "focus": {"x": 0.5, "y": 0.42},
    }
    if direct_or_page:
        payload["url"] = str(direct_or_page)
    if result.width:
        payload["width"] = int(result.width)
    if result.height:
        payload["height"] = int(result.height)
    if result.duration_seconds:
        payload["duration_seconds"] = float(result.duration_seconds)
    if result.kind == "video" and start_seconds > 0:
        payload["source_start_seconds"] = float(start_seconds)
    return payload


def find_baseline(slot: dict, used: set[str]):
    commons = WikimediaCommonsProvider()
    queries = [slot["baseline"], *GENERIC_FALLBACKS[slot["kind"]]]
    for query in queries:
        try:
            results = commons.search(query, slot["kind"], limit=12)
        except Exception:
            continue
        for result in results:
            key = result_key(result)
            if key in used or not result.download_url:
                continue
            suffix = Path(result.suggested_file or urlparse(result.download_url).path).suffix.lower()
            if slot["kind"] == "video" and suffix not in {".mp4",".mov",".webm",".mkv",".avi",".m4v"}:
                continue
            if slot["kind"] == "image" and suffix not in {".jpg",".jpeg",".png",".webp"}:
                continue
            used.add(key)
            return result
    raise RuntimeError(f"Could not find direct baseline for {slot['id']}")


def build_candidates(slot: dict, used: set[str]) -> list[dict]:
    if slot["kind"] == "video":
        providers = (YouTubeWebVideoProvider(), WikimediaCommonsProvider())
    else:
        providers = (DuckDuckGoWebImageProvider(), WikimediaCommonsProvider(), OpenverseImageProvider())

    queries = list(slot["queries"])
    fallbacks = GENERIC_FALLBACKS[slot["kind"]]
    results = []
    tried = set()

    for extra in [None, *fallbacks]:
        active_queries = queries if extra is None else [extra]
        query_sig = tuple(active_queries)
        if query_sig in tried:
            continue
        tried.add(query_sig)
        try:
            report = search_visual(PROJECT_ROOT, active_queries, slot["kind"], include_external=True, limit=24, providers=providers)
        except Exception:
            continue
        for result in report.results:
            key = result_key(result)
            if key in used:
                continue
            if slot["kind"] == "video" and not (result.search_provider == "youtube_web" or result.download_url):
                continue
            if slot["kind"] == "image" and not result.download_url:
                continue
            used.add(key)
            results.append(result)
            if len(results) >= 5:
                break
        if len(results) >= 5:
            break

    if len(results) < 5:
        raise RuntimeError(f"{slot['id']}: only {len(results)} unique candidates found")

    start = 5.0 if slot["kind"] == "video" else 0.0
    return [candidate_payload(result, slot["id"], rank, start) for rank, result in enumerate(results[:5], start=1)]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_beat_it_v3_visual_pool.py <slug>")
    slug = sys.argv[1].strip()
    episode_dir = PROJECT_ROOT / "episodes" / slug
    if not episode_dir.is_dir():
        raise RuntimeError(f"episode not found: {slug}")

    used_candidates: set[str] = set()
    used_baselines: set[str] = set()
    assets = []
    slots_payload = []

    for slot in SLOTS:
        baseline = find_baseline(slot, used_baselines)
        baseline_suffix = Path(baseline.suggested_file or urlparse(baseline.download_url).path).suffix.lower()
        assets.append({
            "id": slot["id"],
            "file": f"{slot['id']}{baseline_suffix}",
            "url": str(baseline.download_url),
            "credit": str(baseline.attribution or baseline.creator or baseline.source or "Wikimedia Commons"),
            "license": str(baseline.license or ""),
            "focus": {"x": 0.5, "y": 0.42},
        })
        candidates = build_candidates(slot, used_candidates)
        slots_payload.append({
            "id": slot["id"],
            "required_seconds": 4.0,
            "crossfade_seconds": 0.2,
            "inspect_top": 4,
            "min_visual_score": 35 if slot["kind"] == "image" else 40,
            "candidates": candidates,
        })
        print(f"{slot['id']}: baseline + {len(candidates)} candidates")

    assets.append({"id":"end_card_brand","file":"end_card_template.jpg","credit":"Além do Hit","license":"","focus":{"x":0.5,"y":0.5}})
    total = sum(len(slot["candidates"]) for slot in slots_payload)
    if total != 115:
        raise RuntimeError(f"expected 115 visual candidates, got {total}")

    (episode_dir / "assets.json").write_text(json.dumps({"schema_version":1,"assets":assets}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (episode_dir / "visual_candidates.json").write_text(json.dumps({"schema_version":1,"slots":slots_payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Visual pool ready: {total} candidates across {len(slots_payload)} slots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
