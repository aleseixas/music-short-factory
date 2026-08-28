from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from cloudinary import api as cloudinary_api
from cloudinary import uploader as cloudinary_uploader
import requests

from .base import ApiError, PublishContext, PublishResult, Publisher, PublishingError
from .metadata import render_platform_text


class VideoHost(Protocol):
    """Temporary public hosting used while Instagram ingests a local video."""

    def upload(self, local_file: Path) -> str:
        """Upload local_file and return its public HTTPS URL."""
        ...

    def cleanup(self) -> None:
        """Remove the temporary hosted object. Implementations must be idempotent."""
        ...


class _CloudinaryUploader(Protocol):
    def upload(self, file: str, **options: Any) -> Mapping[str, Any]: ...

    def upload_large(self, file: str, **options: Any) -> Mapping[str, Any]: ...

    def destroy(self, public_id: str, **options: Any) -> Mapping[str, Any]: ...


class _CloudinaryAdmin(Protocol):
    def resources(self, **options: Any) -> Mapping[str, Any]: ...


class PresignedUrlVideoHost:
    """Storage-neutral host backed by explicit HTTPS PUT and DELETE URLs."""

    def __init__(
        self,
        upload_url: str,
        public_url: str,
        delete_url: str,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 90.0,
    ) -> None:
        self.upload_url = _require_https_url(upload_url, "upload do VideoHost")
        self.public_url = _require_https_url(
            public_url,
            "publica do VideoHost",
            require_public_host=True,
        )
        self.delete_url = _require_https_url(delete_url, "cleanup do VideoHost")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self._upload_attempted = False
        self._cleaned = False

    def upload(self, local_file: Path) -> str:
        self._upload_attempted = True
        try:
            file_size = local_file.stat().st_size
            with local_file.open("rb") as media:
                response = self.session.request(
                    "PUT",
                    self.upload_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(file_size),
                    },
                    data=media,
                    timeout=self.timeout_seconds,
                )
        except OSError as exc:
            raise PublishingError(
                "InstagramPublisher: nao foi possivel ler o MP4 para o VideoHost."
            ) from exc
        except requests.RequestException as exc:
            raise PublishingError(
                "InstagramPublisher: falha de conexao com o VideoHost "
                f"({exc.__class__.__name__})."
            ) from exc
        if not 200 <= response.status_code < 300:
            raise PublishingError(
                f"InstagramPublisher: VideoHost retornou HTTP {response.status_code} no upload."
            )
        return self.public_url

    def cleanup(self) -> None:
        if not self._upload_attempted or self._cleaned:
            return
        try:
            response = self.session.request(
                "DELETE",
                self.delete_url,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise PublishingError(
                "InstagramPublisher: falha de conexao ao limpar o VideoHost "
                f"({exc.__class__.__name__})."
            ) from exc
        if response.status_code != 404 and not 200 <= response.status_code < 300:
            raise PublishingError(
                f"InstagramPublisher: VideoHost retornou HTTP {response.status_code} no cleanup."
            )
        self._cleaned = True


class CloudinaryVideoHost:
    """Temporary Cloudinary video used as Instagram's public ingestion URL."""

    _SIMPLE_UPLOAD_LIMIT_BYTES = 100_000_000
    _TEMP_PUBLIC_ID_PREFIX = "instagram/"
    _STALE_AFTER = timedelta(hours=12)

    def __init__(
        self,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        *,
        episode: str,
        uploader: _CloudinaryUploader | None = None,
        admin_client: _CloudinaryAdmin | None = None,
        id_factory: Callable[[], str] | None = None,
        now_factory: Callable[[], datetime] | None = None,
        timeout_seconds: float = 90.0,
    ) -> None:
        values = {
            "CLOUDINARY_CLOUD_NAME": cloud_name.strip(),
            "CLOUDINARY_API_KEY": api_key.strip(),
            "CLOUDINARY_API_SECRET": api_secret.strip(),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise PublishingError(
                "InstagramPublisher: Cloudinary nao configurado "
                f"(faltando: {', '.join(missing)})."
            )
        self._cloud_name = values["CLOUDINARY_CLOUD_NAME"]
        self._api_key = values["CLOUDINARY_API_KEY"]
        self._api_secret = values["CLOUDINARY_API_SECRET"]
        self._episode = _safe_public_id_part(episode, "episode")
        self._uploader = uploader if uploader is not None else cloudinary_uploader
        if admin_client is not None:
            self._admin_client = admin_client
        elif uploader is not None and callable(getattr(uploader, "resources", None)):
            self._admin_client = uploader
        else:
            self._admin_client = cloudinary_api
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._timeout_seconds = timeout_seconds
        self._public_id: str | None = None
        self._cleaned = False

    @property
    def public_id(self) -> str | None:
        return self._public_id

    def __repr__(self) -> str:
        return (
            f"CloudinaryVideoHost(episode={self._episode!r}, "
            "credentials=[REDACTED])"
        )

    def upload(self, local_file: Path) -> str:
        try:
            file_size = local_file.stat().st_size
        except OSError as exc:
            raise PublishingError(
                "InstagramPublisher: nao foi possivel ler o MP4 para o Cloudinary."
            ) from exc

        self._cleanup_stale_assets_best_effort()
        unique_part = _safe_public_id_part(str(self._id_factory()), "")
        if not unique_part:
            unique_part = uuid4().hex
        candidate_public_id = f"instagram/{self._episode}/{unique_part}"
        options: dict[str, Any] = {
            "resource_type": "video",
            "public_id": candidate_public_id,
            "overwrite": False,
            "cloud_name": self._cloud_name,
            "api_key": self._api_key,
            "api_secret": self._api_secret,
            "timeout": self._timeout_seconds,
        }
        upload = (
            self._uploader.upload_large
            if file_size > self._SIMPLE_UPLOAD_LIMIT_BYTES
            else self._uploader.upload
        )
        try:
            result = upload(str(local_file), **options)
        except Exception as exc:
            raise PublishingError(
                "InstagramPublisher: upload do video no Cloudinary falhou "
                f"({exc.__class__.__name__})."
            ) from None
        if not isinstance(result, Mapping):
            raise PublishingError(
                "InstagramPublisher: Cloudinary retornou uma resposta inesperada no upload."
            )
        returned_public_id = str(result.get("public_id", "")).strip() or candidate_public_id
        if not returned_public_id.startswith(self._TEMP_PUBLIC_ID_PREFIX):
            raise PublishingError(
                "InstagramPublisher: Cloudinary retornou public_id fora do namespace temporario."
            )
        public_url = _require_https_url(
            str(result.get("secure_url", "")).strip(),
            "secure_url do Cloudinary",
            require_public_host=True,
        )
        self._public_id = returned_public_id
        print(f"[cloudinary] uploaded: {self._public_id}")
        return public_url

    def cleanup(self) -> None:
        if not self._public_id or self._cleaned:
            return
        try:
            outcome = self._destroy_asset(self._public_id)
        except Exception as exc:
            raise PublishingError(
                "InstagramPublisher: cleanup do video no Cloudinary falhou "
                f"({exc.__class__.__name__})."
            ) from None
        if outcome not in {"ok", "not found"}:
            raise PublishingError(
                "InstagramPublisher: Cloudinary nao confirmou o cleanup do video."
            )
        self._cleaned = True
        if outcome == "ok":
            print(f"[cloudinary] deleted: {self._public_id}")

    def _cleanup_stale_assets_best_effort(self) -> None:
        try:
            stale_public_ids = self._stale_public_ids()
        except Exception as exc:
            print(
                "[cloudinary] cleanup warning: list failed "
                f"({exc.__class__.__name__})"
            )
            return

        for public_id in stale_public_ids:
            try:
                outcome = self._destroy_asset(public_id)
            except Exception as exc:
                print(
                    f"[cloudinary] cleanup warning: delete failed: {public_id} "
                    f"({exc.__class__.__name__})"
                )
                continue
            if outcome == "ok":
                print(f"[cloudinary] cleanup deleted: {public_id}")
            elif outcome != "not found":
                print(
                    f"[cloudinary] cleanup warning: delete not confirmed: {public_id}"
                )

    def _stale_public_ids(self) -> list[str]:
        now = self._now_factory()
        if now.tzinfo is None:
            raise ValueError("Cloudinary cleanup clock must include timezone")
        cutoff = now.astimezone(timezone.utc) - self._STALE_AFTER
        stale: list[str] = []
        seen_public_ids: set[str] = set()
        seen_cursors: set[str] = set()
        next_cursor = ""

        while True:
            options: dict[str, Any] = {
                "resource_type": "video",
                "type": "upload",
                "prefix": self._TEMP_PUBLIC_ID_PREFIX,
                "fields": ["public_id", "created_at"],
                "max_results": 500,
                "cloud_name": self._cloud_name,
                "api_key": self._api_key,
                "api_secret": self._api_secret,
                "timeout": self._timeout_seconds,
            }
            if next_cursor:
                options["next_cursor"] = next_cursor
            result = self._admin_client.resources(**options)
            if not isinstance(result, Mapping):
                raise PublishingError(
                    "InstagramPublisher: Cloudinary retornou uma resposta inesperada no cleanup."
                )
            resources = result.get("resources", [])
            if not isinstance(resources, list):
                raise PublishingError(
                    "InstagramPublisher: Cloudinary retornou assets invalidos no cleanup."
                )
            for resource in resources:
                if not isinstance(resource, Mapping):
                    continue
                public_id = str(resource.get("public_id", "")).strip()
                if (
                    not public_id.startswith(self._TEMP_PUBLIC_ID_PREFIX)
                    or public_id == self._public_id
                    or public_id in seen_public_ids
                ):
                    continue
                created_at = _parse_cloudinary_datetime(resource.get("created_at"))
                if created_at is None or created_at >= cutoff:
                    continue
                seen_public_ids.add(public_id)
                stale.append(public_id)

            returned_cursor = str(result.get("next_cursor", "")).strip()
            if not returned_cursor or returned_cursor in seen_cursors:
                break
            seen_cursors.add(returned_cursor)
            next_cursor = returned_cursor

        return stale

    def _destroy_asset(self, public_id: str) -> str:
        if not public_id.startswith(self._TEMP_PUBLIC_ID_PREFIX):
            raise PublishingError(
                "InstagramPublisher: cleanup recusou public_id fora do namespace temporario."
            )
        result = self._uploader.destroy(
            public_id,
            resource_type="video",
            type="upload",
            invalidate=True,
            cloud_name=self._cloud_name,
            api_key=self._api_key,
            api_secret=self._api_secret,
            timeout=self._timeout_seconds,
        )
        if not isinstance(result, Mapping):
            return ""
        return str(result.get("result", "")).strip().casefold()


class _TerminalContainerError(ApiError):
    """Container state that makes removal of the hosted source safe."""


class InstagramPublisher(Publisher):
    platform = "instagram"
    display_name = "InstagramPublisher"

    def __init__(
        self,
        *args: Any,
        poll_interval_seconds: float = 5.0,
        poll_timeout_seconds: float = 300.0,
        video_host: VideoHost | None = None,
        cloudinary_client: _CloudinaryUploader | None = None,
        cloudinary_admin_client: _CloudinaryAdmin | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.video_host = video_host
        self.cloudinary_client = cloudinary_client
        self.cloudinary_admin_client = cloudinary_admin_client
        self._pending_video_hosts: dict[str, VideoHost] = {}

    def validate(self, context: PublishContext, require_credentials: bool = True) -> None:
        self._validate_common(context)
        caption = str(context.metadata.get("caption", "")).strip()
        if not caption:
            raise PublishingError("InstagramPublisher: caption nao pode ficar vazia.")
        if require_credentials:
            missing = [
                key
                for key in (
                    "INSTAGRAM_ACCESS_TOKEN",
                    "INSTAGRAM_ACCOUNT_ID",
                    "META_GRAPH_API_VERSION",
                )
                if not self.credentials.has(key)
            ]
            if missing:
                raise PublishingError(
                    "InstagramPublisher: credenciais nao configuradas "
                    f"(faltando: {', '.join(missing)})."
                )
            host = self._host()
            direct_url = str(context.metadata.get("video_url", "")).strip()
            if direct_url:
                _require_https_url(
                    direct_url,
                    "video_url",
                    require_public_host=True,
                )
            elif (
                host == "graph.instagram.com"
                and self.video_host is None
                and self._configured_video_host(context.episode) is None
            ):
                raise _instagram_login_video_url_error()

    def upload(self, context: PublishContext) -> Mapping[str, Any]:
        self.validate(context, require_credentials=True)
        rendered = render_platform_text({"instagram": context.metadata}, "instagram")
        token = self.credentials.get("INSTAGRAM_ACCESS_TOKEN")
        account_id = self.credentials.get("INSTAGRAM_ACCOUNT_ID")
        video_url, hosted_video, use_resumable = self._video_source(context, rendered)
        fields: dict[str, Any] = {
            "media_type": "REELS",
            "caption": rendered["caption"],
            "share_to_feed": str(bool(rendered.get("share_to_feed", True))).lower(),
        }
        cover_url = str(rendered.get("cover_url", "")).strip()
        if cover_url:
            fields["cover_url"] = cover_url
        elif "thumb_offset_ms" in rendered:
            fields["thumb_offset"] = int(rendered["thumb_offset_ms"])
        if video_url:
            fields["video_url"] = video_url
        else:
            fields["upload_type"] = "resumable"

        response = self._request(
            "POST",
            self._api_url(f"/{account_id}/media"),
            **self._graph_request_kwargs(data=fields),
        )
        payload = self._json(response, self.display_name)
        container_id = str(payload.get("id", "")).strip()
        if not container_id:
            raise ApiError("InstagramPublisher: criacao do container nao retornou id.")

        if use_resumable:
            upload_uri = str(payload.get("uri", "")).strip() or (
                f"https://rupload.facebook.com/ig-api-upload/"
                f"{self.credentials.get('META_GRAPH_API_VERSION')}/{container_id}"
            )
            file_size = context.video_path.stat().st_size
            with context.video_path.open("rb") as media:
                self._request(
                    "POST",
                    upload_uri,
                    headers={
                        "Authorization": f"OAuth {token}",
                        "offset": "0",
                        "file_size": str(file_size),
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(file_size),
                    },
                    data=media,
                )
        try:
            status = self._wait_until_finished(container_id)
        except _TerminalContainerError:
            if hosted_video is not None and not isinstance(
                hosted_video,
                CloudinaryVideoHost,
            ):
                self._cleanup_video_host(hosted_video)
            raise
        if hosted_video is not None:
            self._pending_video_hosts[container_id] = hosted_video
        return {"container_id": container_id, "container_status": status}

    def publish(
        self,
        context: PublishContext,
        upload_result: Mapping[str, Any],
    ) -> PublishResult:
        container_id = str(upload_result.get("container_id", "")).strip()
        if not container_id:
            raise PublishingError("InstagramPublisher: upload_result nao contem container_id.")
        account_id = self.credentials.get("INSTAGRAM_ACCOUNT_ID")
        hosted_video = self._pending_video_hosts.get(container_id)
        response = self._request(
            "POST",
            self._api_url(f"/{account_id}/media_publish"),
            **self._graph_request_kwargs(data={"creation_id": container_id}),
        )
        payload = self._json(response, self.display_name)
        media_id = str(payload.get("id", "")).strip()
        if not media_id:
            raise ApiError("InstagramPublisher: media_publish nao retornou media id.")
        print(f"[instagram] published: {media_id}")
        if hosted_video is not None:
            self._pending_video_hosts.pop(container_id, None)
        details: dict[str, Any] = {"container_id": container_id}
        url: str | None = None
        try:
            info = self._media_info(media_id)
        except ApiError as exc:
            details["warning"] = str(exc)
            details["status_lookup_failed"] = True
        else:
            permalink = info.get("permalink")
            if isinstance(permalink, str) and permalink:
                url = permalink
            details["media"] = info
        if hosted_video is not None:
            cleanup_warning = self._cleanup_video_host(hosted_video)
            if cleanup_warning:
                details["cleanup_warning"] = cleanup_warning
        return PublishResult(
            platform=self.platform,
            status="published",
            external_id=media_id,
            url=url,
            details=details,
        )

    def get_status(self, external_id: str) -> Mapping[str, Any]:
        if not external_id.strip():
            raise PublishingError("InstagramPublisher: container/media id ausente.")
        token = self.credentials.get("INSTAGRAM_ACCESS_TOKEN")
        if not token:
            raise PublishingError("InstagramPublisher: credenciais nao configuradas.")
        response = self._request(
            "GET",
            self._api_url(f"/{external_id}"),
            **self._graph_request_kwargs(params={"fields": "status_code,status"}),
        )
        return self._json(response, self.display_name)

    def _wait_until_finished(self, container_id: str) -> Mapping[str, Any]:
        deadline = time.monotonic() + self.poll_timeout_seconds
        last: Mapping[str, Any] = {}
        while time.monotonic() <= deadline:
            last = self.get_status(container_id)
            status = str(last.get("status_code", "")).upper()
            if status == "FINISHED":
                return last
            if status in {"ERROR", "EXPIRED", "PUBLISHED"}:
                raise _TerminalContainerError(
                    f"InstagramPublisher: container {container_id} terminou com status {status}."
                )
            if self.poll_interval_seconds > 0:
                time.sleep(min(self.poll_interval_seconds, 60.0))
        raise ApiError(
            f"InstagramPublisher: timeout aguardando o container {container_id}; "
            f"ultimo status: {last.get('status_code', 'desconhecido')}."
        )

    def _media_info(self, media_id: str) -> Mapping[str, Any]:
        response = self._request(
            "GET",
            self._api_url(f"/{media_id}"),
            **self._graph_request_kwargs(
                params={"fields": "id,permalink,media_type"}
            ),
        )
        return self._json(response, self.display_name)

    def _video_source(
        self,
        context: PublishContext,
        rendered: Mapping[str, Any],
    ) -> tuple[str, VideoHost | None, bool]:
        direct_url = str(rendered.get("video_url", "")).strip()
        if direct_url:
            return (
                _require_https_url(
                    direct_url,
                    "video_url",
                    require_public_host=True,
                ),
                None,
                False,
            )

        video_host = self.video_host
        if video_host is None:
            video_host = self._configured_video_host(context.episode)
        if video_host is not None:
            try:
                public_url = video_host.upload(context.video_path)
            except Exception as exc:
                self._cleanup_video_host(video_host)
                if isinstance(exc, PublishingError):
                    raise
                raise PublishingError(
                    "InstagramPublisher: VideoHost falhou ao hospedar o MP4 "
                    f"({exc.__class__.__name__})."
                ) from exc
            try:
                public_url = _require_https_url(
                    str(public_url).strip(),
                    "URL retornada pelo VideoHost",
                    require_public_host=True,
                )
            except PublishingError:
                self._cleanup_video_host(video_host)
                raise
            return public_url, video_host, False

        if self._host() == "graph.instagram.com":
            raise _instagram_login_video_url_error()
        return "", None, True

    def _configured_video_host(self, episode: str) -> VideoHost | None:
        backend = self.credentials.get("INSTAGRAM_VIDEO_HOST").strip().lower()
        if not backend:
            return None
        if backend == "cloudinary":
            keys = (
                "CLOUDINARY_CLOUD_NAME",
                "CLOUDINARY_API_KEY",
                "CLOUDINARY_API_SECRET",
            )
            missing = [key for key in keys if not self.credentials.has(key)]
            if missing:
                raise PublishingError(
                    "InstagramPublisher: Cloudinary nao configurado "
                    f"(faltando: {', '.join(missing)})."
                )
            return CloudinaryVideoHost(
                cloud_name=self.credentials.get("CLOUDINARY_CLOUD_NAME"),
                api_key=self.credentials.get("CLOUDINARY_API_KEY"),
                api_secret=self.credentials.get("CLOUDINARY_API_SECRET"),
                episode=episode,
                uploader=self.cloudinary_client,
                admin_client=self.cloudinary_admin_client,
                timeout_seconds=self.timeout_seconds,
            )
        if backend != "presigned":
            raise PublishingError(
                "InstagramPublisher: INSTAGRAM_VIDEO_HOST deve ser "
                "'cloudinary' ou 'presigned'."
            )
        keys = (
            "INSTAGRAM_VIDEO_HOST_UPLOAD_URL",
            "INSTAGRAM_VIDEO_HOST_PUBLIC_URL",
            "INSTAGRAM_VIDEO_HOST_DELETE_URL",
        )
        missing = [key for key in keys if not self.credentials.has(key)]
        if missing:
            raise PublishingError(
                "InstagramPublisher: VideoHost presigned nao configurado "
                f"(faltando: {', '.join(missing)})."
            )
        return PresignedUrlVideoHost(
            upload_url=self.credentials.get("INSTAGRAM_VIDEO_HOST_UPLOAD_URL"),
            public_url=self.credentials.get("INSTAGRAM_VIDEO_HOST_PUBLIC_URL"),
            delete_url=self.credentials.get("INSTAGRAM_VIDEO_HOST_DELETE_URL"),
            session=self.session,
            timeout_seconds=self.timeout_seconds,
        )

    @staticmethod
    def _cleanup_video_host(video_host: VideoHost) -> str | None:
        try:
            video_host.cleanup()
        except PublishingError as exc:
            return str(exc)
        except Exception:
            return "InstagramPublisher: cleanup do VideoHost temporario falhou."
        return None

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            return super()._request(method, url, **kwargs)
        except ApiError as exc:
            sensitive_values = [
                self.credentials.get("INSTAGRAM_ACCESS_TOKEN"),
                self.credentials.get("INSTAGRAM_VIDEO_HOST_UPLOAD_URL"),
                self.credentials.get("INSTAGRAM_VIDEO_HOST_PUBLIC_URL"),
                self.credentials.get("INSTAGRAM_VIDEO_HOST_DELETE_URL"),
                self.credentials.get("CLOUDINARY_CLOUD_NAME"),
                self.credentials.get("CLOUDINARY_API_KEY"),
                self.credentials.get("CLOUDINARY_API_SECRET"),
            ]
            for key in ("data", "params"):
                values = kwargs.get(key)
                if isinstance(values, Mapping):
                    sensitive_values.extend(
                        str(values[field])
                        for field in ("access_token", "video_url")
                        if values.get(field)
                    )
            safe_message = _redact_known_values(str(exc), sensitive_values)
            if safe_message == str(exc):
                raise
            raise ApiError(safe_message) from None

    def _graph_request_kwargs(
        self,
        *,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if (data is None) == (params is None):
            raise ValueError("Informe exatamente um de data ou params.")

        token = self.credentials.get("INSTAGRAM_ACCESS_TOKEN")
        key = "data" if data is not None else "params"
        values = dict(data if data is not None else params or {})
        kwargs: dict[str, Any] = {key: values}
        if self._host() == "graph.instagram.com":
            values["access_token"] = token
        else:
            kwargs["headers"] = {"Authorization": f"Bearer {token}"}
        return kwargs

    def _host(self) -> str:
        host = self.credentials.get("INSTAGRAM_API_HOST", "graph.facebook.com").strip()
        if host not in {"graph.facebook.com", "graph.instagram.com"}:
            raise PublishingError(
                "InstagramPublisher: INSTAGRAM_API_HOST deve ser "
                "graph.facebook.com ou graph.instagram.com."
            )
        return host

    def _api_url(self, path: str) -> str:
        version = self.credentials.get("META_GRAPH_API_VERSION")
        return f"https://{self._host()}/{version}{path}"


def _require_https_url(
    value: str,
    label: str,
    *,
    require_public_host: bool = False,
) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        parsed.port
        valid = (
            parsed.scheme.lower() == "https"
            and bool(hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and not any(character.isspace() for character in value)
            and (not require_public_host or _is_potentially_public_host(hostname))
        )
    except ValueError:
        valid = False
    if not valid:
        raise PublishingError(
            f"InstagramPublisher: {label} precisa ser uma URL HTTPS valida."
        )
    return value


def _instagram_login_video_url_error() -> PublishingError:
    return PublishingError(
        "InstagramPublisher: Instagram Login requer uma video_url "
        "HTTPS publicamente acessível."
    )


def _safe_public_id_part(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.strip().casefold())
    return normalized.strip("-_")[:100] or fallback


def _parse_cloudinary_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_potentially_public_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").casefold()
    if normalized == "localhost" or normalized.endswith((".localhost", ".local")):
        return False
    try:
        address = ip_address(normalized)
    except ValueError:
        return True
    return address.is_global


def _redact_known_values(message: str, values: list[str]) -> str:
    secrets = {value for value in values if len(value) >= 4}
    for secret in sorted(secrets, key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
        message = message.replace(secret.replace("/", r"\/"), "[REDACTED]")
    return message
