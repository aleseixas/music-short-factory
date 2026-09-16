from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from scripts import publish_ready_bundle as bundle


EPISODE = "episodio_teste"
RUN_ID = "123456"
SOURCE_SHA = "a" * 40


def _probe_payload(
    *,
    width: int = 18,
    height: int = 32,
    fps: str = "30/1",
    duration: str = "12.500000",
) -> dict:
    return {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": duration,
        },
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": width,
                "height": height,
                "avg_frame_rate": fps,
                "r_frame_rate": fps,
                "duration": duration,
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "duration": duration,
            },
        ],
    }


def _media_info() -> dict:
    return {
        "container": "mp4",
        "duration_seconds": 12.5,
        "video": {
            "codec": "h264",
            "pixel_format": "yuv420p",
            "width": 18,
            "height": 32,
            "fps": 30.0,
            "frame_rate": "30",
            "duration_seconds": 12.5,
        },
        "audio": {
            "codec": "aac",
            "sample_rate": 48000,
            "channels": 2,
            "duration_seconds": 12.5,
        },
    }


class PublishReadyBundleTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / "episodes" / EPISODE).mkdir(parents=True)
        (self.root / "output").mkdir()
        (self.root / "config" / "config.json").write_text(
            json.dumps(
                {
                    "paths": {"episodes_dir": "episodes", "output_dir": "output"},
                    "render": {"width": 18, "height": 32, "fps": 30},
                    "mix": {"sample_rate": 48000},
                }
            ),
            encoding="utf-8",
        )
        self.video_bytes = b"fake-mp4-bytes-for-bundle"
        (self.root / "output" / f"{EPISODE}.mp4").write_bytes(self.video_bytes)
        Image.new("RGB", (18, 32), "navy").save(
            self.root / "output" / f"{EPISODE}_cover.jpg", format="JPEG"
        )
        self.post_bytes = b'{"schema_version":1,"youtube":{"title":"Teste"}}\n'
        (self.root / "episodes" / EPISODE / "post.json").write_bytes(self.post_bytes)

    def tearDown(self):
        self.temporary.cleanup()

    def create(self, *, include_usage: bool = False) -> Path:
        if include_usage:
            (self.root / "episodes" / EPISODE / "visual_usage.json").write_text(
                json.dumps({"schema_version": 1, "episode": EPISODE, "visual_usage": []}),
                encoding="utf-8",
            )
        with mock.patch.object(bundle, "_validate_video", return_value=_media_info()):
            return bundle.create_bundle(
                self.root,
                EPISODE,
                Path(".publish-ready"),
                source_run_id=RUN_ID,
                source_sha=SOURCE_SHA,
            )


