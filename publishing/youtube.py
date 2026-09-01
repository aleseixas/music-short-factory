from __future__ import annotations

from typing import Any, Mapping

from .base import ApiError, PublishContext, PublishResult, Publisher, PublishingError
from .metadata import (
    PLATFORM_TEXT_LIMITS,
    YOUTUBE_TAGS_LIMIT,
    YOUTUBE_TITLE_LIMIT,
    platform_text_length,
    render_platform_text,
)


class YouTubePublisher(Publisher):
    platform = "youtube"
    display_name = "YouTubePublisher"

    TOKEN_URL = "https://oauth2.googleapis.com/token"
    UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
    THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
    API_URL = "https://www.googleapis.com/youtube/v3"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._current_token: str | None = None

    def validate(self, context: PublishContext, require_credentials: bool = True) -> None:
        self._validate_common(context)
        title = str(context.metadata.get("title", "")).strip()
        if not title or len(title) > YOUTUBE_TITLE_LIMIT:
            raise PublishingError(
                f"YouTubePublisher: titulo ausente ou maior que {YOUTUBE_TITLE_LIMIT} "
                "caracteres."
            )
        rendered = render_platform_text({"youtube": context.metadata}, "youtube")
        text_limit = PLATFORM_TEXT_LIMITS["youtube"]
        if platform_text_length(str(rendered.get("description", "")), "youtube") > text_limit:
            raise PublishingError(
                f"YouTubePublisher: description final excede {text_limit} bytes UTF-8."
            )
        tags = rendered.get("hashtags", [])
        if not isinstance(tags, list):
            raise PublishingError("YouTubePublisher: hashtags precisa ser uma lista.")
        if _youtube_tags_length(tags) > YOUTUBE_TAGS_LIMIT:
            raise PublishingError(
                f"YouTubePublisher: tags excedem o limite oficial agregado de "
                f"{YOUTUBE_TAGS_LIMIT} caracteres."
            )
        privacy = context.metadata.get("privacy_status", "private")
        if privacy not in {"private", "unlisted", "public"}:
            raise PublishingError(
                "YouTubePublisher: privacy_status deve ser private, unlisted ou public."
            )
        if context.cover_path.stat().st_size > 2 * 1024 * 1024:
            raise PublishingError("YouTubePublisher: a thumbnail excede o limite oficial de 2 MB.")
        if require_credentials:
            missing = self._missing_credentials()
            if missing:
                raise PublishingError(
                    "YouTubePublisher: credenciais nao configuradas "
                    f"(faltando: {', '.join(missing)})."
                )

    def upload(self, context: PublishContext) -> Mapping[str, Any]:
        self.validate(context, require_credentials=True)
        token = self._access_token()
        rendered = render_platform_text({"youtube": context.metadata}, "youtube")
        body = {
            "snippet": {
                "title": rendered["title"],
                "description": rendered.get("description", ""),
                "tags": list(rendered.get("hashtags", [])),
                "categoryId": str(rendered.get("category_id", "10")),
            },
            "status": {
                "privacyStatus": rendered.get("privacy_status", "private"),
                "selfDeclaredMadeForKids": bool(
                    rendered.get("self_declared_made_for_kids", False)
                ),
            },
        }
        file_size = context.video_path.stat().st_size
        mime_type = "video/mp4"
        response = self._request(
            "POST",
            self.UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(file_size),
                "X-Upload-Content-Type": mime_type,
            },
            json=body,
        )
        location = response.headers.get("Location")
        if not location:
            raise ApiError(
                "YouTubePublisher: a API nao retornou a URL da sessao resumivel."
            )
        with context.video_path.open("rb") as media:
            uploaded = self._request(
                "PUT",
                location,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": mime_type,
                    "Content-Length": str(file_size),
                },
                data=media,
            )
        payload = self._json(uploaded, self.display_name)
        video_id = str(payload.get("id", "")).strip()
        if not video_id:
            raise ApiError("YouTubePublisher: upload concluido sem video_id na resposta.")
        return {"video_id": video_id, "resource": payload}

    def publish(
        self,
        context: PublishContext,
        upload_result: Mapping[str, Any],
    ) -> PublishResult:
        video_id = str(upload_result.get("video_id", "")).strip()
        if not video_id:
            raise PublishingError("YouTubePublisher: upload_result nao contem video_id.")
        token = self._access_token()
        thumbnail_set = True
        warning: str | None = None
        try:
            with context.cover_path.open("rb") as thumbnail:
                self._request(
                    "POST",
                    self.THUMBNAIL_URL,
                    params={"videoId": video_id, "uploadType": "media"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "image/jpeg",
                        "Content-Length": str(context.cover_path.stat().st_size),
                    },
                    data=thumbnail,
                )
        except ApiError as exc:
            thumbnail_set = False
            warning = str(exc)
        return PublishResult(
            platform=self.platform,
            status="uploaded" if thumbnail_set else "uploaded_without_thumbnail",
            external_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            details={
                "thumbnail_set": thumbnail_set,
                **({"warning": warning} if warning else {}),
            },
        )

    def get_status(self, external_id: str) -> Mapping[str, Any]:
        if not external_id.strip():
            raise PublishingError("YouTubePublisher: video_id ausente.")
        token = self._access_token()
        response = self._request(
            "GET",
            f"{self.API_URL}/videos",
            params={"part": "status,processingDetails", "id": external_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        payload = self._json(response, self.display_name)
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ApiError(f"YouTubePublisher: video {external_id!r} nao encontrado.")
        item = items[0]
        return dict(item) if isinstance(item, Mapping) else {"value": item}

    def _missing_credentials(self) -> list[str]:
        if self.credentials.has("YOUTUBE_ACCESS_TOKEN"):
            return []
        missing = [
            key
            for key in (
                "YOUTUBE_CLIENT_ID",
                "YOUTUBE_CLIENT_SECRET",
                "YOUTUBE_REFRESH_TOKEN",
            )
            if not self.credentials.has(key)
        ]
        return missing

    def _access_token(self) -> str:
        if self._current_token:
            return self._current_token
        direct = self.credentials.get("YOUTUBE_ACCESS_TOKEN")
        if direct:
            self._current_token = direct
            return direct
        missing = self._missing_credentials()
        if missing:
            raise PublishingError(
                "YouTubePublisher: credenciais nao configuradas "
                f"(faltando: {', '.join(missing)})."
            )
        response = self._request(
            "POST",
            self.TOKEN_URL,
            data={
                "client_id": self.credentials.get("YOUTUBE_CLIENT_ID"),
                "client_secret": self.credentials.get("YOUTUBE_CLIENT_SECRET"),
                "refresh_token": self.credentials.get("YOUTUBE_REFRESH_TOKEN"),
                "grant_type": "refresh_token",
            },
        )
        payload = self._json(response, self.display_name)
        token = str(payload.get("access_token", "")).strip()
        if not token:
            raise ApiError("YouTubePublisher: OAuth nao retornou access_token.")
        self._current_token = token
        return token


def _youtube_tags_length(tags: list[object]) -> int:
    values = [str(tag) for tag in tags]
    separators = max(0, len(values) - 1)
    quoted_spaces = sum(2 for value in values if " " in value)
    return sum(len(value) for value in values) + separators + quoted_spaces
