from __future__ import annotations

from copy import deepcopy
from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping


SCHEMA_VERSION = 1
PLATFORMS = ("youtube", "instagram", "facebook", "tiktok")
HASHTAG_RECOMMENDATIONS = {
    "youtube": 5,
    "instagram": 8,
    "facebook": 8,
    "tiktok": 5,
}
PLATFORM_TEXT_LIMITS = {
    "youtube": 5000,
    "instagram": 2200,
    "facebook": 2200,
    "tiktok": 2200,
}
YOUTUBE_TITLE_LIMIT = 100
YOUTUBE_TAGS_LIMIT = 500
FACEBOOK_TITLE_LIMIT = 255
PRIVACY_LEVELS = {
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
}
SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*")


@dataclass(frozen=True)
class PostMetadata:
    path: Path
    data: Mapping[str, Any]

    def for_platform(self, platform: str) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise RuntimeError(f"Plataforma desconhecida: {platform!r}.")
        value = self.data[platform]
        return deepcopy(dict(value))


@dataclass(frozen=True)
class PreparedPost:
    episode: str
    post_path: Path
    cover_path: Path
    post: PostMetadata
    previews: Mapping[str, Mapping[str, Any]]


def load_post(
    path: Path,
    warning_platforms: Sequence[str] | None = None,
) -> PostMetadata:
    data = _load_json(path)
    normalized = normalize_post(data)
    validate_post(normalized, path, warning_platforms=warning_platforms)
    return PostMetadata(path=path, data=normalized)