class CreateBundleTests(PublishReadyBundleTestCase):
    def test_create_rejects_slug_outside_episode_contract(self):
        for invalid in ("Episode", "episode-", "episode__name", "-episode"):
            with self.subTest(invalid=invalid), self.assertRaises(bundle.BundleError):
                bundle.create_bundle(
                    self.root,
                    invalid,
                    Path("bundle"),
                    source_run_id=RUN_ID,
                    source_sha=SOURCE_SHA,
                )

    def test_create_packs_validated_files_and_complete_manifest(self):
        target = self.create(include_usage=True)

        expected = {
            "manifest.json",
            f"output/{EPISODE}.mp4",
            f"output/{EPISODE}_cover.jpg",
            f"episodes/{EPISODE}/post.json",
            f"episodes/{EPISODE}/visual_usage.json",
        }
        actual = {
            path.relative_to(target).as_posix()
            for path in target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, expected)

        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["episode"], EPISODE)
        self.assertEqual(manifest["source"], {"run_id": RUN_ID, "sha": SOURCE_SHA})
        self.assertEqual(manifest["media"]["video"]["codec"], "h264")
        self.assertEqual(manifest["media"]["cover"]["format"], "jpeg")
        by_role = {entry["role"]: entry for entry in manifest["files"]}
        self.assertEqual(set(by_role), {"video", "cover", "post", "visual_usage"})
        for entry in by_role.values():
            payload = (target / entry["path"]).read_bytes()
            self.assertEqual(entry["size_bytes"], len(payload))
            self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())

    def test_create_refuses_existing_bundle_instead_of_overwriting_it(self):
        target = self.root / ".publish-ready"
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaisesRegex(bundle.BundleError, "ja existe"):
            self.create()

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_create_requires_github_source_identity(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(bundle.BundleError, "source run id"):
                bundle.create_bundle(self.root, EPISODE, Path("bundle"))

    def test_create_accepts_explicit_preflight_environment_identity(self):
        with mock.patch.dict(
            os.environ,
            {"PREFLIGHT_SOURCE_RUN_ID": RUN_ID, "PREFLIGHT_SOURCE_SHA": SOURCE_SHA},
            clear=True,
        ), mock.patch.object(bundle, "_validate_video", return_value=_media_info()):
            target = bundle.create_bundle(self.root, EPISODE, Path("bundle"))

        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"], {"run_id": RUN_ID, "sha": SOURCE_SHA})

    def test_create_rejects_visual_usage_from_another_episode(self):
        (self.root / "episodes" / EPISODE / "visual_usage.json").write_text(
            json.dumps({"episode": "outro_episodio"}), encoding="utf-8"
        )
        with self.assertRaisesRegex(bundle.BundleError, "pertence"):
            self.create()


class VideoValidationTests(PublishReadyBundleTestCase):
    def settings(self) -> bundle.ProjectSettings:
        return bundle._load_settings(self.root)

    @mock.patch.object(bundle, "_full_decode")
    @mock.patch.object(bundle, "_find_binary", side_effect=["ffprobe", "ffmpeg"])
    @mock.patch.object(bundle, "_run_ffprobe", return_value=_probe_payload())
    def test_video_validation_checks_probe_and_full_decode(
        self, probe, find_binary, full_decode
    ):
        path = self.root / "output" / f"{EPISODE}.mp4"
        result = bundle._validate_video(path, self.settings())

        self.assertEqual(result["container"], "mp4")
        self.assertEqual(result["video"]["pixel_format"], "yuv420p")
        self.assertEqual(result["audio"]["sample_rate"], 48000)
        probe.assert_called_once_with(path, "ffprobe")
        full_decode.assert_called_once_with(path, "ffmpeg")

    def test_video_validation_rejects_incompatible_probe_metadata(self):
        mutations = {
            "container": lambda value: value["format"].update(format_name="matroska,webm"),
            "video codec": lambda value: value["streams"][0].update(codec_name="hevc"),
            "pixel format": lambda value: value["streams"][0].update(pix_fmt="yuv444p"),
            "resolution": lambda value: value["streams"][0].update(width=20),
            "fps": lambda value: value["streams"][0].update(avg_frame_rate="25/1"),
            "audio codec": lambda value: value["streams"][1].update(codec_name="mp3"),
            "sample rate": lambda value: value["streams"][1].update(sample_rate="44100"),
            "duration": lambda value: value["format"].update(duration="0"),
            "platform duration": lambda value: (
                value["format"].update(duration="2.5"),
                value["streams"][0].update(duration="2.5"),
                value["streams"][1].update(duration="2.5"),
            ),
            "platform bitrate": lambda value: value["format"].update(
                bit_rate="26000000"
            ),
            "extra stream": lambda value: value["streams"].append(
                {"codec_type": "subtitle", "codec_name": "mov_text"}
            ),
        }
        path = self.root / "output" / f"{EPISODE}.mp4"
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                payload = copy.deepcopy(_probe_payload())
                mutate(payload)
                with mock.patch.object(bundle, "_find_binary", side_effect=["ffprobe", "ffmpeg"]), mock.patch.object(
                    bundle, "_run_ffprobe", return_value=payload
                ), mock.patch.object(bundle, "_full_decode") as decode:
                    with self.assertRaises(bundle.BundleError):
                        bundle._validate_video(path, self.settings())
                    decode.assert_not_called()

    @mock.patch.object(bundle.subprocess, "run")
    def test_full_decode_uses_both_streams_and_xerror(self, run):
        run.return_value = mock.Mock(returncode=0, stderr="")
        path = self.root / "output" / f"{EPISODE}.mp4"

        bundle._full_decode(path, "ffmpeg")

        command = run.call_args.args[0]
        self.assertIn("-xerror", command)
        self.assertIn("0:v:0", command)
        self.assertIn("0:a:0", command)
        self.assertEqual(command[-3:], ["-f", "null", "-"])

    def test_cover_validation_rejects_wrong_dimensions_and_size(self):
        cover = self.root / "output" / f"{EPISODE}_cover.jpg"
        Image.new("RGB", (9, 16), "black").save(cover, format="JPEG")
        with self.assertRaisesRegex(bundle.BundleError, "Resolucao da capa"):
            bundle._validate_cover(cover, self.settings())

        Image.new("RGB", (18, 32), "black").save(cover, format="JPEG")
        with mock.patch.object(bundle, "MAX_COVER_BYTES", 1):
            with self.assertRaisesRegex(bundle.BundleError, "2 MiB"):
                bundle._validate_cover(cover, self.settings())


class RestoreBundleTests(PublishReadyBundleTestCase):
    def test_restore_verifies_bundle_then_copies_exact_bytes(self):
        target = self.create(include_usage=True)
        expected = {
            entry["role"]: (target / entry["path"]).read_bytes()
            for entry in json.loads((target / "manifest.json").read_text(encoding="utf-8"))[
                "files"
            ]
        }
        (self.root / "output" / f"{EPISODE}.mp4").write_bytes(b"changed")
        (self.root / "output" / f"{EPISODE}_cover.jpg").write_bytes(b"changed")
        (self.root / "episodes" / EPISODE / "post.json").write_bytes(b"changed")
        (self.root / "episodes" / EPISODE / "visual_usage.json").write_bytes(b"changed")

        restored = bundle.restore_bundle(
            self.root, EPISODE, target, source_run_id=RUN_ID
        )

        self.assertEqual(len(restored), 4)
        self.assertEqual(
            (self.root / "output" / f"{EPISODE}.mp4").read_bytes(), expected["video"]
        )
        self.assertEqual(
            (self.root / "output" / f"{EPISODE}_cover.jpg").read_bytes(), expected["cover"]
        )
        self.assertEqual(
            (self.root / "episodes" / EPISODE / "post.json").read_bytes(), expected["post"]
        )
        self.assertEqual(
            (self.root / "episodes" / EPISODE / "visual_usage.json").read_bytes(),
            expected["visual_usage"],
        )

    def test_restore_rejects_wrong_episode_or_source_run(self):
        target = self.create()
        with self.assertRaisesRegex(bundle.BundleError, "source run"):
            bundle.restore_bundle(self.root, EPISODE, target, source_run_id="999")
        with self.assertRaisesRegex(bundle.BundleError, "pertence ao episodio"):
            bundle.restore_bundle(self.root, "outro", target, source_run_id=RUN_ID)

    def test_restore_rejects_changed_missing_or_extra_file(self):
        cases = ("changed", "missing", "extra")
        for case in cases:
            with self.subTest(case=case):
                case_root = self.root / f"case-{case}"
                shutil_target = case_root / ".publish-ready"
                case_root.mkdir()
                source = self.create()
                shutil.copytree(source, shutil_target)
                source.rename(self.root / f"saved-{case}")
                video = shutil_target / "output" / f"{EPISODE}.mp4"
                if case == "changed":
                    video.write_bytes(b"tampered")
                elif case == "missing":
                    video.unlink()
                else:
                    (shutil_target / "unexpected.txt").write_text("extra", encoding="utf-8")
                with self.assertRaises(bundle.BundleError):
                    bundle.restore_bundle(self.root, EPISODE, shutil_target, source_run_id=RUN_ID)

    def test_restore_rejects_manifest_path_traversal(self):
        target = self.create()
        manifest_path = target / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../escape.mp4"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(bundle.BundleError, "Caminho inseguro"):
            bundle.restore_bundle(self.root, EPISODE, target, source_run_id=RUN_ID)

    def test_restore_rejects_symlinked_file_when_supported(self):
        target = self.create()
        video = target / "output" / f"{EPISODE}.mp4"
        real = target / "output" / "real.mp4"
        video.rename(real)
        try:
            video.symlink_to(real.name)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink indisponivel neste ambiente: {exc}")

        with self.assertRaisesRegex(bundle.BundleError, "simbolico"):
            bundle.restore_bundle(self.root, EPISODE, target, source_run_id=RUN_ID)


if __name__ == "__main__":
    unittest.main()
