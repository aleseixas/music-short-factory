from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from publish import create_publisher
from publishing.base import ApiError, PublishContext, PublishingError
from publishing.credentials import CredentialStore
from publishing.facebook import FacebookPublisher
from publishing.metadata import normalize_post


class JsonResponse:
    status_code = 200
    reason = "OK"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def json(self) -> dict[str, object]:
        return self.payload


class QueueSession:
    def __init__(self, responses: list[JsonResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> JsonResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected network request")
        return self.responses.pop(0)


def context(root: Path, metadata: dict[str, object] | None = None) -> PublishContext:
    video = root / "demo.mp4"
    cover = root / "demo_cover.jpg"
    video.write_bytes(b"facebook reel fixture")
    Image.new("RGB", (32, 48), "black").save(cover, format="JPEG")
    return PublishContext(
        episode="demo",
        video_path=video,
        cover_path=cover,
        metadata=metadata
        or {
            "title": "Titulo curto",
            "caption": "Uma historia curta.",
            "hashtags": ["reels", "historia"],
        },
    )


def credentials() -> CredentialStore:
    return CredentialStore.from_mapping(
        {
            "FACEBOOK_PAGE_ACCESS_TOKEN": "page-token",
            "FACEBOOK_PAGE_ID": "page-123",
            "META_GRAPH_API_VERSION": "v26.0",
        }
    )


class FacebookPublisherTests(unittest.TestCase):
    def test_old_post_without_facebook_derives_compatible_metadata(self):
        normalized = normalize_post(
            {
                "youtube": {"title": "Titulo original"},
                "instagram": {
                    "caption": "Legenda original",
                    "hashtags": ["reels"],
                },
            }
        )

        self.assertEqual(normalized["facebook"]["title"], "Titulo original")
        self.assertEqual(normalized["facebook"]["caption"], "Legenda original")
        self.assertEqual(normalized["facebook"]["hashtags"], ["reels"])

    def test_factory_selects_facebook_adapter(self):
        publisher = create_publisher("facebook", CredentialStore.from_mapping({}))
        self.assertIsInstance(publisher, FacebookPublisher)

    def test_dry_run_needs_no_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = FacebookPublisher(credentials=CredentialStore.from_mapping({}))
            result = publisher.dry_run(context(Path(temp_dir)))

        self.assertTrue(result.dry_run)
        self.assertEqual(result.platform, "facebook")
        self.assertEqual(result.status, "validated")

    def test_live_publish_uses_official_reels_flow_and_polls_status(self):
        session = QueueSession(
            [
                JsonResponse(
                    {
                        "video_id": "video-123",
                        "upload_url": (
                            "https://rupload.facebook.com/video-upload/"
                            "v26.0/video-123"
                        ),
                    }
                ),
                JsonResponse({"success": True}),
                JsonResponse({"success": True}),
                JsonResponse(
                    {
                        "id": "video-123",
                        "status": {
                            "video_status": "ready",
                            "processing_phase": {"status": "complete"},
                            "publishing_phase": {"status": "complete"},
                        },
                    }
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            publish_context = context(Path(temp_dir))
            publisher = FacebookPublisher(
                credentials=credentials(),
                session=session,
                poll_interval_seconds=0,
            )
            uploaded = publisher.upload(publish_context)
            result = publisher.publish(publish_context, uploaded)

        self.assertEqual(result.status, "published")
        self.assertEqual(result.external_id, "video-123")
        self.assertEqual(result.url, "https://www.facebook.com/reel/video-123")
        self.assertEqual([call[0] for call in session.calls], ["POST", "POST", "POST", "GET"])
        self.assertEqual(
            session.calls[0][1],
            "https://graph.facebook.com/v26.0/page-123/video_reels",
        )
        self.assertEqual(session.calls[0][2]["data"]["upload_phase"], "start")
        self.assertEqual(session.calls[1][2]["headers"]["Authorization"], "OAuth page-token")
        self.assertEqual(session.calls[2][2]["data"]["video_state"], "PUBLISHED")
        self.assertIn("#reels", session.calls[2][2]["data"]["description"])
        self.assertEqual(session.calls[3][2]["params"]["fields"], "status")

    def test_missing_live_credentials_fail_before_network(self):
        session = QueueSession([])
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = FacebookPublisher(
                credentials=CredentialStore.from_mapping({}),
                session=session,
            )
            with self.assertRaisesRegex(PublishingError, "FACEBOOK_PAGE_ACCESS_TOKEN"):
                publisher.upload(context(Path(temp_dir)))

        self.assertEqual(session.calls, [])

    def test_rejects_untrusted_upload_url(self):
        session = QueueSession(
            [JsonResponse({"video_id": "video-123", "upload_url": "https://evil.test/file"})]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = FacebookPublisher(credentials=credentials(), session=session)
            with self.assertRaisesRegex(ApiError, "upload_url invalida"):
                publisher.upload(context(Path(temp_dir)))

        self.assertEqual(len(session.calls), 1)

    def test_processing_error_is_terminal(self):
        session = QueueSession(
            [
                JsonResponse({"success": True}),
                JsonResponse(
                    {
                        "status": {
                            "video_status": "error",
                            "processing_phase": {"status": "error"},
                            "publishing_phase": {"status": "not_started"},
                        }
                    }
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = FacebookPublisher(
                credentials=credentials(),
                session=session,
                poll_interval_seconds=0,
            )
            with self.assertRaisesRegex(ApiError, "terminou com erro"):
                publisher.publish(
                    context(Path(temp_dir)),
                    {"video_id": "video-123"},
                )
