from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any, Mapping

import requests

from .credentials import CredentialStore


class PublishingError(RuntimeError):
    """User-facing publishing failure."""


class ApiError(PublishingError):
    """Failure returned by, or while contacting, an official platform API."""


@dataclass(frozen=True)
class PublishContext:
    episode: str
    video_path: Path
    cover_path: Path
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class PublishResult:
    platform: str
    status: str
    external_id: str | None = None
    url: str | None = None
    dry_run: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Publisher(ABC):
    """Small common interface implemented by every official API adapter."""

    platform = "base"
    display_name = "Publisher"

    def __init__(
        self,
        credentials: CredentialStore,
        session: requests.Session | None = None,
        timeout_seconds: float = 90.0,
    ) -> None:
        self.credentials = credentials
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    @abstractmethod
    def validate(self, context: PublishContext, require_credentials: bool = True) -> None:
        """Validate local input and, for live use, authorization data."""

    @abstractmethod
    def upload(self, context: PublishContext) -> Mapping[str, Any]:
        """Upload/create the platform media resource."""

    @abstractmethod
    def publish(
        self,
        context: PublishContext,
        upload_result: Mapping[str, Any],
    ) -> PublishResult:
        """Finish publication, or report the action started during upload."""

    @abstractmethod
    def get_status(self, external_id: str) -> Mapping[str, Any]:
        """Return the platform's real status for an uploaded resource."""

    def dry_run(self, context: PublishContext) -> PublishResult:
        self.validate(context, require_credentials=False)
        return PublishResult(
            platform=self.platform,
            status="validated",
            dry_run=True,
            details={
                "episode": context.episode,
                "video": str(context.video_path),
                "cover": str(context.cover_path),
                "metadata": dict(context.metadata),
                "api_calls_made": 0,
            },
        )

    def _validate_common(self, context: PublishContext) -> None:
        if not context.video_path.is_file():
            raise PublishingError(f"Video ausente: {context.video_path}")
        if context.video_path.suffix.lower() != ".mp4":
            raise PublishingError(f"O video precisa ser MP4: {context.video_path}")
        if context.video_path.stat().st_size <= 0:
            raise PublishingError(f"Video vazio: {context.video_path}")
        if not context.cover_path.is_file():
            raise PublishingError(
                f"Capa ausente: {context.cover_path}. Execute prepare_post.py primeiro."
            )
        if context.cover_path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise PublishingError(f"A capa precisa ser JPEG: {context.cover_path}")
        if not isinstance(context.metadata, Mapping):
            raise PublishingError(f"Metadata invalida para {self.platform}.")

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout_seconds)
        try:
            response = self.session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise ApiError(
                f"{self.display_name}: falha de conexao com a API oficial: {exc.__class__.__name__}"
            ) from exc
        if not 200 <= response.status_code < 300:
            detail = _safe_api_detail(response)
            raise ApiError(
                f"{self.display_name}: API retornou HTTP {response.status_code}"
                + (f" ({detail})" if detail else "")
            )
        return response

    @staticmethod
    def _json(response: requests.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, requests.JSONDecodeError) as exc:
            raise ApiError(f"{label}: resposta JSON invalida da API oficial.") from exc
        if not isinstance(payload, dict):
            raise ApiError(f"{label}: resposta inesperada da API oficial.")
        return payload


def _safe_api_detail(response: requests.Response) -> str:
    """Extract a short error without ever echoing authorization material."""

    detail = ""
    try:
        payload = response.json()
    except (ValueError, requests.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
        elif error:
            detail = str(error)
        else:
            detail = str(payload.get("message") or "")
    if not detail:
        detail = response.reason or ""
    detail = re.sub(
        r"(?i)(access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[:=]\s*[^\s,;&]+",
        r"\1=[REDACTED]",
        detail,
    )
    detail = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", detail)
    return detail.strip()[:300]
