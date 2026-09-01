from __future__ import annotations

from typing import Any, Mapping

from .base import ApiError, PublishContext, PublishResult, Publisher, PublishingError
from .metadata import PLATFORM_TEXT_LIMITS, platform_text_length, render_platform_text


class TikTokPublisher(Publisher):
    platform = "tiktok"
    display_name = "TikTokPublisher"

    TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
    CREATOR_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
    INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
    MAX_CHUNK_BYTES = 64_000_000
    CHUNK_BYTES = 10_000_000
    MAX_VIDEO_BYTES = 4_000_000_000

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._current_token: str | None = None

    def validate(self, context: PublishContext, require_credentials: bool = True) -> None:
        self._validate_common(context)
        caption = str(context.metadata.get("caption", "")).strip()
        if not caption:
            raise PublishingError("TikTokPublisher: caption nao pode ficar vazia.")
        rendered = render_platform_text({"tiktok": context.metadata}, "tiktok")
        text_limit = PLATFORM_TEXT_LIMITS["tiktok"]
        if platform_text_length(str(rendered.get("caption", "")), "tiktok") > text_limit:
            raise PublishingError(
                f"TikTokPublisher: caption final excede {text_limit} unidades UTF-16."
            )
        privacy = context.metadata.get("privacy_level", "SELF_ONLY")
        if not isinstance(privacy, str) or not privacy:
            raise PublishingError("TikTokPublisher: privacy_level invalido.")
        if require_credentials:
            missing = self._missing_credentials()
            if missing:
                raise PublishingError(
                    "TikTokPublisher: credenciais nao configuradas "
                    f"(faltando: {', '.join(missing)})."
                )

    def upload(self, context: PublishContext) -> Mapping[str, Any]:
        self.validate(context, require_credentials=True)
        token = self._access_token()
        creator = self._creator_info(token)
        privacy = str(context.metadata.get("privacy_level", "SELF_ONLY"))
        options = creator.get("privacy_level_options")
        if isinstance(options, list) and privacy not in options:
            raise PublishingError(
                f"TikTokPublisher: privacy_level {privacy!r} nao esta liberado para esta conta. "
                f"Opcoes da API: {', '.join(map(str, options))}."
            )
        file_size = context.video_path.stat().st_size
        if file_size > self.MAX_VIDEO_BYTES:
            raise PublishingError("TikTokPublisher: o video excede o limite oficial de 4 GB.")
        chunk_size, chunk_sizes = self._chunk_layout(file_size)
        total_chunks = len(chunk_sizes)
        rendered = render_platform_text({"tiktok": context.metadata}, "tiktok")
        post_info = {
            "title": rendered["caption"],
            "privacy_level": privacy,
            "disable_duet": bool(rendered.get("disable_duet", False)),
            "disable_comment": bool(rendered.get("disable_comment", False)),
            "disable_stitch": bool(rendered.get("disable_stitch", False)),
            "video_cover_timestamp_ms": int(
                rendered.get("video_cover_timestamp_ms", 1000)
            ),
            "brand_content_toggle": bool(rendered.get("brand_content_toggle", False)),
            "brand_organic_toggle": bool(rendered.get("brand_organic_toggle", False)),
            "is_aigc": bool(rendered.get("is_aigc", False)),
        }
        response = self._request(
            "POST",
            self.INIT_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": post_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": total_chunks,
                },
            },
        )
        payload = self._tiktok_json(response)
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ApiError("TikTokPublisher: resposta de init sem objeto data.")
        publish_id = str(data.get("publish_id", "")).strip()
        upload_url = str(data.get("upload_url", "")).strip()
        if not publish_id or not upload_url:
            raise ApiError("TikTokPublisher: init nao retornou publish_id e upload_url.")

        with context.video_path.open("rb") as media:
            start = 0
            for expected_size in chunk_sizes:
                chunk = media.read(expected_size)
                if len(chunk) != expected_size:
                    raise PublishingError(
                        "TikTokPublisher: o arquivo mudou ou ficou incompleto durante o upload."
                    )
                end = start + len(chunk) - 1
                self._request(
                    "PUT",
                    upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                    },
                    data=chunk,
                )
                start = end + 1
        if start != file_size:
            raise PublishingError(
                f"TikTokPublisher: upload incompleto ({start} de {file_size} bytes)."
            )
        return {"publish_id": publish_id}

    def publish(
        self,
        context: PublishContext,
        upload_result: Mapping[str, Any],
    ) -> PublishResult:
        publish_id = str(upload_result.get("publish_id", "")).strip()
        if not publish_id:
            raise PublishingError("TikTokPublisher: upload_result nao contem publish_id.")
        # Direct Post has no separate finalize endpoint: video/init already started
        # publication and completion of the byte transfer starts processing.
        try:
            status_payload = self.get_status(publish_id)
        except ApiError as exc:
            return PublishResult(
                platform=self.platform,
                status="status_unknown",
                external_id=publish_id,
                details={
                    "publish_id": publish_id,
                    "warning": str(exc),
                    "publication_started": True,
                },
            )
        status = str(status_payload.get("status", "processing")).lower()
        public_ids = status_payload.get("publicaly_available_post_id")
        external_id = publish_id
        if isinstance(public_ids, list) and public_ids:
            external_id = str(public_ids[0])
        return PublishResult(
            platform=self.platform,
            status=status,
            external_id=external_id,
            details={"publish_id": publish_id, "platform_status": status_payload},
        )

    def get_status(self, external_id: str) -> Mapping[str, Any]:
        if not external_id.strip():
            raise PublishingError("TikTokPublisher: publish_id ausente.")
        token = self._access_token()
        response = self._request(
            "POST",
            self.STATUS_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": external_id},
        )
        payload = self._tiktok_json(response)
        data = payload.get("data")
        return dict(data) if isinstance(data, Mapping) else {}

    def _creator_info(self, token: str) -> Mapping[str, Any]:
        response = self._request(
            "POST",
            self.CREATOR_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={},
        )
        payload = self._tiktok_json(response)
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ApiError("TikTokPublisher: creator_info nao retornou dados da conta.")
        return data

    def _missing_credentials(self) -> list[str]:
        if self.credentials.has("TIKTOK_ACCESS_TOKEN"):
            return []
        missing = [
            key
            for key in (
                "TIKTOK_CLIENT_KEY",
                "TIKTOK_CLIENT_SECRET",
                "TIKTOK_REFRESH_TOKEN",
            )
            if not self.credentials.has(key)
        ]
        return missing

    @classmethod
    def _chunk_layout(cls, file_size: int) -> tuple[int, list[int]]:
        if file_size <= 0:
            raise PublishingError("TikTokPublisher: tamanho de video invalido.")
        if file_size <= cls.MAX_CHUNK_BYTES:
            return file_size, [file_size]
        chunk_size = cls.CHUNK_BYTES
        total_chunks = file_size // chunk_size
        if total_chunks < 1 or total_chunks > 1000:
            raise PublishingError(
                f"TikTokPublisher: quantidade de chunks fora do limite: {total_chunks}."
            )
        sizes = [chunk_size] * total_chunks
        remainder = file_size - chunk_size * total_chunks
        if remainder:
            sizes[-1] += remainder
        return chunk_size, sizes

    def _access_token(self) -> str:
        if self._current_token:
            return self._current_token

        refresh_token = self.credentials.get("TIKTOK_REFRESH_TOKEN")
        client_key = self.credentials.get("TIKTOK_CLIENT_KEY")
        client_secret = self.credentials.get("TIKTOK_CLIENT_SECRET")
        if refresh_token and client_key and client_secret:
            response = self._request(
                "POST",
                self.TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_key": client_key,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            payload = self._json(response, self.display_name)
            token = str(payload.get("access_token", "")).strip()
            if not token:
                raise ApiError("TikTokPublisher: OAuth nao retornou access_token.")

            # TikTok may rotate refresh_token during refresh. Persist the new value
            # when this CredentialStore came from a project .env. In ephemeral CI
            # the new token still remains valid for this process, but durable secret
            # rotation must be handled by the CI secret store before production.
            new_refresh = str(payload.get("refresh_token", "")).strip()
            if new_refresh and new_refresh != refresh_token:
                try:
                    self.credentials.persist_env_values(
                        {"TIKTOK_REFRESH_TOKEN": new_refresh}
                    )
                except RuntimeError:
                    pass
            self._current_token = token
            return token

        direct = self.credentials.get("TIKTOK_ACCESS_TOKEN")
        if direct:
            self._current_token = direct
            return direct

        missing = self._missing_credentials()
        raise PublishingError(
            "TikTokPublisher: credenciais nao configuradas "
            f"(faltando: {', '.join(missing)})."
        )

    def _tiktok_json(self, response: Any) -> dict[str, Any]:
        payload = self._json(response, self.display_name)
        error = payload.get("error")
        if isinstance(error, Mapping):
            code = str(error.get("code", "")).strip()
            if code and code.lower() != "ok":
                message = str(error.get("message", "erro da API"))[:240]
                raise ApiError(f"TikTokPublisher: {code}: {message}")
        return payload
