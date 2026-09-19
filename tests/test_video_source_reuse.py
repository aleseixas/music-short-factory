from __future__ import annotations

import unittest

import resolve_visual_candidates_web as resolver


class VideoSourceReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        resolver.SELECTED_VIDEO_SEGMENTS.clear()
        resolver.INSPECTED_VIDEO_SEGMENTS.clear()

    @staticmethod
    def _youtube_candidate(*, start=None, end=None, file_name="take.mp4") -> dict:
        candidate = {
            "name": "Relevant source",
            "kind": "video",
            "url": "https://www.youtube.com/watch?v=VIDEO123",
            "source_page_url": "https://www.youtube.com/watch?v=VIDEO123",
            "search_provider": "youtube_web",
            "provider_id": "VIDEO123",
            "file": file_name,
        }
        if start is not None:
            candidate["source_start_seconds"] = start
        if end is not None:
            candidate["source_end_seconds"] = end
        return candidate

    @staticmethod
    def _slot(required_seconds=4.0) -> dict:
        return {
            "id": "slot_01",
            "required_seconds": required_seconds,
            "crossfade_seconds": 0.0,
            "_shot": {},
            "_output_fps": 30,
        }

    def test_same_provider_with_different_authored_segments_has_different_key(self):
        first = self._youtube_candidate(start=10.0, end=14.0)
        second = self._youtube_candidate(start=30.0, end=34.0)
        same_as_first = self._youtube_candidate(start=10.0, end=14.0, file_name="alias.mp4")

        self.assertNotEqual(
            resolver._candidate_source_key(first),
            resolver._candidate_source_key(second),
        )
        self.assertEqual(
            resolver._candidate_source_key(first),
            resolver._candidate_source_key(same_as_first),
        )
        self.assertEqual(
            resolver._video_source_identity(first),
            resolver._video_source_identity(second),
        )

    def test_auto_start_moves_reused_source_after_existing_segment(self):
        candidate = self._youtube_candidate()
        source = resolver._video_source_identity(candidate)
        resolver.SELECTED_VIDEO_SEGMENTS[source] = [(0.0, 4.0)]

        resolver._assign_auto_video_start(self._slot(), candidate, source)
        start, end = resolver._requested_video_interval(self._slot(), candidate)

        self.assertGreaterEqual(start, 6.0)
        self.assertGreater(end, start)
        self.assertIsNone(resolver._reuse_rejection_reason(source, start, end))

    def test_overlapping_segment_is_rejected_but_distinct_segment_is_allowed(self):
        candidate = self._youtube_candidate()
        source = resolver._video_source_identity(candidate)
        resolver.SELECTED_VIDEO_SEGMENTS[source] = [(10.0, 15.0)]

        self.assertEqual(
            resolver._reuse_rejection_reason(source, 14.0, 18.0),
            "video_segment_overlaps_selected",
        )
        self.assertIsNone(
            resolver._reuse_rejection_reason(source, 18.0, 22.0),
        )

    def test_source_reuse_is_capped_at_ten_selected_segments(self):
        candidate = self._youtube_candidate()
        source = resolver._video_source_identity(candidate)
        resolver.SELECTED_VIDEO_SEGMENTS[source] = [
            (float(i * 10), float(i * 10 + 4))
            for i in range(10)
        ]

        self.assertEqual(
            resolver._reuse_rejection_reason(source, 100.0, 104.0),
            "video_source_reuse_limit",
        )

    def test_same_youtube_provider_stages_to_one_canonical_filename(self):
        first = resolver._safe_filename("", "youtube-VIDEO123", ".mp4")
        second = resolver._safe_filename("", "youtube-VIDEO123", ".mp4")
        self.assertEqual(first, second)
        self.assertEqual(first, "youtube-VIDEO123.mp4")


if __name__ == "__main__":
    unittest.main()
