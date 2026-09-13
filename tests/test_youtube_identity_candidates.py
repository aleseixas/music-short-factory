from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import check_episode_media as preflight
import check_episode_media_batch as batch_preflight
import resolve_visual_candidates as resolver
import resolve_visual_candidates_web as web_resolver
from engine.models import AssetSpec
from engine.visual_candidates import normalize_visual_candidate, validate_visual_candidate_pool
from engine.visual_repetition import canonicalize_visual_url
from engine.visual_search import VisualSearchResult
from engine.youtube import canonical_youtube_url, extract_youtube_video_id


VIDEO_A = "jsz2fjVDtEo"
VIDEO_B = "3wn9ec2Emuc"


class YoutubeIdentityTests(unittest.TestCase):
    def test_url_forms_share_case_sensitive_identity(self):
        expected = f"https://www.youtube.com/watch?v={VIDEO_A}"
        for url in (
            expected + "&si=tracking&t=12&feature=share",
            f"https://youtu.be/{VIDEO_A}?si=tracking",
            f"https://youtube.com/shorts/{VIDEO_A}?feature=share",
            f"https://youtube.com/embed/{VIDEO_A}?start=12",
            f"http://m.youtube.com/watch?v={VIDEO_A}",
        ):
            with self.subTest(url=url):
                self.assertEqual(extract_youtube_video_id(url), VIDEO_A)
                self.assertEqual(canonical_youtube_url(url), expected)
                self.assertEqual(canonicalize_visual_url(url), expected)
                self.assertEqual(preflight._normalize_url_identity(url), expected)

    def test_different_ids_and_different_case_do_not_collide(self):
        identities = {
            preflight._normalize_url_identity(f"https://www.youtube.com/watch?v={value}")
            for value in (VIDEO_A, VIDEO_B, VIDEO_A.lower())
        }
        self.assertEqual(len(identities), 3)

    def test_invalid_ambiguous_or_impersonating_urls_are_not_youtube_videos(self):
        for url in (
            "https://www.youtube.com/watch", "https://www.youtube.com/watch?v=1",
            f"https://www.youtube.com/watch?v={VIDEO_A}&v={VIDEO_B}",
            f"https://youtube.com.evil.invalid/watch?v={VIDEO_A}",
            f"https://user:secret@youtube.com/watch?v={VIDEO_A}",
        ):
            with self.subTest(url=url):
                self.assertIsNone(extract_youtube_video_id(url))

    @staticmethod
    def _episode(urls):
        assets = {
            f"asset_{index}": AssetSpec(id=f"asset_{index}", file=f"file_{index}.mp4", url=url,
                                         credit="fixture", license="", focus_x=0.5, focus_y=0.5)
            for index, url in enumerate(urls)
        }
        return SimpleNamespace(assets=assets, shots=[
            SimpleNamespace(id=f"shot_{index}", asset_id=asset_id)
            for index, asset_id in enumerate(assets)
        ])

    def test_preflight_accepts_different_youtube_videos(self):
        episode = self._episode([
            f"https://www.youtube.com/watch?v={VIDEO_A}",
            f"https://www.youtube.com/watch?v={VIDEO_B}",
        ])
        with redirect_stdout(io.StringIO()):
            preflight._assert_intra_episode_visuals_unique(episode)

    def test_preflight_distinguishes_case_sensitive_ids_in_cache_filenames(self):
        ids = (VIDEO_A, VIDEO_A.lower())
        episode = self._episode([f"https://youtube.com/watch?v={value}" for value in ids])
        episode.assets = {
            asset_id: replace(asset, file=f"youtube-{ids[index]}.mp4")
            for index, (asset_id, asset) in enumerate(episode.assets.items())
        }
        with redirect_stdout(io.StringIO()):
            preflight._assert_intra_episode_visuals_unique(episode)

    def test_preflight_blocks_same_video_across_url_formats(self):
        for url in (f"https://youtu.be/{VIDEO_A}", f"https://youtube.com/shorts/{VIDEO_A}",
                    f"https://youtube.com/embed/{VIDEO_A}"):
            with self.subTest(url=url):
                episode = self._episode([f"https://youtube.com/watch?v={VIDEO_A}", url])
                with self.assertRaisesRegex(RuntimeError, f"identidade=youtube:{VIDEO_A}"):
                    preflight._assert_intra_episode_visuals_unique(episode)

    def test_resolver_identity_uses_video_id_not_numeric_internal_id(self):
        a = {"kind": "video", "provider_id": "1", "url": f"https://youtu.be/{VIDEO_A}"}
        alias = dict(a, url=f"https://youtube.com/shorts/{VIDEO_A}", provider_id="other")
        b = dict(a, url=f"https://youtube.com/watch?v={VIDEO_B}")
        self.assertEqual(resolver._candidate_source_key(a), resolver._candidate_source_key(alias))
        self.assertNotEqual(resolver._candidate_source_key(a), resolver._candidate_source_key(b))
        self.assertEqual(resolver._candidate_log_reference(a, 1), (
            VIDEO_A, f"https://www.youtube.com/watch?v={VIDEO_A}", "yt-dlp",
        ))

    def test_bad_locator_diagnostics_do_not_crash_or_expose_credentials(self):
        for url in ("https://[invalid", "https://user:PRIVATE@example.com/video.mp4"):
            with self.subTest(url=url):
                candidate = {"kind": "video", "url": url}
                resolver._candidate_source_key(candidate)
                diagnostic = resolver._candidate_log_reference(candidate, 1)
                self.assertEqual(diagnostic[1], "<invalid>")
                self.assertNotIn("PRIVATE", str(diagnostic))