def normalize_post(data: Mapping[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(dict(data))
    if "facebook" not in normalized:
        instagram = normalized.get("instagram")
        youtube = normalized.get("youtube")
        if isinstance(instagram, Mapping):
            facebook: dict[str, Any] = {
                "caption": str(instagram.get("caption", "")),
                "hashtags": deepcopy(instagram.get("hashtags", [])),
            }
            if isinstance(youtube, Mapping):
                title = str(youtube.get("title", "")).strip()
                if title:
                    facebook["title"] = title
            normalized["facebook"] = facebook
    for platform in PLATFORMS:
        raw = normalized.get(platform)
        if not isinstance(raw, dict):
            continue
        hashtags = raw.get("hashtags")
        if isinstance(hashtags, list):
            cleaned: list[str] = []
            seen: set[str] = set()
            for item in hashtags:
                if not isinstance(item, str):
                    cleaned.append(item)
                    continue
                tag = item.strip().lstrip("#").strip()
                key = tag.casefold()
                if tag and key not in seen:
                    cleaned.append(tag)
                    seen.add(key)
            raw["hashtags"] = cleaned
    return normalized


def validate_post(
    data: Mapping[str, Any],
    path: Path | None = None,
    *,
    warning_platforms: Sequence[str] | None = None,
) -> None:
    label = str(path or "post.json")
    warning_scope = frozenset(PLATFORMS if warning_platforms is None else warning_platforms)
    unknown_warning_platforms = warning_scope.difference(PLATFORMS)
    if unknown_warning_platforms:
        raise RuntimeError(
            "Plataformas de warning desconhecidas: "
            + ", ".join(sorted(unknown_warning_platforms))
        )
    if data.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(
            f"schema_version invalido em {label}: esperado {SCHEMA_VERSION}, "
            f"recebido {data.get('schema_version')!r}."
        )
    _validate_cover(data.get("cover"), label)

    youtube = _platform_object(data, "youtube", label)
    _required_text(youtube, "title", "youtube", label)
    youtube_title = str(youtube["title"]).strip()
    if "youtube" in warning_scope and len(youtube_title) > YOUTUBE_TITLE_LIMIT:
        _editorial_warning(
            f"youtube.title excede {YOUTUBE_TITLE_LIMIT} caracteres em {label}. "
            "A publicacao no YouTube sera validada separadamente."
        )
    _optional_text(youtube, "description", "youtube", label)
    _validate_hashtags(
        youtube,
        "youtube",
        label,
        warn_excess="youtube" in warning_scope,
    )
    privacy = youtube.get("privacy_status", "private")
    if privacy not in {"private", "unlisted", "public"}:
        raise RuntimeError(
            f"youtube.privacy_status invalido em {label}: {privacy!r}. "
            "Use private, unlisted ou public."
        )
    category = str(youtube.get("category_id", "10"))
    if not category.isdigit():
        raise RuntimeError(f"youtube.category_id precisa ser numerico em {label}.")

    instagram = _platform_object(data, "instagram", label)
    _required_text(instagram, "caption", "instagram", label)
    _validate_hashtags(
        instagram,
        "instagram",
        label,
        warn_excess="instagram" in warning_scope,
    )
    _optional_bool(instagram, "share_to_feed", "instagram", label)
    _optional_url(instagram, "video_url", "instagram", label)
    _optional_url(instagram, "cover_url", "instagram", label)
    _optional_nonnegative_int(instagram, "thumb_offset_ms", "instagram", label)

    facebook = _platform_object(data, "facebook", label)
    _required_text(facebook, "caption", "facebook", label)
    _optional_text(facebook, "title", "facebook", label)
    facebook_title = str(facebook.get("title", "")).strip()
    if "facebook" in warning_scope and len(facebook_title) > FACEBOOK_TITLE_LIMIT:
        _editorial_warning(
            f"facebook.title excede {FACEBOOK_TITLE_LIMIT} caracteres em {label}. "
            "A publicacao no Facebook sera validada separadamente."
        )
    _validate_hashtags(
        facebook,
        "facebook",
        label,
        warn_excess="facebook" in warning_scope,
    )

    tiktok = _platform_object(data, "tiktok", label)
    _required_text(tiktok, "caption", "tiktok", label)
    _validate_hashtags(
        tiktok,
        "tiktok",
        label,
        warn_excess="tiktok" in warning_scope,
    )
    privacy_level = tiktok.get("privacy_level", "SELF_ONLY")
    if privacy_level not in PRIVACY_LEVELS:
        raise RuntimeError(
            f"tiktok.privacy_level invalido em {label}: {privacy_level!r}."
        )
    _optional_nonnegative_int(
        tiktok, "video_cover_timestamp_ms", "tiktok", label
    )
    for field in (
        "disable_comment",
        "disable_duet",
        "disable_stitch",
        "brand_content_toggle",
        "brand_organic_toggle",
        "is_aigc",
    ):
        _optional_bool(tiktok, field, "tiktok", label)

    for platform in PLATFORMS:
        if platform not in warning_scope:
            continue
        rendered = render_platform_text(data, platform)
        maximum = PLATFORM_TEXT_LIMITS[platform]
        measured = rendered.get("description") or rendered.get("caption") or ""
        measured_length = platform_text_length(str(measured), platform)
        if measured_length > maximum:
            unit = "bytes UTF-8" if platform == "youtube" else "unidades UTF-16"
            _editorial_warning(
                f"Texto final de {platform} excede {maximum} {unit} em {label} "
                "depois de adicionar hashtags. A publicacao nessa plataforma "
                "sera validada separadamente."
            )


def render_platform_text(data: Mapping[str, Any], platform: str) -> dict[str, Any]:
    if platform not in PLATFORMS:
        raise RuntimeError(f"Plataforma desconhecida: {platform!r}.")
    source = data.get(platform)
    if not isinstance(source, Mapping):
        raise RuntimeError(f"post.json precisa conter o objeto {platform!r}.")
    rendered = deepcopy(dict(source))
    hashtags = [f"#{tag}" for tag in source.get("hashtags", [])]
    suffix = " ".join(hashtags)
    field = "description" if platform == "youtube" else "caption"
    base = str(source.get(field, "")).strip()
    rendered[field] = f"{base}\n\n{suffix}".strip() if suffix else base
    return rendered


def create_post_template(episode_dir: Path, overwrite: bool = False) -> Path:
    post_path = episode_dir / "post.json"
    if post_path.exists() and not overwrite:
        return post_path
    story = _load_json(episode_dir / "story.json")
    timeline = _load_json(episode_dir / "timeline.json")
    raw_shots = timeline.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raise RuntimeError("timeline.json precisa ter ao menos um plano para criar post.json.")
    first_shot = raw_shots[0]
    if not isinstance(first_shot, dict) or not str(first_shot.get("asset", "")).strip():
        raise RuntimeError("O primeiro plano da timeline nao define um asset para a capa.")
    data = build_post_defaults(story, str(first_shot["asset"]).strip())
    _write_json_atomic(post_path, data)
    return post_path


def build_post_defaults(story: Mapping[str, Any], asset_id: str) -> dict[str, Any]:
    title = str(story.get("title", "")).strip() or "Novo video musical"
    segments = story.get("segments")
    hook = ""
    if isinstance(segments, list) and segments and isinstance(segments[0], dict):
        hook = str(segments[0].get("text", "")).strip()
    hook = hook or title
    headline = _short_headline(title)
    youtube_title = _truncate(title, 100)
    topic = title.rstrip(".?! ")
    youtube_description = _truncate(
        f"{hook}\n\nA história por trás de {topic}, contada em formato curto.",
        5000,
    )
    instagram_caption = _truncate(
        f"{hook}\n\nA história por trás de {topic}.",
        2200,
    )
    tiktok_caption = _truncate(hook, 1800)
    return {
        "schema_version": SCHEMA_VERSION,
        "cover": {
            "headline": headline,
            "source": {"type": "asset", "asset_id": asset_id},
        },
        "youtube": {
            "title": youtube_title,
            "description": youtube_description,
            "hashtags": ["Shorts"],
            "privacy_status": "public",
            "category_id": "10",
        },
        "instagram": {
            "caption": instagram_caption,
            "hashtags": ["Reels"],
            "share_to_feed": True,
            "thumb_offset_ms": 1000,
        },
        "facebook": {
            "title": youtube_title,
            "caption": instagram_caption,
            "hashtags": ["Reels"],
        },
        "tiktok": {
            "caption": tiktok_caption,
            "hashtags": [],
            "privacy_level": "SELF_ONLY",
            "video_cover_timestamp_ms": 1000,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "is_aigc": False,
        },
    }


def prepare_episode_post(project_root: Path, episode: str) -> PreparedPost:
    project_root = project_root.resolve()
    if not SLUG_PATTERN.fullmatch(episode):
        raise RuntimeError(
            f"Nome de episodio invalido: {episode!r}. "
            "Use letras minusculas, numeros, '_' ou '-'."
        )
    episodes_dir, output_dir = _project_paths(project_root)
    episode_dir = (project_root / episodes_dir / episode).resolve()
    episodes_root = (project_root / episodes_dir).resolve()
    try:
        episode_dir.relative_to(episodes_root)
    except ValueError as exc:
        raise RuntimeError(f"Caminho de episodio fora da pasta permitida: {episode_dir}") from exc
    if not episode_dir.is_dir():
        raise RuntimeError(f"Episodio {episode!r} nao encontrado em {episode_dir}.")

    story = _load_json(episode_dir / "story.json")
    if story.get("slug") != episode:
        raise RuntimeError(
            f"O slug de story.json ({story.get('slug')!r}) precisa ser igual a {episode!r}."
        )
    post_path = create_post_template(episode_dir)
    current = _load_json(post_path)
    timeline = _load_json(episode_dir / "timeline.json")
    raw_shots = timeline.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots or not isinstance(raw_shots[0], dict):
        raise RuntimeError("timeline.json precisa ter ao menos um plano valido.")
    first_asset = str(raw_shots[0].get("asset", "")).strip()
    if not first_asset:
        raise RuntimeError("O primeiro plano da timeline nao define um asset para a capa.")
    defaults = build_post_defaults(story, first_asset)
    merged = _fill_blanks(current, defaults)
    merged = normalize_post(merged)
    validate_post(merged, post_path)
    _write_json_atomic(post_path, merged)
    post = PostMetadata(path=post_path, data=merged)

    from .cover import generate_cover

    cover_path = (project_root / output_dir / f"{episode}_cover.jpg").resolve()
    cover_path.parent.mkdir(parents=True, exist_ok=True)
    generate_cover(project_root, episode_dir, post.data, cover_path)
    previews = {platform: render_platform_text(post.data, platform) for platform in PLATFORMS}
    return PreparedPost(
        episode=episode,
        post_path=post_path,
        cover_path=cover_path,
        post=post,
        previews=previews,
    )


def _validate_cover(raw: Any, label: str) -> None:
    if not isinstance(raw, Mapping):
        raise RuntimeError(f"post.json precisa conter o objeto 'cover' em {label}.")
    headline = str(raw.get("headline", "")).strip()
    if not headline:
        raise RuntimeError(f"cover.headline nao pode ficar vazio em {label}.")
    if len(headline) > 42 or len(headline.split()) > 6:
        _editorial_warning(
            f"cover.headline tem {len(headline)} caracteres e "
            f"{len(headline.split())} palavras em {label}; a recomendacao editorial "
            "e usar no maximo 42 caracteres e 6 palavras. O gerador tentara "
            "ajustar a tipografia e continuara."
        )
    source = raw.get("source")
    if not isinstance(source, Mapping):
        raise RuntimeError(f"cover.source precisa ser um objeto em {label}.")
    source_type = source.get("type")
    if source_type == "asset":
        asset_id = str(source.get("asset_id", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", asset_id):
            raise RuntimeError(f"cover.source.asset_id invalido em {label}.")
    elif source_type == "video_frame":
        value = source.get("timestamp_seconds")
        try:
            timestamp = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"cover.source.timestamp_seconds precisa ser numerico em {label}."
            ) from exc
        if timestamp < 0:
            raise RuntimeError(
                f"cover.source.timestamp_seconds nao pode ser negativo em {label}."
            )
    else:
        raise RuntimeError(
            f"cover.source.type invalido em {label}: {source_type!r}. "
            "Use asset ou video_frame."
        )
    focus = raw.get("focus")
    if focus is not None:
        if not isinstance(focus, Mapping):
            raise RuntimeError(f"cover.focus precisa ser um objeto em {label}.")
        for axis in ("x", "y"):
            try:
                value = float(focus.get(axis, 0.5))
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"cover.focus.{axis} invalido em {label}.") from exc
            if not 0 <= value <= 1:
                raise RuntimeError(f"cover.focus.{axis} precisa ficar entre 0 e 1 em {label}.")


def _platform_object(data: Mapping[str, Any], platform: str, label: str) -> Mapping[str, Any]:
    value = data.get(platform)
    if not isinstance(value, Mapping):
        raise RuntimeError(f"post.json precisa conter o objeto {platform!r} em {label}.")
    return value


def _required_text(
    data: Mapping[str, Any],
    field: str,
    platform: str,
    label: str,
) -> None:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{platform}.{field} nao pode ficar vazio em {label}.")


def _optional_text(
    data: Mapping[str, Any], field: str, platform: str, label: str
) -> None:
    value = data.get(field, "")
    if not isinstance(value, str):
        raise RuntimeError(f"{platform}.{field} precisa ser texto em {label}.")


def _validate_hashtags(
    data: Mapping[str, Any],
    platform: str,
    label: str,
    *,
    warn_excess: bool,
) -> None:
    hashtags = data.get("hashtags", [])
    if not isinstance(hashtags, list):
        raise RuntimeError(f"{platform}.hashtags precisa ser uma lista em {label}.")
    limit = HASHTAG_RECOMMENDATIONS[platform]
    if warn_excess and len(hashtags) > limit:
        _editorial_warning(
            f"{platform}.hashtags tem {len(hashtags)} itens em {label}; "
            f"a recomendacao editorial e usar no maximo {limit}. "
            "As hashtags serao mantidas."
        )
    seen: set[str] = set()
    for index, tag in enumerate(hashtags, start=1):
        if not isinstance(tag, str) or not tag or tag.startswith("#"):
            raise RuntimeError(
                f"Hashtag {index} de {platform} invalida em {label}; salve sem '#'."
            )
        if not re.fullmatch(r"\w+", tag, flags=re.UNICODE):
            raise RuntimeError(
                f"Hashtag {tag!r} de {platform} invalida em {label}; "
                "use apenas letras, numeros e underscore."
            )
        key = tag.casefold()
        if key in seen:
            raise RuntimeError(f"Hashtag duplicada em {platform}: {tag!r}.")
        seen.add(key)


def _editorial_warning(message: str) -> None:
    print(f"[metadata] aviso: {message}")


def _optional_bool(data: Mapping[str, Any], field: str, platform: str, label: str) -> None:
    if field in data and not isinstance(data[field], bool):
        raise RuntimeError(f"{platform}.{field} precisa ser true ou false em {label}.")


def _optional_nonnegative_int(
    data: Mapping[str, Any], field: str, platform: str, label: str
) -> None:
    if field not in data:
        return
    value = data[field]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError(f"{platform}.{field} precisa ser inteiro nao negativo em {label}.")


def _optional_url(data: Mapping[str, Any], field: str, platform: str, label: str) -> None:
    value = data.get(field)
    if value in (None, ""):
        return
    if not isinstance(value, str) or not re.match(r"^https://[^\s]+$", value):
        raise RuntimeError(f"{platform}.{field} precisa ser uma URL HTTPS em {label}.")


def _project_paths(project_root: Path) -> tuple[str, str]:
    config = _load_json(project_root / "config" / "config.json")
    paths = config.get("paths")
    if not isinstance(paths, Mapping):
        raise RuntimeError("config/config.json precisa conter o objeto 'paths'.")
    episodes_dir = str(paths.get("episodes_dir", "episodes"))
    output_dir = str(paths.get("output_dir", "output"))
    for value, label in ((episodes_dir, "episodes_dir"), (output_dir, "output_dir")):
        candidate = (project_root / value).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError as exc:
            raise RuntimeError(f"config.paths.{label} aponta para fora do projeto.") from exc
    return episodes_dir, output_dir


def _fill_blanks(current: Mapping[str, Any], defaults: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(current))
    for key, default in defaults.items():
        if key not in result or result[key] in (None, ""):
            result[key] = deepcopy(default)
        elif isinstance(default, Mapping) and isinstance(result[key], Mapping):
            result[key] = _fill_blanks(result[key], default)
    return result


def _short_headline(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", title).strip()
    words = cleaned.split()
    selected: list[str] = []
    for word in words:
        candidate = " ".join([*selected, word])
        if len(selected) >= 6 or len(candidate) > 42:
            break
        selected.append(word)
    return (" ".join(selected) or "HISTORIA DA MUSICA").upper()


def _truncate(value: str, maximum: int) -> str:
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value).strip()
    if len(value) <= maximum:
        return value
    shortened = value[: maximum - 1].rsplit(" ", 1)[0].rstrip(".,;:-")
    return (shortened or value[: maximum - 1]).rstrip() + "…"


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def platform_text_length(value: str, platform: str) -> int:
    if platform == "youtube":
        return len(value.encode("utf-8"))
    if platform in {"instagram", "facebook", "tiktok"}:
        return utf16_length(value)
    raise RuntimeError(f"Plataforma desconhecida: {platform!r}.")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Arquivo obrigatorio ausente: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"JSON invalido em {path} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Nao foi possivel ler {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"O arquivo {path} precisa conter um objeto JSON.")
    return data


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
