from __future__ import annotations

from pathlib import Path
import unittest

from publishing.base import PublishContext, PublishingError
from publishing.credentials import CredentialStore
from publishing.tiktok import TikTokPublisher


class StubTikTokPublisher(TikTokPublisher):
    def __init__(self, statuses: list[dict[str, object]]) -> None:
        super().__init__(credentials=CredentialStore.from_mapping({}))
        self.statuses = list(statuses)
        self.status_calls = 0
        self.STATUS_POLL_INTERVAL_SECONDS = 0
        self.STATUS_POLL_MAX_ATTEMPTS = max(1, len(self.statuses))

    def get_status(self, external_id: str) -> dict[str, object]:
        self.status_calls += 1
        if not self.statuses:
            raise AssertionError(f"status inesperado para {external_id}")
        return self.statuses.pop(0)


def context(post_mode: str = "draft") -> PublishContext:
    return PublishContext(
        episode="demo",
        video_path=Path("demo.mp4"),
        cover_path=Path("demo_cover.jpg"),
        metadata={"post_mode": post_mode},
    )


class TikTokStatusPollingTests(unittest.TestCase):
    def test_draft_only_succeeds_after_inbox_delivery(self) -> None:
        publisher = StubTikTokPublisher(
            [
                {"status": "PROCESSING_UPLOAD", "uploaded_bytes": 123},
                {"status": "SEND_TO_USER_INBOX", "uploaded_bytes": 123},
            ]
        )

        result = publisher.publish(
            context("draft"),
            {"publish_id": "v_inbox_file~test", "post_mode": "draft"},
        )

        self.assertEqual(result.status, "send_to_user_inbox")
        self.assertEqual(result.external_id, "v_inbox_file~test")
        self.assertTrue(result.details["confirmed_terminal"])
        self.assertTrue(result.details["requires_user_action"])
        self.assertEqual(publisher.status_calls, 2)

    def test_failed_processing_is_a_real_publish_failure(self) -> None:
        publisher = StubTikTokPublisher(
            [{"status": "FAILED", "fail_reason": "video_pull_failed"}]
        )

        with self.assertRaisesRegex(PublishingError, "FAILED") as raised:
            publisher.publish(
                context("draft"),
                {"publish_id": "v_inbox_file~failed", "post_mode": "draft"},
            )

        self.assertIn("video_pull_failed", str(raised.exception))
        self.assertIn("v_inbox_file~failed", str(raised.exception))

    def test_processing_timeout_is_not_reported_as_success(self) -> None:
        publisher = StubTikTokPublisher(
            [
                {"status": "PROCESSING_UPLOAD"},
                {"status": "PROCESSING_UPLOAD"},
            ]
        )

        with self.assertRaisesRegex(PublishingError, "nao confirmou um estado terminal"):
            publisher.publish(
                context("draft"),
                {"publish_id": "v_inbox_file~timeout", "post_mode": "draft"},
            )

        self.assertEqual(publisher.status_calls, 2)

    def test_direct_post_requires_publish_complete(self) -> None:
        publisher = StubTikTokPublisher([{"status": "SEND_TO_USER_INBOX"}])

        with self.assertRaisesRegex(PublishingError, "estado terminal inesperado"):
            publisher.publish(
                context("direct"),
                {"publish_id": "v_post~unexpected", "post_mode": "direct"},
            )


if __name__ == "__main__":
    unittest.main()
