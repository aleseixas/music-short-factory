from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import requests

from publishing.base import ApiError, PublishContext, Publisher, PublishingError
from publishing.credentials import CredentialStore
from publishing.instagram import InstagramPublisher
from publishing.metadata import PLATFORMS, load_post
from publishing.tiktok import TikTokPublisher
from publishing.youtube import YouTubePublisher


PUBLISHER_TYPES: dict[str, type[Publisher]] = {
    "youtube": YouTubePublisher,
    "instagram": InstagramPublisher,
    "tiktok": TikTokPublisher,
}


def create_publisher(
    platform: str,
    credentials: CredentialStore,
    session: requests.Session | None = None,
) -> Publisher:
    try:
        publisher_type = PUBLISHER_TYPES[platform]
    except KeyError as exc:
        raise PublishingError(f"Plataforma desconhecida: {platform!r}.") from exc
    return publisher_type(credentials=credentials, session=session)


def build_context(project_root: Path, episode: str, platform: str) -> PublishContext:
    if not re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", episode):
        raise PublishingError(
            f"Nome de episodio invalido: {episode!r}. "
            "Use letras minusculas, numeros, '_' ou '-'."
        )
    config = _load_json(project_root / "config" / "config.json")
    paths = config.get("paths")
    if not isinstance(paths, Mapping):
        raise PublishingError("config/config.json precisa conter o objeto 'paths'.")
    episodes_dir = str(paths.get("episodes_dir", "episodes"))
    output_dir = str(paths.get("output_dir", "output"))
    episode_dir = (project_root / episodes_dir / episode).resolve()
    episodes_root = (project_root / episodes_dir).resolve()
    try:
        episode_dir.relative_to(episodes_root)
    except ValueError as exc:
        raise PublishingError(f"Caminho de episodio fora da pasta permitida: {episode_dir}") from exc
    if not episode_dir.is_dir():
        raise PublishingError(f"Episodio {episode!r} nao encontrado em {episode_dir}.")
    post = load_post(
        episode_dir / "post.json",
        warning_platforms=(platform,),
    )
    output_root = (project_root / output_dir).resolve()
    video_path = (output_root / f"{episode}.mp4").resolve()
    cover_path = (output_root / f"{episode}_cover.jpg").resolve()
    for path in (video_path, cover_path):
        try:
            path.relative_to(output_root)
        except ValueError as exc:
            raise PublishingError(f"Arquivo de publicacao fora de output/: {path}") from exc
    return PublishContext(
        episode=episode,
        video_path=video_path,
        cover_path=cover_path,
        metadata=post.for_platform(platform),
    )


def main(
    argv: Sequence[str] | None = None,
    default_project_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Valida ou publica um episodio pelas APIs oficiais. Dry-run e o padrao."
    )
    parser.add_argument("episode", help="Slug do episodio (ex.: duckworth).")
    parser.add_argument(
        "--platform",
        required=True,
        choices=[*PLATFORMS, "all"],
        help="Plataforma de destino.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Somente validar; nenhuma chamada externa (comportamento padrao).",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Autoriza explicitamente upload/publicacao real pelas APIs oficiais.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=default_project_root or Path.cwd(),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    dry_run = not args.live
    platforms = list(PLATFORMS) if args.platform == "all" else [args.platform]
    try:
        credentials = CredentialStore.load(project_root)
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    if dry_run:
        print("MODO SEGURO: DRY-RUN — nenhuma chamada de API sera feita.")
    else:
        print("MODO LIVE: upload/publicacao real autorizados por --live.")

    failed = False
    for platform in platforms:
        publisher = create_publisher(platform, credentials)
        try:
            context = build_context(project_root, args.episode, platform)
            if dry_run:
                result = publisher.dry_run(context)
                credential_warning = _credential_warning(publisher, context)
                if credential_warning:
                    print(credential_warning)
            else:
                publisher.validate(context, require_credentials=True)
                uploaded = publisher.upload(context)
                result = publisher.publish(context, uploaded)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
        except (PublishingError, RuntimeError, ApiError) as exc:
            failed = True
            print(f"ERRO: {exc}", file=sys.stderr)
    return 1 if failed else 0


def _credential_warning(publisher: Publisher, context: PublishContext) -> str:
    try:
        publisher.validate(context, require_credentials=True)
    except PublishingError as exc:
        if "credenciais nao configuradas" in str(exc):
            return str(exc)
        raise
    return ""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise PublishingError(f"Arquivo obrigatorio ausente: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PublishingError(
            f"JSON invalido em {path} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise PublishingError(f"O arquivo {path} precisa conter um objeto JSON.")
    return data


if __name__ == "__main__":
    raise SystemExit(main(default_project_root=Path(__file__).resolve().parent))