class VisualCandidateAuthorshipTests(unittest.TestCase):
    @staticmethod
    def _pool(candidate):
        return {"schema_version": 1, "slots": [{"id": "hook", "candidates": [candidate]}]}

    def test_placeholder_video_fails_early_with_location(self):
        for candidate in (
            {"id": 1, "name": "Halsey festival search", "kind": "video"},
            {"id": 1, "name": "Halsey festival search", "kind": "video", "source": "youtube"},
            {"kind": "video", "provider_id": "1", "search_provider": "youtube_web"},
            {"kind": "video", "url": "https://www.youtube.com/watch"},
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(ValueError, "slot hook, candidato 1"):
                    validate_visual_candidate_pool(self._pool(candidate))

    def test_placeholder_image_also_is_not_a_real_candidate(self):
        with self.assertRaisesRegex(ValueError, "localizador"):
            normalize_visual_candidate({"kind": "image", "name": "Halsey health photo"})

    def test_preflight_gate_rejects_placeholder_before_media_acquisition(self):
        with TemporaryDirectory() as directory:
            episode = SimpleNamespace(directory=Path(directory), shots=[])
            (episode.directory / "visual_candidates.json").write_text(json.dumps(self._pool(
                {"kind": "video", "name": "Halsey festival", "id": 1}
            )), encoding="utf-8")
            errors = batch_preflight._collect_visual_authoring_errors(episode, "halsey")
            self.assertEqual([error["code"] for error in errors], ["VISUAL_CANDIDATE_POOL_INVALID"])
            self.assertIn("slot hook, candidato 1", errors[0]["detail"])

    def test_preflight_gate_accepts_reconstructible_youtube_candidate(self):
        with TemporaryDirectory() as directory:
            episode = SimpleNamespace(directory=Path(directory), shots=[])
            (episode.directory / "visual_candidates.json").write_text(json.dumps(self._pool(
                {"kind": "video", "source": "youtube", "provider_id": VIDEO_A}
            )), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(batch_preflight._collect_visual_authoring_errors(episode, "halsey"), [])

    def test_youtube_url_preserves_metadata_and_restores_video_id(self):
        original = {"id": 1, "kind": "video", "url": f"https://youtu.be/{VIDEO_A}?si=tracking",
                    "creator": "Halsey", "rights_status": "unknown", "semantic_fit": "direct"}
        candidate = validate_visual_candidate_pool(self._pool(original))["slots"][0]["candidates"][0]
        self.assertEqual(candidate["provider_id"], VIDEO_A)
        self.assertEqual(candidate["video_id"], VIDEO_A)
        self.assertEqual(candidate["source"], "youtube")
        self.assertEqual(candidate["search_provider"], "youtube_web")
        self.assertEqual(candidate["url"], f"https://www.youtube.com/watch?v={VIDEO_A}")
        self.assertEqual(candidate["creator"], "Halsey")
        self.assertEqual(candidate["semantic_fit"], "direct")
        self.assertNotIn("provider_id", original)

    def test_explicit_video_provider_id_is_reconstructible_without_url(self):
        candidate = normalize_visual_candidate({"kind": "video", "source": "youtube", "provider_id": VIDEO_A})
        self.assertEqual(candidate["url"], f"https://www.youtube.com/watch?v={VIDEO_A}")
        result = web_resolver._candidate_result(candidate, "hook", 1)
        self.assertEqual(result.provider_id, VIDEO_A)
        self.assertEqual(result.source_page_url, candidate["url"])

    def test_conflicting_video_ids_fail_early(self):
        with self.assertRaisesRegex(ValueError, "consistente"):
            normalize_visual_candidate({"kind": "video", "provider_id": VIDEO_B,
                                        "url": f"https://youtube.com/watch?v={VIDEO_A}"})

    def test_legacy_direct_url_remains_resolvable(self):
        original = {"kind": "video", "url": "https://upload.wikimedia.org/clip.webm", "credit": "author"}
        candidate = normalize_visual_candidate(original)
        self.assertEqual(candidate["url"], original["url"])
        self.assertEqual(candidate["credit"], "author")

    def test_discovery_serialization_can_be_authored_without_losing_locator(self):
        discovery = VisualSearchResult(
            provider_id=VIDEO_A, name="Festival", kind="video", source="youtube",
            search_provider="youtube_web", source_page_url=f"https://youtu.be/{VIDEO_A}",
            creator="artist", license="", license_url="", attribution="artist / YouTube",
            width=1920, height=1080, duration_seconds=30,
        )
        payload = discovery.as_dict()
        self.assertEqual(payload["video_id"], VIDEO_A)
        self.assertEqual(payload["url"], f"https://www.youtube.com/watch?v={VIDEO_A}")
        candidate = normalize_visual_candidate(payload)
        self.assertEqual(candidate["width"], 1920)
        self.assertEqual(candidate["duration_seconds"], 30)
        self.assertEqual(web_resolver._candidate_result(candidate, "hook", 1).provider_id, VIDEO_A)


if __name__ == "__main__":
    unittest.main()
