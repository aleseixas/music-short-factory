"""Read-only platform observations for crash recovery; never resend a payload."""
from __future__ import annotations

from typing import Any, Callable


def confirmed_receipt(platform: str, result: Any) -> bool:
    """Only terminal adapter receipts establish successful delivery."""
    statuses = {
        "youtube": {"uploaded", "uploaded_without_thumbnail"},
        "instagram": {"published"}, "facebook": {"published"},
        "tiktok": {"publish_complete", "send_to_user_inbox"},
    }
    return (bool(result.external_id) and not result.dry_run
            and str(result.status).lower() in statuses.get(platform, set()))


def remote_resource(upload: dict[str, Any]) -> str | None:
    for key in ("publish_id", "container_id", "video_id"):
        value = upload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def classify_observation(platform: str, payload: dict[str, Any]) -> str:
    # Missing/deleted resources and API failures cannot prove a send never
    # happened. Only affirmative observations establish confirmed_sent.
    if platform == "youtube" and payload.get("id"):
        return "confirmed_sent"
    if platform == "instagram" and str(payload.get("status_code", "")).upper() == "PUBLISHED":
        return "confirmed_sent"
    if platform == "tiktok" and str(payload.get("status", "")).upper() in {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}:
        return "confirmed_sent"
    if platform == "facebook":
        status = payload.get("status", {})
        phase = status.get("publishing_phase", {}) if isinstance(status, dict) else {}
        if isinstance(phase, dict) and str(phase.get("status", "")).lower() == "complete":
            return "confirmed_sent"
    return "uncertain"


def platform_observer(root) -> Callable[[str, dict[str, Any]], str]:
    from publish import create_publisher
    from .credentials import CredentialStore

    def observe(platform: str, attempt: dict[str, Any]) -> str:
        external_id = attempt.get("remote_id")
        if not external_id:
            return "uncertain"
        try:
            publisher = create_publisher(platform, CredentialStore.load(root))
            payload = publisher.get_status(str(external_id))
            return classify_observation(platform, dict(payload))
        except Exception:
            return "uncertain"
    return observe
