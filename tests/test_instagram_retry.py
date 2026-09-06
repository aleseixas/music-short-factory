from __future__ import annotations

"""Focused regression tests for bounded Instagram container retries."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from publish import _publish_instagram_with_retries
from publishing.base import ApiError, PublishContext, PublishResult
from publishing.credentials import CredentialStore


class RecordingPublisher:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = list(failures)
        self.upload_contexts: list[PublishContext] = []
        self.publish_calls = 0

    def upload(self, context: PublishContext):
        self.upload_contexts.append(context)
        if self.failures:
            raise self.failures.pop(0)
        return {"container_id": f"container-{len(self.upload_contexts)}"}

    def publish(self, context: PublishContext, upload_result):
        self.publish_calls += 1
        return PublishResult(
            platform="instagram",
            status="published",
            external_id=str(upload_result["container_id"]),
        )


def context_with_cover(root: Path) -> PublishContext:
    return PublishContext(
        episode="demo",
        video_path=root / "demo.mp4",
        cover_path=root / "demo_cover.jpg",
        metadata={
            "caption": "demo",
            "cover_url": "https://cdn.example.test/cover.jpg",
            "thumb_offset_ms": 700,
        },
    )


class InstagramRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credentials = CredentialStore.from_mapping({})

    def test_processing_error_retries_without_cover_and_then_succeeds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = context_with_cover(Path(temp_dir))
            publisher = RecordingPublisher(
                [ApiError("InstagramPublisher: container 111 terminou com status ERROR.")]
            )

            with (
                patch("publish._log_instagram_video_preflight"),
                patch("publish._log_instagram_container_diagnostics"),
            ):
                result = _publish_instagram_with_retries(
                    publisher,
                    context,
                    self.credentials,
                )

        self.assertEqual(result.status, "published")
        self.assertEqual(len(publisher.upload_contexts), 2)
        self.assertIn("cover_url", publisher.upload_contexts[0].metadata)
        self.assertNotIn("cover_url", publisher.upload_contexts[1].metadata)
        self.assertEqual(publisher.upload_contexts[1].metadata["thumb_offset_ms"], 700)
        self.assertEqual(publisher.publish_calls, 1)

    def test_processing_error_never_exceeds_three_attempts(self):
        failures = [
            ApiError(f"InstagramPublisher: container {index} terminou com status ERROR.")
            for index in (111, 222, 333)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            publisher = RecordingPublisher(failures)
            context = context_with_cover(Path(temp_dir))

            with (
                patch("publish._log_instagram_video_preflight"),
                patch("publish._log_instagram_container_diagnostics"),
            ):
                with self.assertRaisesRegex(ApiError, "container 333"):
                    _publish_instagram_with_retries(
                        publisher,
                        context,
                        self.credentials,
                    )

        self.assertEqual(len(publisher.upload_contexts), 3)
        self.assertIn("cover_url", publisher.upload_contexts[0].metadata)
        self.assertNotIn("cover_url", publisher.upload_contexts[1].metadata)
        self.assertNotIn("cover_url", publisher.upload_contexts[2].metadata)
        self.assertEqual(publisher.publish_calls, 0)

    def test_non_processing_api_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = context_with_cover(Path(temp_dir))
            publisher = RecordingPublisher(
                [ApiError("InstagramPublisher: API retornou HTTP 401")]
            )

            with (
                patch("publish._log_instagram_video_preflight"),
                patch("publish._log_instagram_container_diagnostics"),
            ):
                with self.assertRaisesRegex(ApiError, "HTTP 401"):
                    _publish_instagram_with_retries(
                        publisher,
                        context,
                        self.credentials,
                    )

        self.assertEqual(len(publisher.upload_contexts), 1)
        self.assertEqual(publisher.publish_calls, 0)


if __name__ == "__main__":
    unittest.main()
