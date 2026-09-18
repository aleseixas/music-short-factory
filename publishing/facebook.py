from __future__ import annotations

import time
from typing import Any, Mapping
from urllib.parse import urlsplit

from .base import ApiError, PublishContext, PublishResult, Publisher, PublishingError
from .metadata import (
    FACEBOOK_TITLE_LIMIT,
    PLATFORM_TEXT_LIMITS,
    platform_text_length,
    render_platform_text,
)


class FacebookPublisher(Publisher):
    """Publish a local MP4 as a Reel on a Facebook Page."""

    platform = "facebook"
    display_name = "FacebookPublisher"

    def __init__(
        self,
        *args: Any,
        poll_interval_seconds: float = 5.0,
        poll_timeout_seconds: float = 300.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds

    def validate(self, context: PublishContext, require_credentials: bool = True) -> None:
        self._validate_common(context)
        rendered = render_platform_text({"facebook": context.metadata}, "facebook")
        caption = str(rendered.get("caption", "")).strip()
        if not caption:
            raise PublishingError("FacebookPublisher: caption nao pode ficar vazia.")
        text_limit = PLATFORM_TEXT_LIMITS["facebook"]
        if platform_text_length(caption, "facebook") > text_limit:
            raise PublishingError(
                f"FacebookPublisher: caption final excede {text_limit} unidades UTF-16."
            )
        title = str(rendered.get("title", "")).strip()
        if len(title) > FACEBOOK_TITLE_LIMIT:
            raise PublishingError(
                f"FacebookPublisher: title excede {FACEBOOK_TITLE_LIMIT} caracteres."
            )
        if require_credentials:
            missing = [
                key
                for key in (
                    "FACEBOOK_PAGE_ACCESS_TOKEN",
                    "FACEBOOK_PAGE_ID",
                    "META_GRAPH_API_VERSION",
                )
                if not self.credentials.has(key)
            ]
            if missing:
                raise PublishingError(
                    "FacebookPublisher: credenciais nao configuradas "
                    f"(faltando: {', '.join(missing)})."
                )

    def upload(self, context: PublishContext) -> Mapping[str, Any]:
        self.validate(context, require_credentials=True)
        token = self.credentials.get("FACEBOOK_PAGE_ACCESS_TOKEN")
        page_id = self.credentials.get("FACEBOOK_PAGE_ID")

        response = self._request(
            "POST",
            self._api_url(f"/{page_id}/video_reels"),
            data={"upload_phase": "start", "access_token": token},
        )
        payload = self._json(response, self.display_name)
        video_id = str(payload.get("video_id", "")).strip()
        if not video_id:
            raise ApiError("FacebookPublisher: inicio do upload nao retornou video_id.")

        upload_url = str(payload.get("upload_url", "")).strip()
        if not upload_url:
            version = self.credentials.get("META_GRAPH_API_VERSION")
            upload_url = (
                f"https://rupload.facebook.com/video-upload/{version}/{video_id}"
            )
        _validate_upload_url(upload_url)

        try:
            file_size = context.video_path.stat().st_size
            with context.video_path.open("rb") as media:
                upload_response = self._request(
                    "POST",
                    upload_url,
                    headers={
                        "Authorization": f"OAuth {token}",
                        "offset": "0",
                        "file_size": str(file_size),
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(file_size),
                    },
                    data=media,
                )
        except OSError as exc:
            raise PublishingError(
                "FacebookPublisher: nao foi possivel ler o MP4 para upload."
            ) from exc

        upload_payload = self._json(upload_response, self.display_name)
        if upload_payload.get("success") is not True:
            raise ApiError("FacebookPublisher: API nao confirmou o upload do video.")
        return {"video_id": video_id, "upload_status": upload_payload}

    def publish(
        self,
        context: PublishContext,
        upload_result: Mapping[str, Any],
    ) -> PublishResult:
        video_id = str(upload_result.get("video_id", "")).strip()
        if not video_id:
            raise PublishingError("FacebookPublisher: upload_result nao contem video_id.")

        rendered = render_platform_text({"facebook": context.metadata}, "facebook")
        fields: dict[str, Any] = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": rendered["caption"],
            "access_token": self.credentials.get("FACEBOOK_PAGE_ACCESS_TOKEN"),
        }
        title = str(rendered.get("title", "")).strip()
        if title:
            fields["title"] = title

        page_id = self.credentials.get("FACEBOOK_PAGE_ID")
        response = self._request(
            "POST",
            self._api_url(f"/{page_id}/video_reels"),
            data=fields,
        )
        payload = self._json(response, self.display_name)
        if payload.get("success") is not True:
            raise ApiError("FacebookPublisher: API nao confirmou o inicio da publicacao.")

        status = self._wait_until_published(video_id)
        print(f"[facebook] published: {video_id}")
        return PublishResult(
            platform=self.platform,
            status="published",
            external_id=video_id,
            url=f"https://www.facebook.com/reel/{video_id}",
            details={"status": status},
        )

    def get_status(self, external_id: str) -> Mapping[str, Any]:
        video_id = external_id.strip()
        if not video_id:
            raise PublishingError("FacebookPublisher: video_id ausente.")
        token = self.credentials.get("FACEBOOK_PAGE_ACCESS_TOKEN")
        if not token:
            raise PublishingError("FacebookPublisher: credenciais nao configuradas.")
        response = self._request(
            "GET",
            self._api_url(f"/{video_id}"),
            params={"fields": "status", "access_token": token},
        )
        return self._json(response, self.display_name)

    def _wait_until_published(self, video_id: str) -> Mapping[str, Any]:
        deadline = time.monotonic() + self.poll_timeout_seconds
        last: Mapping[str, Any] = {}
        while time.monotonic() <= deadline:
            last = self.get_status(video_id)
            status = last.get("status")
            if not isinstance(status, Mapping):
                raise ApiError("FacebookPublisher: consulta nao retornou status do video.")

            video_status = str(status.get("video_status", "")).strip().lower()
            processing_status = _phase_status(status, "processing_phase")
            publishing_status = _phase_status(status, "publishing_phase")
            if publishing_status == "complete":
                return last
            if video_status in {"error", "failed"} or processing_status in {
                "error",
                "failed",
            } or publishing_status in {"error", "failed"}:
                raise ApiError(
                    f"FacebookPublisher: video {video_id} terminou com erro de processamento/publicacao."
                )
            if self.poll_interval_seconds > 0:
                time.sleep(min(self.poll_interval_seconds, 60.0))

        raise ApiError(
            f"FacebookPublisher: timeout aguardando a publicacao do video {video_id}; "
            f"ultimo status: {_status_summary(last)}."
        )

    def _api_url(self, path: str) -> str:
        version = self.credentials.get("META_GRAPH_API_VERSION")
        return f"https://graph.facebook.com/{version}{path}"


def _validate_upload_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "rupload.facebook.com"
        or not parsed.path.startswith("/video-upload/")
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ApiError("FacebookPublisher: API retornou upload_url invalida.")


def _phase_status(status: Mapping[str, Any], phase: str) -> str:
    value = status.get(phase)
    if not isinstance(value, Mapping):
        return ""
    return str(value.get("status", "")).strip().lower()


def _status_summary(payload: Mapping[str, Any]) -> str:
    status = payload.get("status")
    if not isinstance(status, Mapping):
        return "desconhecido"
    values = (
        str(status.get("video_status", "")).strip(),
        _phase_status(status, "processing_phase"),
        _phase_status(status, "publishing_phase"),
    )
    return "/".join(value for value in values if value) or "desconhecido"
