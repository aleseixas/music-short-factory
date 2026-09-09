from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from engine.ffmpeg import run_ffmpeg
import engine.visual_repetition as repetition
from engine.visual_repetition import (
    MAX_REPETITION_PENALTY,
    RepetitionAssessment,
    VisualFingerprint,
    VisualHistoryEntry,
    assess_repetition,
    build_visual_fingerprint,
    canonicalize_visual_url,
    fingerprint_to_usage,
    load_visual_history,
)


def _pattern(seed: int = 19) -> Image.Image:
    """Create a reproducible, textured scene without external image fixtures."""
    generator = random.Random(seed)
    image = Image.new("RGB", (320, 240), (41, 66, 91))
    draw = ImageDraw.Draw(image)
    for index in range(42):
        left = generator.randrange(0, 290)
        top = generator.randrange(0, 210)
        right = min(319, left + generator.randrange(12, 130))
        bottom = min(239, top + generator.randrange(12, 100))
        colour = tuple(generator.randrange(256) for _ in range(3))
        if index % 2:
            draw.ellipse((left, top, right, bottom), fill=colour)
        else:
            draw.rectangle((left, top, right, bottom), fill=colour)
    return image


def _entry(
    fingerprint: VisualFingerprint,
    *,
    episode: str = "prior_episode",
    shot_id: str = "shot_a",
    rank: int = 0,
    recorded_at: str | None = None,
) -> VisualHistoryEntry:
    return VisualHistoryEntry(
        episode=episode,
        shot_id=shot_id,
        asset_id="asset_a",
        fingerprint=fingerprint,
        recorded_at=recorded_at,
        recency_rank=rank,
    )


def _url_fingerprint(url: str, kind: str = "image") -> VisualFingerprint:
    return VisualFingerprint.from_dict({"kind": kind, "urls": [url]})


def _methods(assessment: RepetitionAssessment) -> set[str]:
    return {match.method for match in assessment.matches}


class VisualFingerprintTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_urls_normalize_host_port_fragment_tracking_and_query_order(self):
        original = (
            " HTTPS://EXAMPLE.COM.:443/photos/a%20b.jpg?z=2&UTM_Source=old"
            "&a=1&fbclid=tracking#preview "
        )
        canonical = "https://example.com/photos/a%20b.jpg?a=1&z=2"
        self.assertEqual(canonicalize_visual_url(original), canonical)
        first = _url_fingerprint(original)
        second = _url_fingerprint(canonical)
        self.assertEqual(first.url_hashes, second.url_hashes)
        self.assertEqual(
            first.url_hashes,
            (hashlib.sha256(canonical.encode("utf-8")).hexdigest(),),
        )
        self.assertEqual(
            _methods(assess_repetition(first, (_entry(second),))), {"url"}
        )

    def test_urls_preserve_resource_query_values_and_nondefault_port(self):
        self.assertEqual(
            canonicalize_visual_url("http://EXAMPLE.com:8080/?b=&a=2&a=1"),
            "http://example.com:8080/?a=1&a=2&b=",
        )
        prior = _url_fingerprint("https://example.com/photo?id=1")
        changed = _url_fingerprint("https://example.com/photo?id=2")
        self.assertFalse(assess_repetition(changed, (_entry(prior),)).is_repeated)

    def test_invalid_and_credential_bearing_urls_provide_no_identity(self):
        invalid = (
            "", "  ", "relative/photo.jpg", "file:///private/photo.jpg",
            "ftp://example.com/photo.jpg", "https://", "https://[broken",
            "https://example.com:invalid/photo.jpg", "https://example.com:99999/",
            "https://user:secret@example.com/photo.jpg",
        )
        for url in invalid:
            with self.subTest(url=url):
                self.assertEqual(canonicalize_visual_url(url), "")
                self.assertEqual(_url_fingerprint(url).url_hashes, ())

    def test_url_hashes_deduplicate_without_persisting_signed_url_values(self):
        source = self.root / "photo.png"
        _pattern().save(source)
        first = "https://example.com/photo.png?token=private-value&utm_source=one"
        second = "https://example.com/photo.png?utm_medium=two&token=private-value"
        fingerprint = build_visual_fingerprint(source, "image", urls=(first, second, ""))
        self.assertEqual(len(fingerprint.url_hashes), 1)
        serialized = json.dumps(fingerprint.as_dict())
        self.assertNotIn("private-value", serialized)
        self.assertNotIn("example.com", serialized)

    def test_identical_bytes_match_sha_despite_filename_and_url_changes(self):
        original = self.root / "original.png"
        renamed = self.root / "renamed.png"
        _pattern().save(original)
        renamed.write_bytes(original.read_bytes())
        first = build_visual_fingerprint(original, "image", ("https://one.example/a",))
        second = build_visual_fingerprint(renamed, "image", ("https://two.example/b",))
        self.assertEqual(first.sha256, hashlib.sha256(original.read_bytes()).hexdigest())
        assessment = assess_repetition(second, (_entry(first),))
        self.assertIn("sha256", _methods(assessment))
        self.assertNotIn("url", _methods(assessment))
        self.assertLess(assessment.penalty, 0)
        self.assertFalse(assessment.blocked)

    def test_resizing_and_jpeg_compression_match_perceptually_only(self):
        image = _pattern()
        original = self.root / "original.png"
        image.save(original)
        prior = build_visual_fingerprint(original, "image")
        for name, size, quality in (
            ("small.jpg", (160, 120), 38),
            ("large.jpg", (640, 480), 75),
        ):
            with self.subTest(name=name):
                candidate = self.root / name
                image.resize(size, Image.Resampling.LANCZOS).save(candidate, quality=quality)
                fingerprint = build_visual_fingerprint(candidate, "image")
                self.assertNotEqual(fingerprint.sha256, prior.sha256)
                assessment = assess_repetition(fingerprint, (_entry(prior),))
                self.assertEqual(_methods(assessment), {"image_phash"})
                self.assertGreaterEqual(assessment.matches[0].similarity, 0.8)

    def test_center_and_off_center_crops_match_the_source_photo(self):
        image = _pattern()
        source = self.root / "original.png"
        image.save(source)
        prior = build_visual_fingerprint(source, "image")
        crops = {
            "center90": (16, 12, 304, 228),
            "center80": (32, 24, 288, 216),
            "top_left80": (0, 0, 256, 192),
            "bottom_right80": (64, 48, 320, 240),
        }
        for name, box in crops.items():
            with self.subTest(crop=name):
                target = self.root / f"{name}.jpg"
                image.crop(box).save(target, quality=65)
                assessment = assess_repetition(
                    build_visual_fingerprint(target, "image"), (_entry(prior),)
                )
                self.assertEqual(_methods(assessment), {"image_phash"})

    def test_distinct_textured_images_do_not_match(self):
        first_path = self.root / "first.png"
        _pattern().save(first_path)
        first = build_visual_fingerprint(first_path, "image")
        for seed in (73, 107, 251):
            with self.subTest(seed=seed):
                target = self.root / f"different-{seed}.png"
                _pattern(seed).save(target)
                candidate = build_visual_fingerprint(target, "image")
                self.assertTrue(candidate.perceptual_hashes)
                assessment = assess_repetition(candidate, (_entry(first),))
                self.assertFalse(assessment.is_repeated)
                self.assertEqual(assessment.penalty, 0)

    def test_flat_images_use_exact_identity_without_false_perceptual_matches(self):
        fingerprints = []
        for colour in ("red", "blue", "white", "black"):
            target = self.root / f"{colour}.png"
            Image.new("RGB", (64, 64), colour).save(target)
            fingerprints.append(build_visual_fingerprint(target, "image"))
        for index, fingerprint in enumerate(fingerprints):
            self.assertEqual(fingerprint.perceptual_hashes, ())
            for other in fingerprints[index + 1:]:
                self.assertFalse(assess_repetition(fingerprint, (_entry(other),)).is_repeated)
        self.assertEqual(
            _methods(assess_repetition(fingerprints[0], (_entry(fingerprints[0]),))),
            {"sha256"},
        )

    def test_missing_empty_corrupt_and_invalid_image_inputs_fail_clearly(self):
        missing = self.root / "missing.png"
        empty = self.root / "empty.png"
        empty.touch()
        corrupt = self.root / "corrupt.png"
        corrupt.write_bytes(b"not an image")
        for source in (missing, empty, corrupt):
            with self.subTest(source=source.name), self.assertRaises(RuntimeError):
                build_visual_fingerprint(source, "image")
        valid = self.root / "valid.png"
        _pattern().save(valid)
        for kwargs in (
            {"kind": "audio"},
            {"kind": "image", "source_start_seconds": 1},
            {"kind": "image", "source_duration_seconds": 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError):
                build_visual_fingerprint(valid, **kwargs)

    def test_fingerprint_roundtrip_preserves_frame_order_and_repeated_frames(self):
        frames = ("0123456789abcdef", "0123456789abcdef", "fedcba9876543210")
        original = VisualFingerprint(
            kind="video", sha256="a" * 64, url_hashes=("b" * 64,),
            video_frame_hashes=frames, source_start_seconds=2.125,
            source_duration_seconds=3.75,
        )
        loaded = VisualFingerprint.from_dict(json.loads(json.dumps(original.as_dict())))
        self.assertEqual(loaded, original)
        self.assertEqual(loaded.video_frame_hashes, frames)

    def test_malformed_persisted_fingerprints_are_rejected(self):
        invalid = (
            None, [], {}, {"kind": "audio"},
            {"kind": "image", "sha256": "not-a-sha"},
            {"kind": "image", "url_hashes": "a" * 64},
            {"kind": "image", "perceptual_hashes": ["g" * 16]},
            {"kind": "video", "video_frame_hashes": ["0" * 15]},
            {"kind": "video", "source_start_seconds": -1},
            {"kind": "video", "source_start_seconds": True},
            {"kind": "video", "source_start_seconds": float("nan")},
            {"kind": "video", "source_duration_seconds": 0},
            {"kind": "video", "source_duration_seconds": float("inf")},
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                VisualFingerprint.from_dict(raw)


class VisualVideoRepetitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name)
        cls.source = cls.root / "four-scenes.mp4"
        arguments = ["-y", "-hide_banner", "-loglevel", "error"]
        for colour in ("red", "lime", "blue", "yellow"):
            arguments.extend([
                "-f", "lavfi", "-i", f"color=c={colour}:s=160x120:r=16:d=1",
            ])
        arguments.extend([
            "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]",
            "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-crf", "0", "-pix_fmt", "yuv420p", cls.source,
        ])
        run_ffmpeg(arguments)
        cls.transcoded = cls.root / "resized-reencoded.mkv"
        run_ffmpeg([
            "-y", "-hide_banner", "-loglevel", "error", "-i", cls.source,
            "-vf", "scale=96:72", "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "28", cls.transcoded,
        ])
        cls.reversed = cls.root / "reversed.mp4"
        run_ffmpeg([
            "-y", "-hide_banner", "-loglevel", "error", "-i", cls.source,
            "-vf", "reverse", "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-crf", "0", cls.reversed,
        ])
        cls.fingerprints: dict[tuple[Path, float, float], VisualFingerprint] = {}

    def _fingerprint(self, start: float, duration: float, path: Path | None = None):
        source = path or self.source
        key = (source, start, duration)
        if key not in self.fingerprints:
            self.fingerprints[key] = build_visual_fingerprint(
                source, "video", urls=(f"https://example.com/{source.name}",),
                source_start_seconds=start, source_duration_seconds=duration,
            )
        return self.fingerprints[key]

    def test_overlapping_windows_match_source_identity_and_explain_overlap(self):
        prior = self._fingerprint(0, 2)
        candidate = self._fingerprint(1, 2)
        assessment = assess_repetition(candidate, (_entry(prior),))
        self.assertTrue(assessment.is_repeated)
        self.assertEqual(_methods(assessment), {"url", "sha256"})
        for match in assessment.matches:
            self.assertEqual(match.similarity, 1.0)
        self.assertEqual(assessment.penalty, -MAX_REPETITION_PENALTY)
        self.assertFalse(assessment.blocked)

    def test_disjoint_source_windows_do_not_match_by_shared_url_or_bytes(self):
        prior = self._fingerprint(0, 0.75)
        candidate = self._fingerprint(2, 0.75)
        self.assertEqual(prior.sha256, candidate.sha256)
        self.assertEqual(prior.url_hashes, candidate.url_hashes)
        self.assertNotEqual(prior.video_frame_hashes, candidate.video_frame_hashes)
        assessment = assess_repetition(candidate, (_entry(prior),))
        self.assertTrue(assessment.is_repeated)
        self.assertEqual(_methods(assessment), {"url", "sha256"})
        self.assertTrue(all(match.similarity == 1.0 for match in assessment.matches))
        self.assertEqual(assessment.penalty, -MAX_REPETITION_PENALTY)

    def test_adjacent_windows_do_not_count_a_touching_boundary_as_overlap(self):
        prior = self._fingerprint(0, 1)
        candidate = self._fingerprint(1, 1)
        assessment = assess_repetition(candidate, (_entry(prior),))
        self.assertTrue(assessment.is_repeated)
        self.assertEqual(assessment.penalty, -MAX_REPETITION_PENALTY)

    def test_contained_trim_is_fully_repeated(self):
        prior = self._fingerprint(0, 2)
        candidate = self._fingerprint(0.25, 0.5)
        assessment = assess_repetition(candidate, (_entry(prior),))
        source_matches = [
            match for match in assessment.matches if match.method in {"url", "sha256"}
        ]
        self.assertEqual(len(source_matches), 2)
        self.assertTrue(all(match.similarity == 1 for match in source_matches))

    def test_reencoding_and_resizing_match_ordered_frames_despite_changed_identity(self):
        prior = self._fingerprint(0, 4)
        candidate = self._fingerprint(0, 4, self.transcoded)
        self.assertNotEqual(candidate.sha256, prior.sha256)
        self.assertNotEqual(candidate.url_hashes, prior.url_hashes)
        assessment = assess_repetition(candidate, (_entry(prior),))
        self.assertEqual(_methods(assessment), {"video_frames"})
        self.assertGreaterEqual(assessment.matches[0].similarity, 0.9)

    def test_frame_sequence_reversal_does_not_match_same_set_of_scenes(self):
        prior = self._fingerprint(0, 4)
        reversed_sequence = self._fingerprint(0, 4, self.reversed)
        self.assertEqual(set(prior.video_frame_hashes), set(reversed_sequence.video_frame_hashes))
        self.assertNotEqual(prior.video_frame_hashes, reversed_sequence.video_frame_hashes)
        self.assertFalse(
            assess_repetition(reversed_sequence, (_entry(prior),)).is_repeated
        )

    def test_sampling_keeps_repeated_frames_and_stays_inside_requested_trim(self):
        red = self._fingerprint(0, 0.75)
        blue = self._fingerprint(2, 0.75)
        self.assertEqual(len(red.video_frame_hashes), 8)
        self.assertEqual(len(blue.video_frame_hashes), 8)
        self.assertEqual(len(set(red.video_frame_hashes)), 1)
        self.assertEqual(len(set(blue.video_frame_hashes)), 1)
        self.assertNotEqual(red.video_frame_hashes[0], blue.video_frame_hashes[0])
        restored = VisualFingerprint.from_dict(red.as_dict())
        self.assertEqual(restored.video_frame_hashes, red.video_frame_hashes)
        self.assertIn("video_frames", _methods(assess_repetition(red, (_entry(restored),))))

    def test_unspecified_duration_uses_only_remaining_video(self):
        candidate = build_visual_fingerprint(self.source, "video", source_start_seconds=2)
        self.assertEqual(candidate.source_start_seconds, 2)
        self.assertAlmostEqual(candidate.source_duration_seconds, 2)
        self.assertEqual(candidate.video_frame_hashes, self._fingerprint(2, 2).video_frame_hashes)

    def test_out_of_bounds_video_trim_is_rejected(self):
        for start, duration in ((4, 0.5), (5, None), (3.5, 1), (0, 0)):
            with self.subTest(start=start, duration=duration), self.assertRaises(RuntimeError):
                build_visual_fingerprint(
                    self.source, "video", source_start_seconds=start,
                    source_duration_seconds=duration,
                )


class VisualRepetitionAssessmentTests(unittest.TestCase):
    def test_recency_reduces_but_does_not_erase_repetition_penalty(self):
        fingerprint = _url_fingerprint("https://example.com/photo.jpg")
        assessments = [
            assess_repetition(fingerprint, (_entry(fingerprint, rank=rank),))
            for rank in (0, 5, 12, 100)
        ]
        penalties = [assessment.penalty for assessment in assessments]
        self.assertEqual(penalties, sorted(penalties))
        self.assertLess(penalties[0], penalties[1])
        self.assertTrue(all(value < 0 for value in penalties))
        self.assertTrue(all(not assessment.blocked for assessment in assessments))

    def test_multiple_detection_methods_for_one_usage_do_not_stack_penalties(self):
        fingerprint = VisualFingerprint(
            kind="image", sha256="a" * 64, url_hashes=("b" * 64,),
            perceptual_hashes=("a5" * 8,),
        )
        assessment = assess_repetition(fingerprint, (_entry(fingerprint),))
        self.assertEqual(_methods(assessment), {"url", "sha256", "image_phash"})
        self.assertEqual(assessment.penalty, min(match.penalty for match in assessment.matches))
        duplicate = assess_repetition(fingerprint, (_entry(fingerprint), _entry(fingerprint)))
        self.assertEqual(duplicate.penalty, assessment.penalty)

    def test_multiple_prior_usages_have_bounded_penalty_and_never_block(self):
        fingerprint = _url_fingerprint("https://example.com/photo.jpg")
        history = tuple(_entry(fingerprint, episode=f"episode_{index}") for index in range(20))
        assessment = assess_repetition(fingerprint, history)
        once = assess_repetition(fingerprint, history[:1])
        self.assertLess(assessment.penalty, once.penalty)
        self.assertEqual(assessment.penalty, -MAX_REPETITION_PENALTY)
        self.assertFalse(assessment.blocked)
        self.assertEqual(assessment.adjust_score(10), 0)
        self.assertEqual(assessment.adjust_score(250), 100)

    def test_matching_identity_across_different_media_kinds_is_not_repetition(self):
        image = VisualFingerprint(kind="image", sha256="a" * 64, url_hashes=("b" * 64,))
        video = replace(image, kind="video", source_duration_seconds=1)
        self.assertFalse(assess_repetition(image, (_entry(video),)).is_repeated)

    def test_unknown_legacy_video_duration_provides_weak_source_evidence(self):
        url = "https://example.com/video.mp4"
        legacy = _url_fingerprint(url, "video")
        candidate = replace(legacy, source_start_seconds=10, source_duration_seconds=2)
        weak = assess_repetition(candidate, (_entry(legacy),))
        exact = assess_repetition(candidate, (_entry(candidate),))
        self.assertTrue(weak.is_repeated)
        self.assertEqual(weak.penalty, -MAX_REPETITION_PENALTY)
        self.assertEqual(exact.penalty, -MAX_REPETITION_PENALTY)
        self.assertTrue(all(match.similarity == 1.0 for match in weak.matches))

    def test_empty_or_invalid_history_and_candidate_degrade_without_blocking(self):
        fingerprint = _url_fingerprint("https://example.com/photo.jpg")
        self.assertEqual(assess_repetition(fingerprint, ()).penalty, 0)
        with_invalid = assess_repetition(fingerprint, (None, {}, _entry(fingerprint)))
        self.assertTrue(with_invalid.is_repeated)
        self.assertEqual(len(with_invalid.warnings), 1)
        invalid_candidate = assess_repetition(None, (_entry(fingerprint),))
        self.assertFalse(invalid_candidate.is_repeated)
        self.assertTrue(invalid_candidate.warnings)
        self.assertFalse(invalid_candidate.blocked)

    def test_assessment_roundtrip_keeps_explanation_and_cannot_restore_a_hard_gate(self):
        fingerprint = _url_fingerprint("https://example.com/photo.jpg")
        original = assess_repetition(fingerprint, (_entry(fingerprint, rank=3),))
        report = original.as_dict()
        self.assertTrue(report["repetition_detected"])
        self.assertEqual(report["reason_codes"], ["url"])
        self.assertEqual(report["matches"][0]["episode"], "prior_episode")
        report["blocked"] = True
        restored = RepetitionAssessment.from_dict(json.loads(json.dumps(report)))
        self.assertFalse(restored.blocked)
        self.assertEqual(restored.penalty, original.penalty)
        self.assertEqual(restored.matches, original.matches)


class VisualHistoryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        git_dates = patch.object(repetition, "_episode_creation_dates", return_value={})
        self.creation_dates = git_dates.start()
        self.addCleanup(git_dates.stop)
        network = patch("socket.create_connection", side_effect=AssertionError("network forbidden"))
        self.network = network.start()
        self.addCleanup(network.stop)

    def _report(
        self, episode: str, entries: list[dict], *,
        recorded_at: str | None = None,
        name: str = "visual_resolution_report.json",
    ) -> Path:
        episode_dir = self.root / "episodes" / episode
        episode_dir.mkdir(parents=True, exist_ok=True)
        report = {"schema_version": 1, "recorded_at": recorded_at, "visual_usage": entries}
        path = episode_dir / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def _legacy(self, episode: str, assets: list[dict], shots: list[dict]) -> Path:
        episode_dir = self.root / "episodes" / episode
        episode_dir.mkdir(parents=True, exist_ok=True)
        (episode_dir / "assets.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")
        (episode_dir / "timeline.json").write_text(json.dumps({"shots": shots}), encoding="utf-8")
        return episode_dir

    def test_missing_episodes_directory_is_empty_history(self):
        history, warnings = load_visual_history(self.root)
        self.assertEqual(history, ())
        self.assertEqual(warnings, ())

    def test_selected_usage_roundtrip_persists_all_evidence_and_attribution(self):
        fingerprint = VisualFingerprint(
            kind="video", sha256="a" * 64, url_hashes=("b" * 64,),
            video_frame_hashes=("1234567890abcdef",) * 8,
            source_start_seconds=12.5, source_duration_seconds=3.25,
        )
        usage = fingerprint_to_usage(
            fingerprint, "published", "shot_selected", "selected_asset",
            recorded_at="2026-09-08T12:00:00+00:00",
        )
        self._report("published", [usage])
        first, warnings = load_visual_history(self.root)
        second, repeated_warnings = load_visual_history(self.root)
        self.assertEqual(first, second)
        self.assertEqual(warnings, ())
        self.assertEqual(repeated_warnings, ())
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].fingerprint, fingerprint)
        self.assertEqual(first[0].shot_id, "shot_selected")
        self.assertEqual(first[0].asset_id, "selected_asset")
        self.assertEqual(first[0].recency_rank, 0)
        self.assertTrue(assess_repetition(fingerprint, second).is_repeated)
        self.network.assert_not_called()

    def test_recency_uses_recorded_time_and_excludes_self_before_limiting(self):
        fingerprint = _url_fingerprint("https://example.com/photo.jpg")
        for episode, date in (
            ("z_old", "2026-09-01T00:00:00Z"),
            ("a_new", "2026-09-06T00:00:00Z"),
            ("current", "2026-09-08T00:00:00Z"),
        ):
            usage = _entry(fingerprint, episode=episode, recorded_at=date).as_dict()
            self._report(episode, [usage], recorded_at=date)
        history, warnings = load_visual_history(self.root, exclude_episode="current", recent_episode_limit=2)
        self.assertEqual(warnings, ())
        self.assertEqual([entry.episode for entry in history], ["a_new", "z_old"])
        self.assertEqual([entry.recency_rank for entry in history], [0, 1])
        newest, _ = load_visual_history(self.root, exclude_episode="current", recent_episode_limit=1)
        self.assertEqual([entry.episode for entry in newest], ["a_new"])

    def test_window_counts_episodes_and_keeps_all_shots_in_each_selected_episode(self):
        fingerprint = _url_fingerprint("https://example.com/photo.jpg")
        self._report("older", [_entry(fingerprint, episode="older").as_dict()], recorded_at="2026-09-01")
        self._report("newer", [
            _entry(fingerprint, episode="newer", shot_id=f"shot_{index}").as_dict()
            for index in range(3)
        ], recorded_at="2026-09-07")
        history, _ = load_visual_history(self.root, recent_episode_limit=1)
        self.assertEqual(len(history), 3)
        self.assertEqual({entry.episode for entry in history}, {"newer"})
        self.assertEqual({entry.recency_rank for entry in history}, {0})

    def test_usage_report_precedes_resolution_report_and_adds_missing_legacy_shots(self):
        current = _url_fingerprint("https://example.com/rendered.jpg")
        obsolete = _url_fingerprint("https://example.com/old-selection.jpg")
        unresolved = _url_fingerprint("https://example.com/resolution-only.jpg")
        self._report("episode", [_entry(current, episode="episode").as_dict()], name="visual_usage.json")
        self._report("episode", [
            _entry(obsolete, episode="episode").as_dict(),
            _entry(unresolved, episode="episode", shot_id="shot_c").as_dict(),
        ])
        self._legacy("episode", [
            {"id": "asset_a", "file": "old.jpg", "url": "https://example.com/old.jpg"},
            {"id": "asset_b", "file": "extra.jpg", "url": "https://example.com/extra.jpg"},
        ], [
            {"id": "shot_a", "asset": "asset_a"},
            {"id": "shot_b", "asset": "asset_b"},
        ])
        history, warnings = load_visual_history(self.root)
        self.assertEqual(warnings, ())
        self.assertEqual(len(history), 3)
        self.assertEqual(next(entry for entry in history if entry.shot_id == "shot_a").fingerprint, current)
        self.assertFalse(assess_repetition(obsolete, history).is_repeated)
        self.assertTrue(assess_repetition(unresolved, history).is_repeated)
        self.assertTrue(assess_repetition(_url_fingerprint("https://example.com/extra.jpg"), history).is_repeated)

    def test_corrupt_primary_report_falls_back_to_valid_resolution_report(self):
        fingerprint = _url_fingerprint("https://example.com/photo.jpg")
        self._report("episode", [_entry(fingerprint, episode="episode").as_dict()])
        primary = self.root / "episodes" / "episode" / "visual_usage.json"
        primary.write_text("{broken", encoding="utf-8")
        history, warnings = load_visual_history(self.root)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].fingerprint, fingerprint)
        self.assertTrue(any("corrompido" in warning for warning in warnings))

    def test_invalid_usage_entries_are_skipped_but_valid_entries_survive(self):
        fingerprint = _url_fingerprint("https://example.com/photo.jpg")
        good = _entry(fingerprint, episode="incorrect-embedded-name").as_dict()
        invalid_hash = dict(good, fingerprint={"kind": "image", "sha256": "bad"})
        invalid_date = dict(good, recorded_at="not-a-date")
        invalid_identity = dict(good, shot_id="")
        self._report("actual-folder", [None, invalid_hash, invalid_date, invalid_identity, good])
        history, warnings = load_visual_history(self.root)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].episode, "actual-folder")
        self.assertEqual(history[0].fingerprint, fingerprint)
        self.assertEqual(len(warnings), 4)

    def test_corrupt_report_and_invalid_usage_shape_fall_back_to_legacy_offline(self):
        for episode, contents in (("corrupt", "{broken"), ("wrong_shape", '{"visual_usage": {}}')):
            episode_dir = self._legacy(episode, [
                {"id": "selected", "file": "missing.jpg", "url": "https://example.com/legacy.jpg"},
            ], [{"id": "selected_shot", "asset": "selected"}])
            (episode_dir / "visual_resolution_report.json").write_text(contents, encoding="utf-8")
        history, warnings = load_visual_history(self.root)
        self.assertEqual(len(history), 2)
        self.assertTrue(warnings)
        self.assertTrue(all(entry.fingerprint.sha256 is None for entry in history))
        self.assertTrue(assess_repetition(_url_fingerprint("https://example.com/legacy.jpg"), history).is_repeated)
        self.network.assert_not_called()

    def test_legacy_only_includes_timeline_assets_and_does_not_invent_consumed_duration(self):
        self._legacy("legacy", [
            {"id": "used", "file": "used.mp4", "url": "https://example.com/used.mp4"},
            {"id": "unused", "file": "unused.jpg", "url": "https://example.com/unused.jpg"},
            {"id": "audio", "file": "audio.mp3", "url": "https://example.com/audio.mp3"},
        ], [
            {"id": "video_shot", "asset": "used", "source_start_seconds": 3, "source_end_seconds": 90},
            {"id": "audio_shot", "asset": "audio"},
            {"id": "absent", "asset": "does_not_exist"},
        ])
        history, warnings = load_visual_history(self.root)
        self.assertEqual(warnings, ())
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].shot_id, "video_shot")
        self.assertEqual(history[0].fingerprint.source_start_seconds, 3)
        self.assertIsNone(history[0].fingerprint.source_duration_seconds)
        self.assertEqual(history[0].fingerprint.video_frame_hashes, ())
        self.network.assert_not_called()

    def test_legacy_local_images_are_hydrated_only_inside_recent_episode_window(self):
        for episode in ("a_older", "z_newer"):
            episode_dir = self._legacy(episode, [{"id": "local", "file": "local.png"}], [
                {"id": "shot_local", "asset": "local"},
            ])
            (episode_dir / "assets").mkdir()
            _pattern().save(episode_dir / "assets" / "local.png")
        with patch.object(repetition, "_file_sha256", wraps=repetition._file_sha256) as hashing:
            history, warnings = load_visual_history(self.root, recent_episode_limit=1)
        self.assertEqual(warnings, ())
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].episode, "z_newer")
        self.assertTrue(history[0].fingerprint.sha256)
        self.assertTrue(history[0].fingerprint.perceptual_hashes)
        self.assertEqual(hashing.call_count, 1)
        self.assertIn("z_newer", hashing.call_args.args[0].parts)
        self.network.assert_not_called()

    def test_legacy_creation_dates_override_checkout_mtime_and_slug_order(self):
        for episode in ("a_recent", "z_old"):
            episode_dir = self._legacy(episode, [{
                "id": "asset", "file": "missing.jpg", "url": "https://example.com/photo.jpg",
            }], [{"id": "shot", "asset": "asset"}])
            timestamp = 1 if episode == "a_recent" else 2_000_000_000
            os.utime(episode_dir, (timestamp, timestamp))
        self.creation_dates.return_value = {
            "a_recent": datetime(2026, 9, 7, tzinfo=timezone.utc),
            "z_old": datetime(2026, 8, 1, tzinfo=timezone.utc),
        }
        history, warnings = load_visual_history(self.root)
        self.assertEqual(warnings, ())
        self.assertEqual([entry.episode for entry in history], ["a_recent", "z_old"])

    def test_legacy_path_traversal_is_ignored_without_reading_outside_assets(self):
        self._legacy("legacy", [{
            "id": "escaped", "file": "../outside.png", "url": "https://example.com/photo.jpg",
        }], [{"id": "shot", "asset": "escaped"}])
        with patch.object(repetition, "_file_sha256") as hashing:
            history, warnings = load_visual_history(self.root)
        self.assertEqual(history, ())
        self.assertTrue(any("Caminho visual invalido" in warning for warning in warnings))
        hashing.assert_not_called()

    def test_corrupt_legacy_manifests_do_not_abort_other_episodes(self):
        fingerprint = _url_fingerprint("https://example.com/good.jpg")
        self._report("good", [_entry(fingerprint, episode="good").as_dict()])
        broken = self._legacy("broken", [], [])
        (broken / "assets.json").write_text("[]", encoding="utf-8")
        history, warnings = load_visual_history(self.root)
        self.assertEqual([entry.episode for entry in history], ["good"])
        self.assertTrue(any("Manifestos visuais invalidos" in warning for warning in warnings))

    def test_invalid_episode_limit_warns_and_uses_default(self):
        for limit in (0, -1, True, 1.5, "2"):
            with self.subTest(limit=limit):
                history, warnings = load_visual_history(self.root, recent_episode_limit=limit)
                self.assertEqual(history, ())
                self.assertEqual(len(warnings), 1)
                self.assertIn("recent_episode_limit invalido", warnings[0])


if __name__ == "__main__":
    unittest.main()
