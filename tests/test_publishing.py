from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import requests

from publish import create_publisher, main as publish_main
from publishing.base import ApiError, PublishContext, PublishingError
from publishing.cover import generate_cover
from publishing.credentials import CredentialStore
from publishing.instagram import CloudinaryVideoHost, InstagramPublisher
from publishing.metadata import (
    create_post_template,
    load_post,
    normalize_post,
    prepare_episode_post,
    validate_post,
)
from publishing.tiktok import TikTokPublisher
from publishing.youtube import YouTubePublisher


def valid_post() -> dict[str, object]:
    return {
        "schema_version": 1,
        "cover": {
            "headline": "ISSO QUASE MUDOU TUDO",
            "source": {"type": "asset", "asset_id": "main_image"},
        },
        "youtube": {
            "title": "A COINCIDENCIA por tras da musica",
            "description": "Uma historia curta e sustentada pelo video.",
            "hashtags": ["Musica", "Shorts"],
            "privacy_status": "private",
            "category_id": "10",
        },
        "instagram": {
            "caption": "Uma historia curta e sustentada pelo video.",
            "hashtags": ["Musica", "Reels"],
            "share_to_feed": True,
            "thumb_offset_ms": 1000,
        },
        "tiktok": {
            "caption": "Uma historia curta e sustentada pelo video.",
            "hashtags": ["Musica", "TikTok"],
            "privacy_level": "SELF_ONLY",
            "video_cover_timestamp_ms": 1000,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "is_aigc": False,
        },
    }


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_project_config(root: Path) -> None:
    write_json(
        root / "config" / "config.json",
        {
            "paths": {"episodes_dir": "episodes", "output_dir": "output"},
            "render": {"width": 720, "height": 1280},
        },
    )
    write_json(root / "config" / "style.json", {"highlights": {}})


def write_episode_sources(root: Path, slug: str = "demo") -> Path:
    episode_dir = root / "episodes" / slug
    assets_dir = episode_dir / "assets"
    assets_dir.mkdir(parents=True)
    write_json(
        episode_dir / "story.json",
        {
            "schema_version": 1,
            "title": "Demo Artist — Demo Song: uma historia improvavel",
            "slug": slug,
            "target_duration_seconds": 75,
            "segments": [
                {
                    "id": "hook",
                    "text": "Uma escolha improvavel mudou o destino dessa musica.",
                }
            ],
        },
    )
    write_json(
        episode_dir / "timeline.json",
        {
            "schema_version": 1,
            "shots": [
                {
                    "id": "shot_hook",
                    "segment": "hook",
                    "asset": "main_image",
                    "motion": "push_in",
                    "transition_out": "cut",
                }
            ],
        },
    )
    write_json(
        episode_dir / "assets.json",
        {
            "schema_version": 1,
            "assets": [
                {
                    "id": "main_image",
                    "file": "main_image.jpg",
                    "url": "",
                    "credit": "Fixture",
                    "license": "CC0",
                    "focus": {"x": 0.4, "y": 0.55},
                }
            ],
        },
    )
    Image.new("RGB", (1000, 700), (25, 90, 155)).save(
        assets_dir / "main_image.jpg",
        format="JPEG",
    )
    return episode_dir


def write_publishable_files(root: Path, slug: str = "demo") -> None:
    write_project_config(root)
    episode_dir = root / "episodes" / slug
    episode_dir.mkdir(parents=True)
    write_json(episode_dir / "post.json", valid_post())
    output_dir = root / "output"
    output_dir.mkdir()
    (output_dir / f"{slug}.mp4").write_bytes(b"not decoded during dry-run")
    Image.new("RGB", (720, 1280), "navy").save(
        output_dir / f"{slug}_cover.jpg",
        format="JPEG",
    )


def local_context(root: Path, platform: str) -> PublishContext:
    video = root / "demo.mp4"
    cover = root / "demo_cover.jpg"
    video.write_bytes(b"video fixture")
    Image.new("RGB", (32, 48), "black").save(cover, format="JPEG")
    post = valid_post()
    return PublishContext(
        episode="demo",
        video_path=video,
        cover_path=cover,
        metadata=deepcopy(post[platform]),
    )


class FailingSession:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("dry-run tentou acessar a rede")


class TimeoutSession:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        raise requests.Timeout("request details must not leak")


class StaticSession:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0

    def request(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return self.response


class JsonResponse:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.reason = "OK"

    def json(self) -> dict[str, object]:
        return self.payload


class QueueSession:
    def __init__(self, responses: list[JsonResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, url: str, **kwargs: object) -> JsonResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"requisicao inesperada: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingVideoHost:
    def __init__(
        self,
        public_url: str = "https://cdn.example.test/video.mp4",
        *,
        fail_upload: bool = False,
        fail_cleanup: bool = False,
    ) -> None:
        self.public_url = public_url
        self.fail_upload = fail_upload
        self.fail_cleanup = fail_cleanup
        self.upload_calls: list[Path] = []
        self.cleanup_calls = 0

    def upload(self, local_file: Path) -> str:
        self.upload_calls.append(local_file)
        if self.fail_upload:
            raise RuntimeError("storage details must not leak")
        return self.public_url

    def cleanup(self) -> None:
        self.cleanup_calls += 1
        if self.fail_cleanup:
            raise RuntimeError("storage details must not leak")


class FakeCloudinaryUploader:
    def __init__(
        self,
        secure_url: str = "https://res.cloudinary.com/demo/video/upload/reel.mp4",
        *,
        upload_error: Exception | None = None,
        destroy_error: Exception | None = None,
        destroy_result: str = "ok",
        resource_pages: list[dict[str, object]] | None = None,
        resources_error: Exception | None = None,
        destroy_errors_by_public_id: dict[str, Exception] | None = None,
    ) -> None:
        self.secure_url = secure_url
        self.upload_error = upload_error
        self.destroy_error = destroy_error
        self.destroy_result = destroy_result
        self.resource_pages = list(resource_pages or [{"resources": []}])
        self.resources_error = resources_error
        self.destroy_errors_by_public_id = destroy_errors_by_public_id or {}
        self.upload_calls: list[tuple[str, str, dict[str, object]]] = []
        self.destroy_calls: list[tuple[str, dict[str, object]]] = []
        self.resources_calls: list[dict[str, object]] = []

    def resources(self, **options: object) -> dict[str, object]:
        self.resources_calls.append(options)
        if self.resources_error is not None:
            raise self.resources_error
        if self.resource_pages:
            return self.resource_pages.pop(0)
        return {"resources": []}

    def upload(self, file: str, **options: object) -> dict[str, object]:
        return self._upload("upload", file, options)

    def upload_large(self, file: str, **options: object) -> dict[str, object]:
        return self._upload("upload_large", file, options)

    def _upload(
        self,
        method: str,
        file: str,
        options: dict[str, object],
    ) -> dict[str, object]:
        self.upload_calls.append((method, file, options))
        if self.upload_error is not None:
            raise self.upload_error
        return {
            "secure_url": self.secure_url,
            "public_id": options["public_id"],
        }

    def destroy(self, public_id: str, **options: object) -> dict[str, object]:
        self.destroy_calls.append((public_id, options))
        if public_id in self.destroy_errors_by_public_id:
            raise self.destroy_errors_by_public_id[public_id]
        if self.destroy_error is not None:
            raise self.destroy_error
        return {"result": self.destroy_result}


class ErrorResponse:
    status_code = 401
    reason = "Unauthorized"

    @staticmethod
    def json() -> dict[str, object]:
        return {
            "error": {
                "message": (
                    "access_token=super-secret Bearer abc.def "
                    "client_secret:very-secret"
                )
            }
        }


class MetadataTests(unittest.TestCase):
    def test_normalize_load_and_validate_post(self):
        data = valid_post()
        data["youtube"]["hashtags"] = [" #Musica ", "musica", " Shorts "]

        normalized = normalize_post(data)
        self.assertEqual(normalized["youtube"]["hashtags"], ["Musica", "Shorts"])
        validate_post(normalized)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "post.json"
            write_json(path, data)
            loaded = load_post(path)

        self.assertEqual(loaded.data["youtube"]["hashtags"], ["Musica", "Shorts"])
        self.assertEqual(loaded.for_platform("youtube")["privacy_status"], "private")

    def test_invalid_headlines_are_rejected(self):
        cases = (
            "UM DOIS TRES QUATRO CINCO SEIS SETE",
            "X" * 43,
        )
        for headline in cases:
            with self.subTest(headline=headline):
                data = valid_post()
                data["cover"]["headline"] = headline
                with self.assertRaisesRegex(RuntimeError, "headline.*longo demais"):
                    validate_post(data)

    def test_invalid_or_excessive_hashtags_are_rejected(self):
        cases = (
            ["um", "dois", "tres", "quatro", "cinco", "seis"],
            ["tag-com-hifen"],
            ["Duplicada", "duplicada"],
        )
        for hashtags in cases:
            with self.subTest(hashtags=hashtags):
                data = valid_post()
                data["youtube"]["hashtags"] = hashtags
                with self.assertRaisesRegex(RuntimeError, "Hashtag|hashtags"):
                    validate_post(data)


class CoverAndPreparationTests(unittest.TestCase):
    def test_generates_720x1280_jpeg_cover_from_episode_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_project_config(root)
            episode_dir = write_episode_sources(root)
            destination = root / "output" / "demo_cover.jpg"

            result = generate_cover(root, episode_dir, valid_post(), destination)

            self.assertEqual(result, destination)
            self.assertTrue(destination.is_file())
            with Image.open(destination) as cover:
                self.assertEqual(cover.size, (720, 1280))
                self.assertEqual(cover.format, "JPEG")

    def test_create_post_template_uses_story_and_first_timeline_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            episode_dir = write_episode_sources(root)

            path = create_post_template(episode_dir)
            post = load_post(path)

            self.assertEqual(path, episode_dir / "post.json")
            self.assertEqual(
                post.data["cover"]["source"],
                {"type": "asset", "asset_id": "main_image"},
            )
            self.assertEqual(post.data["youtube"]["privacy_status"], "public")
            self.assertLessEqual(len(post.data["cover"]["headline"].split()), 6)

    def test_prepare_episode_post_creates_template_preview_and_cover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_project_config(root)
            episode_dir = write_episode_sources(root)

            prepared = prepare_episode_post(root, "demo")

            self.assertEqual(prepared.post_path, episode_dir / "post.json")
            self.assertTrue(prepared.cover_path.is_file())
            self.assertEqual(set(prepared.previews), {"youtube", "instagram", "tiktok"})
            self.assertIn("description", prepared.previews["youtube"])


class CredentialAndPublisherTests(unittest.TestCase):
    def test_missing_credentials_are_reported_by_each_publisher(self):
        publishers = {
            "youtube": YouTubePublisher,
            "instagram": InstagramPublisher,
            "tiktok": TikTokPublisher,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for platform, publisher_type in publishers.items():
                with self.subTest(platform=platform):
                    session = FailingSession()
                    publisher = publisher_type(
                        credentials=CredentialStore.from_mapping({}),
                        session=session,
                    )
                    with self.assertRaisesRegex(
                        PublishingError,
                        rf"{publisher_type.__name__}: credenciais nao configuradas",
                    ):
                        publisher.validate(local_context(root, platform), require_credentials=True)
                    self.assertEqual(session.calls, 0)

    def test_credential_repr_never_exposes_secret_values(self):
        store = CredentialStore.from_mapping(
            {
                "YOUTUBE_CLIENT_ID": "visible-key-name-only",
                "YOUTUBE_CLIENT_SECRET": "do-not-print-this-secret",
                "YOUTUBE_ACCESS_TOKEN": "do-not-print-this-token",
            }
        )

        representation = repr(store)

        self.assertIn("YOUTUBE_CLIENT_SECRET", representation)
        self.assertIn("[REDACTED]", representation)
        self.assertNotIn("do-not-print-this-secret", representation)
        self.assertNotIn("do-not-print-this-token", representation)

    def test_publisher_dry_run_makes_no_session_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = FailingSession()
            publisher = YouTubePublisher(
                credentials=CredentialStore.from_mapping({}),
                session=session,
            )

            result = publisher.dry_run(local_context(root, "youtube"))

            self.assertTrue(result.dry_run)
            self.assertEqual(result.status, "validated")
            self.assertEqual(result.details["api_calls_made"], 0)
            self.assertEqual(session.calls, 0)

    def test_instagram_dry_run_makes_no_session_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = FailingSession()
            video_host = RecordingVideoHost(fail_upload=True, fail_cleanup=True)
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {"INSTAGRAM_API_HOST": "graph.instagram.com"}
                ),
                session=session,
                video_host=video_host,
            )

            result = publisher.dry_run(local_context(root, "instagram"))

            self.assertTrue(result.dry_run)
            self.assertEqual(result.status, "validated")
            self.assertEqual(result.details["api_calls_made"], 0)
            self.assertEqual(session.calls, 0)
            self.assertEqual(video_host.upload_calls, [])
            self.assertEqual(video_host.cleanup_calls, 0)

    def test_direct_access_token_does_not_require_client_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                (
                    YouTubePublisher,
                    "youtube",
                    {"YOUTUBE_ACCESS_TOKEN": "token"},
                ),
                (
                    TikTokPublisher,
                    "tiktok",
                    {"TIKTOK_ACCESS_TOKEN": "token"},
                ),
            )
            for publisher_type, platform, values in cases:
                with self.subTest(platform=platform):
                    session = FailingSession()
                    publisher = publisher_type(
                        credentials=CredentialStore.from_mapping(values),
                        session=session,
                    )
                    publisher.validate(local_context(root, platform), require_credentials=True)
                    self.assertEqual(session.calls, 0)

    def test_create_publisher_selects_all_official_adapters(self):
        credentials = CredentialStore.from_mapping({})
        session = FailingSession()

        self.assertIsInstance(create_publisher("youtube", credentials, session), YouTubePublisher)
        self.assertIsInstance(
            create_publisher("instagram", credentials, session), InstagramPublisher
        )
        self.assertIsInstance(create_publisher("tiktok", credentials, session), TikTokPublisher)
        with self.assertRaisesRegex(PublishingError, "Plataforma desconhecida"):
            create_publisher("other", credentials, session)

    def test_all_platforms_cli_defaults_to_dry_run_and_never_calls_network(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_publishable_files(root)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with (
                patch.dict(os.environ, {}, clear=True),
                patch(
                    "requests.sessions.Session.request",
                    side_effect=AssertionError("CLI dry-run tentou acessar a rede"),
                ) as request,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = publish_main(
                    ["demo", "--platform", "all", "--project-root", str(root)]
                )

            self.assertEqual(exit_code, 0, stderr.getvalue())
            self.assertIn("MODO SEGURO: DRY-RUN", stdout.getvalue())
            for platform in ("youtube", "instagram", "tiktok"):
                self.assertIn(f'"platform": "{platform}"', stdout.getvalue())
            request.assert_not_called()

    def test_api_error_is_sanitized_before_becoming_user_facing(self):
        session = StaticSession(ErrorResponse())
        publisher = YouTubePublisher(
            credentials=CredentialStore.from_mapping(
                {"YOUTUBE_ACCESS_TOKEN": "token-from-store"}
            ),
            session=session,
        )

        with self.assertRaises(ApiError) as raised:
            publisher.get_status("video-id")

        message = str(raised.exception)
        self.assertIn("HTTP 401", message)
        self.assertIn("[REDACTED]", message)
        self.assertNotIn("super-secret", message)
        self.assertNotIn("abc.def", message)
        self.assertNotIn("very-secret", message)
        self.assertNotIn("token-from-store", message)
        self.assertEqual(session.calls, 1)

    def test_instagram_token_never_appears_in_cli_output(self):
        token = "ig-live-secret.abc_123"
        public_url = (
            "https://cdn.example.test/existing.mp4?signature=public-url-secret"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_publishable_files(root)
            post = valid_post()
            post["instagram"]["video_url"] = public_url
            write_json(root / "episodes" / "demo" / "post.json", post)
            stdout = io.StringIO()
            stderr = io.StringIO()
            error_response = JsonResponse(
                {"error": {"message": f"received token {token} for {public_url}"}},
                status_code=401,
            )

            with (
                patch.dict(
                    os.environ,
                    {
                        "INSTAGRAM_ACCESS_TOKEN": token,
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                    },
                    clear=True,
                ),
                patch(
                    "requests.sessions.Session.request",
                    return_value=error_response,
                ) as request,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = publish_main(
                    [
                        "demo",
                        "--platform",
                        "instagram",
                        "--live",
                        "--project-root",
                        str(root),
                    ]
                )

        output = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("[REDACTED]", output)
        self.assertNotIn(token, output)
        self.assertNotIn(public_url, output)
        self.assertNotIn("public-url-secret", output)
        self.assertEqual(request.call_args.kwargs["data"]["access_token"], token)
        self.assertNotIn("Authorization", request.call_args.kwargs.get("headers", {}))

    def test_youtube_official_flow_uses_resumable_upload_and_thumbnail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "youtube")
            session = QueueSession(
                [
                    JsonResponse(headers={"Location": "https://upload.example/session"}),
                    JsonResponse({"id": "yt-123"}),
                    JsonResponse({"items": []}),
                ]
            )
            publisher = YouTubePublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "YOUTUBE_CLIENT_ID": "client",
                        "YOUTUBE_CLIENT_SECRET": "secret",
                        "YOUTUBE_ACCESS_TOKEN": "token",
                    }
                ),
                session=session,
            )

            uploaded = publisher.upload(context)
            result = publisher.publish(context, uploaded)

        self.assertEqual(result.external_id, "yt-123")
        self.assertEqual(result.url, "https://www.youtube.com/watch?v=yt-123")
        self.assertEqual([call[0] for call in session.calls], ["POST", "PUT", "POST"])
        self.assertIn("uploadType", session.calls[0][2]["params"])
        self.assertIn("thumbnails/set", session.calls[2][1])

    def test_youtube_thumbnail_error_preserves_uploaded_video_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "youtube")
            session = QueueSession(
                [
                    JsonResponse(headers={"Location": "https://upload.example/session"}),
                    JsonResponse({"id": "yt-already-created"}),
                    ErrorResponse(),
                ]
            )
            publisher = YouTubePublisher(
                credentials=CredentialStore.from_mapping(
                    {"YOUTUBE_ACCESS_TOKEN": "token"}
                ),
                session=session,
            )

            uploaded = publisher.upload(context)
            result = publisher.publish(context, uploaded)

        self.assertEqual(result.external_id, "yt-already-created")
        self.assertEqual(result.status, "uploaded_without_thumbnail")
        self.assertFalse(result.details["thumbnail_set"])
        self.assertIn("warning", result.details)

    def test_tiktok_official_flow_queries_creator_uploads_and_fetches_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "tiktok")
            session = QueueSession(
                [
                    JsonResponse(
                        {
                            "data": {"privacy_level_options": ["SELF_ONLY"]},
                            "error": {"code": "ok", "message": ""},
                        }
                    ),
                    JsonResponse(
                        {
                            "data": {
                                "publish_id": "tt-123",
                                "upload_url": "https://upload.example/tiktok",
                            },
                            "error": {"code": "ok", "message": ""},
                        }
                    ),
                    JsonResponse(status_code=204),
                    JsonResponse(
                        {
                            "data": {"status": "PROCESSING_UPLOAD"},
                            "error": {"code": "ok", "message": ""},
                        }
                    ),
                ]
            )
            publisher = TikTokPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "TIKTOK_CLIENT_KEY": "client",
                        "TIKTOK_CLIENT_SECRET": "secret",
                        "TIKTOK_ACCESS_TOKEN": "token",
                    }
                ),
                session=session,
            )

            uploaded = publisher.upload(context)
            result = publisher.publish(context, uploaded)

        self.assertEqual(uploaded["publish_id"], "tt-123")
        self.assertEqual(result.status, "processing_upload")
        self.assertEqual([call[0] for call in session.calls], ["POST", "POST", "PUT", "POST"])
        self.assertIn("creator_info/query", session.calls[0][1])
        self.assertIn("status/fetch", session.calls[3][1])

    def test_tiktok_chunk_layout_merges_remainder_into_last_chunk(self):
        chunk_size, chunks = TikTokPublisher._chunk_layout(65_000_123)

        self.assertEqual(chunk_size, 10_000_000)
        self.assertEqual(chunks, [10_000_000] * 5 + [15_000_123])
        self.assertEqual(sum(chunks), 65_000_123)
        self.assertLessEqual(chunks[-1], 128_000_000)

    def test_cloudinary_video_host_uploads_video_and_returns_secure_url(self):
        uploader = FakeCloudinaryUploader()
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            video_host = CloudinaryVideoHost(
                cloud_name="cloud-name",
                api_key="api-key",
                api_secret="api-secret-value",
                episode=context.episode,
                uploader=uploader,
                id_factory=lambda: "execution-one",
            )

            public_url = video_host.upload(context.video_path)

        self.assertEqual(public_url, uploader.secure_url)
        self.assertEqual(len(uploader.upload_calls), 1)
        method, uploaded_path, options = uploader.upload_calls[0]
        self.assertEqual(method, "upload")
        self.assertEqual(Path(uploaded_path), context.video_path)
        self.assertEqual(options["resource_type"], "video")
        self.assertEqual(options["public_id"], "instagram/demo/execution-one")
        self.assertFalse(options["overwrite"])
        self.assertEqual(options["cloud_name"], "cloud-name")
        self.assertEqual(options["api_key"], "api-key")
        self.assertEqual(options["api_secret"], "api-secret-value")
        self.assertEqual(options["timeout"], 90.0)
        self.assertNotIn("api-secret-value", repr(video_host))

    def test_cloudinary_video_host_generates_unique_public_id_per_execution(self):
        uploader = FakeCloudinaryUploader()
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            hosts = [
                CloudinaryVideoHost(
                    cloud_name="cloud-name",
                    api_key="api-key",
                    api_secret="api-secret",
                    episode=context.episode,
                    uploader=uploader,
                )
                for _ in range(2)
            ]

            for video_host in hosts:
                video_host.upload(context.video_path)

        public_ids = [call[2]["public_id"] for call in uploader.upload_calls]
        self.assertEqual(len(set(public_ids)), 2)
        for public_id in public_ids:
            self.assertTrue(str(public_id).startswith("instagram/demo/"))
            self.assertFalse(str(public_id).endswith(".mp4"))

    def test_cloudinary_errors_never_expose_api_secret(self):
        cloud_name = "private-cloud-name"
        api_key = "cloudinary-api-key-value"
        secret = "cloudinary-super-secret-value"
        uploader = FakeCloudinaryUploader(
            upload_error=RuntimeError(
                f"SDK echoed {cloud_name} {api_key} {secret}"
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            video_host = CloudinaryVideoHost(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=secret,
                episode=context.episode,
                uploader=uploader,
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(PublishingError) as raised:
                    video_host.upload(context.video_path)
                video_host.cleanup()
                stderr.write(f"ERRO: {raised.exception}")

        output = stdout.getvalue() + stderr.getvalue()
        self.assertIn("upload do video no Cloudinary falhou", output)
        for credential in (cloud_name, api_key, secret):
            self.assertNotIn(credential, output)
            self.assertNotIn(credential, repr(raised.exception))
            self.assertNotIn(credential, repr(video_host))
        self.assertEqual(uploader.destroy_calls, [])

    def test_cloudinary_delete_error_is_clear_and_redacted(self):
        cloud_name = "private-delete-cloud"
        api_key = "cloudinary-delete-api-key"
        secret = "cloudinary-delete-secret-value"
        uploader = FakeCloudinaryUploader(
            destroy_error=RuntimeError(
                f"SDK echoed {cloud_name} {api_key} {secret}"
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            video_host = CloudinaryVideoHost(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=secret,
                episode=context.episode,
                uploader=uploader,
            )
            video_host.upload(context.video_path)
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(PublishingError) as raised:
                    video_host.cleanup()
                stderr.write(f"ERRO: {raised.exception}")

        output = stdout.getvalue() + stderr.getvalue()
        self.assertIn("cleanup do video no Cloudinary falhou", output)
        for credential in (cloud_name, api_key, secret):
            self.assertNotIn(credential, output)
            self.assertNotIn(credential, repr(raised.exception))

    def test_cloudinary_delete_requires_a_confirmed_sdk_result(self):
        uploader = FakeCloudinaryUploader(destroy_result="error")
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            video_host = CloudinaryVideoHost(
                cloud_name="cloud-name",
                api_key="api-key",
                api_secret="api-secret",
                episode=context.episode,
                uploader=uploader,
            )
            video_host.upload(context.video_path)

            with self.assertRaisesRegex(
                PublishingError,
                "Cloudinary nao confirmou o cleanup do video",
            ):
                video_host.cleanup()

    def test_cloudinary_stale_cleanup_deletes_asset_older_than_twelve_hours(self):
        old_public_id = "instagram/duckworth/old-execution"
        uploader = FakeCloudinaryUploader(
            resource_pages=[
                {
                    "resources": [
                        {
                            "public_id": old_public_id,
                            "created_at": "2026-08-27T23:59:59Z",
                        }
                    ],
                    "next_cursor": "page-two",
                },
                {"resources": []},
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            video_host = CloudinaryVideoHost(
                cloud_name="cloud-name",
                api_key="api-key",
                api_secret="api-secret",
                episode=context.episode,
                uploader=uploader,
                id_factory=lambda: "current-execution",
                now_factory=lambda: datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                video_host.upload(context.video_path)

        self.assertEqual([call[0] for call in uploader.destroy_calls], [old_public_id])
        self.assertEqual(len(uploader.resources_calls), 2)
        first_list_call, second_list_call = uploader.resources_calls
        self.assertEqual(first_list_call["prefix"], "instagram/")
        self.assertEqual(first_list_call["resource_type"], "video")
        self.assertEqual(first_list_call["type"], "upload")
        self.assertEqual(first_list_call["max_results"], 500)
        self.assertNotIn("next_cursor", first_list_call)
        self.assertEqual(second_list_call["next_cursor"], "page-two")
        self.assertIn(
            f"[cloudinary] cleanup deleted: {old_public_id}",
            stdout.getvalue(),
        )

    def test_cloudinary_stale_cleanup_keeps_asset_at_twelve_hour_boundary(self):
        recent_public_id = "instagram/duckworth/recent-execution"
        uploader = FakeCloudinaryUploader(
            resource_pages=[
                {
                    "resources": [
                        {
                            "public_id": recent_public_id,
                            "created_at": "2026-08-28T00:00:00Z",
                        }
                    ]
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            video_host = CloudinaryVideoHost(
                cloud_name="cloud-name",
                api_key="api-key",
                api_secret="api-secret",
                episode=context.episode,
                uploader=uploader,
                now_factory=lambda: datetime(2026, 8, 28, 12, tzinfo=timezone.utc),
            )

            video_host.upload(context.video_path)

        self.assertEqual(uploader.destroy_calls, [])

    def test_cloudinary_stale_cleanup_never_deletes_outside_instagram_namespace(self):
        outside_public_id = "other/duckworth/old-execution"
        uploader = FakeCloudinaryUploader(
            resource_pages=[
                {
                    "resources": [
                        {
                            "public_id": outside_public_id,
                            "created_at": "2020-01-01T00:00:00Z",
                        }
                    ]
                }
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            video_host = CloudinaryVideoHost(
                cloud_name="cloud-name",
                api_key="api-key",
                api_secret="api-secret",
                episode=context.episode,
                uploader=uploader,
            )

            video_host.upload(context.video_path)

        self.assertEqual(uploader.destroy_calls, [])
        self.assertEqual(uploader.resources_calls[0]["prefix"], "instagram/")

    def test_cloudinary_stale_cleanup_never_deletes_current_execution_asset(self):
        current_public_id = "instagram/demo/current-execution"
        execution_ids = iter(["current-execution", "next-execution"])
        uploader = FakeCloudinaryUploader(
            resource_pages=[
                {"resources": []},
                {
                    "resources": [
                        {
                            "public_id": current_public_id,
                            "created_at": "2020-01-01T00:00:00Z",
                        }
                    ]
                },
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            context = local_context(Path(temp_dir), "instagram")
            video_host = CloudinaryVideoHost(
                cloud_name="cloud-name",
                api_key="api-key",
                api_secret="api-secret",
                episode=context.episode,
                uploader=uploader,
                id_factory=lambda: next(execution_ids),
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                video_host.upload(context.video_path)
                video_host.upload(context.video_path)

        self.assertEqual(uploader.destroy_calls, [])
        self.assertEqual(len(uploader.upload_calls), 2)

    def test_instagram_official_flow_uploads_polls_and_media_publishes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            session = QueueSession(
                [
                    JsonResponse(
                        {
                            "id": "container-123",
                            "uri": "https://rupload.facebook.com/ig-api-upload/v1/container-123",
                        }
                    ),
                    JsonResponse({"success": True}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    JsonResponse({"id": "media-123"}),
                    JsonResponse(
                        {
                            "id": "media-123",
                            "media_type": "VIDEO",
                            "permalink": "https://www.instagram.com/reel/example/",
                        }
                    ),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v1.0",
                        "INSTAGRAM_API_HOST": "graph.facebook.com",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
            )

            uploaded = publisher.upload(context)
            result = publisher.publish(context, uploaded)

        self.assertEqual(result.external_id, "media-123")
        self.assertEqual(result.url, "https://www.instagram.com/reel/example/")
        self.assertEqual(
            [call[0] for call in session.calls],
            ["POST", "POST", "GET", "POST", "GET"],
        )
        self.assertIn("media_publish", session.calls[3][1])
        self.assertEqual(
            session.calls[1][2]["headers"]["Authorization"],
            "OAuth token",
        )
        for index in (0, 2, 3, 4):
            self.assertEqual(
                session.calls[index][2]["headers"]["Authorization"],
                "Bearer token",
            )

    def test_instagram_graph_host_uses_video_host_then_cleans_up_after_publish(self):
        token = "instagram-graph-token"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            video_host = RecordingVideoHost()
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    JsonResponse({"id": "media-123"}),
                    JsonResponse(
                        {
                            "id": "media-123",
                            "media_type": "VIDEO",
                            "permalink": "https://www.instagram.com/reel/example/",
                        }
                    ),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": token,
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                video_host=video_host,
            )

            uploaded = publisher.upload(context)
            self.assertEqual(video_host.upload_calls, [context.video_path])
            self.assertEqual(video_host.cleanup_calls, 0)
            result = publisher.publish(context, uploaded)

        self.assertEqual(result.external_id, "media-123")
        self.assertEqual(result.url, "https://www.instagram.com/reel/example/")
        self.assertEqual(video_host.cleanup_calls, 1)
        self.assertEqual(
            [call[0] for call in session.calls],
            ["POST", "GET", "POST", "GET"],
        )

        create_call, status_call, publish_call, media_call = session.calls
        self.assertEqual(
            create_call[2]["data"]["video_url"],
            "https://cdn.example.test/video.mp4",
        )
        self.assertNotIn("upload_type", create_call[2]["data"])
        self.assertEqual(create_call[2]["data"]["access_token"], token)
        self.assertEqual(
            status_call[2]["params"],
            {"fields": "status_code,status", "access_token": token},
        )
        self.assertEqual(
            publish_call[2]["data"],
            {"creation_id": "container-123", "access_token": token},
        )
        self.assertEqual(
            media_call[2]["params"],
            {"fields": "id,permalink,media_type", "access_token": token},
        )
        for _, url, kwargs in (create_call, status_call, publish_call, media_call):
            self.assertTrue(url.startswith("https://graph.instagram.com/v26.0/"))
            self.assertNotIn(token, url)
            self.assertNotIn("Authorization", kwargs.get("headers", {}))

    def test_instagram_direct_video_url_bypasses_video_host(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            context.metadata["video_url"] = "https://cdn.example.test/existing.mp4"
            cloudinary_client = FakeCloudinaryUploader(
                upload_error=AssertionError("Cloudinary must not be called")
            )
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                        "INSTAGRAM_VIDEO_HOST": "cloudinary",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                cloudinary_client=cloudinary_client,
            )

            publisher.upload(context)

        self.assertEqual(cloudinary_client.upload_calls, [])
        self.assertEqual(cloudinary_client.destroy_calls, [])
        self.assertEqual(cloudinary_client.resources_calls, [])
        self.assertEqual(
            session.calls[0][2]["data"]["video_url"],
            "https://cdn.example.test/existing.mp4",
        )
        self.assertNotIn("upload_type", session.calls[0][2]["data"])

    def test_instagram_login_without_public_video_source_fails_before_graph(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = FailingSession()
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                    }
                ),
                session=session,
            )

            with self.assertRaises(PublishingError) as raised:
                publisher.upload(local_context(root, "instagram"))

        self.assertEqual(
            str(raised.exception),
            "InstagramPublisher: Instagram Login requer uma video_url "
            "HTTPS publicamente acessível.",
        )
        self.assertEqual(session.calls, 0)

    def test_instagram_presigned_video_host_is_configured_from_environment(self):
        upload_url = "https://storage.example.test/upload?signature=upload-secret"
        public_url = "https://cdn.example.test/temporary/video.mp4"
        delete_url = "https://storage.example.test/delete?signature=delete-secret"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            credentials = CredentialStore.load(
                root,
                environ={
                    "INSTAGRAM_ACCESS_TOKEN": "token",
                    "INSTAGRAM_ACCOUNT_ID": "account",
                    "META_GRAPH_API_VERSION": "v26.0",
                    "INSTAGRAM_API_HOST": "graph.instagram.com",
                    "INSTAGRAM_VIDEO_HOST": "presigned",
                    "INSTAGRAM_VIDEO_HOST_UPLOAD_URL": upload_url,
                    "INSTAGRAM_VIDEO_HOST_PUBLIC_URL": public_url,
                    "INSTAGRAM_VIDEO_HOST_DELETE_URL": delete_url,
                },
            )
            session = QueueSession(
                [
                    JsonResponse(),
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    JsonResponse({"id": "media-123"}),
                    JsonResponse(
                        {
                            "id": "media-123",
                            "media_type": "VIDEO",
                            "permalink": "https://www.instagram.com/reel/example/",
                        }
                    ),
                    JsonResponse(status_code=204),
                ]
            )
            cloudinary_client = FakeCloudinaryUploader(
                upload_error=AssertionError("Cloudinary must not be called")
            )
            publisher = InstagramPublisher(
                credentials=credentials,
                session=session,
                poll_interval_seconds=0,
                cloudinary_client=cloudinary_client,
            )

            uploaded = publisher.upload(context)
            result = publisher.publish(context, uploaded)

        self.assertEqual(result.external_id, "media-123")
        self.assertEqual(
            [call[0] for call in session.calls],
            ["PUT", "POST", "GET", "POST", "GET", "DELETE"],
        )
        self.assertEqual(session.calls[0][1], upload_url)
        self.assertEqual(session.calls[1][2]["data"]["video_url"], public_url)
        self.assertEqual(session.calls[-1][1], delete_url)
        self.assertEqual(cloudinary_client.upload_calls, [])
        self.assertEqual(cloudinary_client.destroy_calls, [])
        self.assertEqual(cloudinary_client.resources_calls, [])

    def test_instagram_cloudinary_host_uploads_and_deletes_after_publish_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            credentials = CredentialStore.load(
                root,
                environ={
                    "INSTAGRAM_ACCESS_TOKEN": "instagram-token",
                    "INSTAGRAM_ACCOUNT_ID": "account",
                    "META_GRAPH_API_VERSION": "v26.0",
                    "INSTAGRAM_API_HOST": "graph.instagram.com",
                    "INSTAGRAM_VIDEO_HOST": "cloudinary",
                    "CLOUDINARY_CLOUD_NAME": "cloud-name",
                    "CLOUDINARY_API_KEY": "api-key",
                    "CLOUDINARY_API_SECRET": "api-secret",
                },
            )
            cloudinary_client = FakeCloudinaryUploader()
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    JsonResponse({"id": "media-123"}),
                    JsonResponse(
                        {
                            "id": "media-123",
                            "media_type": "VIDEO",
                            "permalink": "https://www.instagram.com/reel/example/",
                        }
                    ),
                ]
            )
            publisher = InstagramPublisher(
                credentials=credentials,
                session=session,
                poll_interval_seconds=0,
                cloudinary_client=cloudinary_client,
            )
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                uploaded = publisher.upload(context)

                self.assertEqual(len(cloudinary_client.upload_calls), 1)
                self.assertEqual(cloudinary_client.destroy_calls, [])
                method, uploaded_path, upload_options = cloudinary_client.upload_calls[0]
                public_id = str(upload_options["public_id"])
                self.assertEqual(method, "upload")
                self.assertEqual(Path(uploaded_path), context.video_path)
                self.assertEqual(upload_options["resource_type"], "video")
                self.assertEqual(
                    session.calls[0][2]["data"]["video_url"],
                    cloudinary_client.secure_url,
                )

                result = publisher.publish(context, uploaded)

        self.assertEqual(result.external_id, "media-123")
        self.assertEqual(len(cloudinary_client.destroy_calls), 1)
        deleted_public_id, delete_options = cloudinary_client.destroy_calls[0]
        self.assertEqual(deleted_public_id, public_id)
        self.assertEqual(delete_options["resource_type"], "video")
        self.assertEqual(delete_options["type"], "upload")
        self.assertTrue(delete_options["invalidate"])
        self.assertEqual(delete_options["cloud_name"], "cloud-name")
        self.assertEqual(delete_options["api_key"], "api-key")
        self.assertEqual(delete_options["api_secret"], "api-secret")
        self.assertEqual(
            [line for line in stdout.getvalue().splitlines() if line],
            [
                f"[cloudinary] uploaded: {public_id}",
                "[instagram] published: media-123",
                f"[cloudinary] deleted: {public_id}",
            ],
        )

    def test_instagram_cloudinary_stale_cleanup_error_does_not_block_publish(self):
        cloud_name = "private-cloud-name"
        api_key = "private-api-key"
        api_secret = "private-api-secret"
        old_public_id = "instagram/duckworth/old-execution"
        cleanup_error = RuntimeError(
            f"SDK echoed {cloud_name} {api_key} {api_secret}"
        )
        cloudinary_client = FakeCloudinaryUploader(
            resource_pages=[
                {
                    "resources": [
                        {
                            "public_id": old_public_id,
                            "created_at": "2020-01-01T00:00:00Z",
                        }
                    ]
                }
            ],
            destroy_errors_by_public_id={old_public_id: cleanup_error},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    JsonResponse({"id": "media-123"}),
                    JsonResponse({"id": "media-123", "media_type": "VIDEO"}),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "instagram-token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                        "INSTAGRAM_VIDEO_HOST": "cloudinary",
                        "CLOUDINARY_CLOUD_NAME": cloud_name,
                        "CLOUDINARY_API_KEY": api_key,
                        "CLOUDINARY_API_SECRET": api_secret,
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                cloudinary_client=cloudinary_client,
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                uploaded = publisher.upload(context)
                result = publisher.publish(context, uploaded)

        current_public_id = str(cloudinary_client.upload_calls[0][2]["public_id"])
        self.assertEqual(result.status, "published")
        self.assertEqual(
            [call[0] for call in cloudinary_client.destroy_calls],
            [old_public_id, current_public_id],
        )
        output = stdout.getvalue() + stderr.getvalue()
        self.assertIn("[cloudinary] cleanup warning: delete failed", output)
        self.assertIn("[instagram] published: media-123", output)
        self.assertIn(f"[cloudinary] deleted: {current_public_id}", output)
        for credential in (cloud_name, api_key, api_secret):
            self.assertNotIn(credential, output)

    def test_instagram_cloudinary_host_is_not_deleted_on_media_publish_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            cloudinary_client = FakeCloudinaryUploader()
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    requests.Timeout("media_publish result is ambiguous"),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "instagram-token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                        "INSTAGRAM_VIDEO_HOST": "cloudinary",
                        "CLOUDINARY_CLOUD_NAME": "cloud-name",
                        "CLOUDINARY_API_KEY": "api-key",
                        "CLOUDINARY_API_SECRET": "api-secret",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                cloudinary_client=cloudinary_client,
            )

            uploaded = publisher.upload(context)
            with self.assertRaises(ApiError):
                publisher.publish(context, uploaded)

        self.assertEqual(len(cloudinary_client.upload_calls), 1)
        self.assertEqual(cloudinary_client.destroy_calls, [])

    def test_instagram_cloudinary_host_is_not_deleted_without_published_media_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            cloudinary_client = FakeCloudinaryUploader()
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    JsonResponse({}),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "instagram-token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                        "INSTAGRAM_VIDEO_HOST": "cloudinary",
                        "CLOUDINARY_CLOUD_NAME": "cloud-name",
                        "CLOUDINARY_API_KEY": "api-key",
                        "CLOUDINARY_API_SECRET": "api-secret",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                cloudinary_client=cloudinary_client,
            )

            uploaded = publisher.upload(context)
            with self.assertRaisesRegex(ApiError, "nao retornou media id"):
                publisher.publish(context, uploaded)

        self.assertEqual(len(cloudinary_client.upload_calls), 1)
        self.assertEqual(cloudinary_client.destroy_calls, [])

    def test_instagram_cloudinary_host_is_not_deleted_on_polling_timeout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            cloudinary_client = FakeCloudinaryUploader()
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "IN_PROGRESS"}),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "instagram-token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                        "INSTAGRAM_VIDEO_HOST": "cloudinary",
                        "CLOUDINARY_CLOUD_NAME": "cloud-name",
                        "CLOUDINARY_API_KEY": "api-key",
                        "CLOUDINARY_API_SECRET": "api-secret",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                poll_timeout_seconds=0,
                cloudinary_client=cloudinary_client,
            )

            with patch(
                "publishing.instagram.time.monotonic",
                side_effect=[0.0, 0.0, 1.0],
            ):
                with self.assertRaisesRegex(ApiError, "timeout aguardando"):
                    publisher.upload(context)

        self.assertEqual(len(cloudinary_client.upload_calls), 1)
        self.assertEqual(cloudinary_client.destroy_calls, [])

    def test_instagram_cloudinary_host_is_not_deleted_without_media_publish_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            cloudinary_client = FakeCloudinaryUploader()
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "PUBLISHED"}),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "instagram-token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                        "INSTAGRAM_VIDEO_HOST": "cloudinary",
                        "CLOUDINARY_CLOUD_NAME": "cloud-name",
                        "CLOUDINARY_API_KEY": "api-key",
                        "CLOUDINARY_API_SECRET": "api-secret",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                cloudinary_client=cloudinary_client,
            )

            with self.assertRaisesRegex(ApiError, "status PUBLISHED"):
                publisher.upload(context)

        self.assertEqual(len(cloudinary_client.upload_calls), 1)
        self.assertEqual(cloudinary_client.destroy_calls, [])

    def test_instagram_cloudinary_missing_credentials_fails_before_network(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = FailingSession()
            cloudinary_client = FakeCloudinaryUploader()
            publisher = InstagramPublisher(
                credentials=CredentialStore.load(
                    root,
                    environ={
                        "INSTAGRAM_ACCESS_TOKEN": "instagram-token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                        "INSTAGRAM_VIDEO_HOST": "cloudinary",
                    },
                ),
                session=session,
                cloudinary_client=cloudinary_client,
            )

            with self.assertRaises(PublishingError) as raised:
                publisher.upload(local_context(root, "instagram"))

        message = str(raised.exception)
        self.assertIn("Cloudinary nao configurado", message)
        self.assertIn("CLOUDINARY_CLOUD_NAME", message)
        self.assertIn("CLOUDINARY_API_KEY", message)
        self.assertIn("CLOUDINARY_API_SECRET", message)
        self.assertEqual(session.calls, 0)
        self.assertEqual(cloudinary_client.upload_calls, [])
        self.assertEqual(cloudinary_client.destroy_calls, [])

    def test_instagram_keeps_hosted_video_when_media_publish_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            video_host = RecordingVideoHost()
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    ErrorResponse(),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                video_host=video_host,
            )

            uploaded = publisher.upload(context)
            self.assertEqual(video_host.cleanup_calls, 0)
            with self.assertRaises(ApiError):
                publisher.publish(context, uploaded)

        self.assertEqual(video_host.cleanup_calls, 0)

    def test_instagram_cleanup_failure_is_a_warning_after_publish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = local_context(root, "instagram")
            video_host = RecordingVideoHost(fail_cleanup=True)
            session = QueueSession(
                [
                    JsonResponse({"id": "container-123"}),
                    JsonResponse({"status_code": "FINISHED", "status": "ok"}),
                    JsonResponse({"id": "media-123"}),
                    JsonResponse({"id": "media-123", "media_type": "VIDEO"}),
                ]
            )
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                    }
                ),
                session=session,
                poll_interval_seconds=0,
                video_host=video_host,
            )

            uploaded = publisher.upload(context)
            result = publisher.publish(context, uploaded)

        self.assertEqual(result.status, "published")
        self.assertEqual(video_host.cleanup_calls, 1)
        self.assertIn("cleanup_warning", result.details)
        self.assertNotIn("storage details", str(result.details))

    def test_instagram_keeps_hosted_video_when_container_request_is_uncertain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = TimeoutSession()
            video_host = RecordingVideoHost()
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                    }
                ),
                session=session,
                video_host=video_host,
            )

            with self.assertRaisesRegex(ApiError, "falha de conexao") as raised:
                publisher.upload(local_context(root, "instagram"))

        self.assertNotIn("request details", str(raised.exception))
        self.assertEqual(session.calls, 1)
        self.assertEqual(video_host.cleanup_calls, 0)

    def test_instagram_rejects_non_public_video_host_url_before_graph(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = FailingSession()
            video_host = RecordingVideoHost("https://127.0.0.1/video.mp4")
            publisher = InstagramPublisher(
                credentials=CredentialStore.from_mapping(
                    {
                        "INSTAGRAM_ACCESS_TOKEN": "token",
                        "INSTAGRAM_ACCOUNT_ID": "account",
                        "META_GRAPH_API_VERSION": "v26.0",
                        "INSTAGRAM_API_HOST": "graph.instagram.com",
                    }
                ),
                session=session,
                video_host=video_host,
            )

            with self.assertRaisesRegex(PublishingError, "URL retornada pelo VideoHost"):
                publisher.upload(local_context(root, "instagram"))

        self.assertEqual(video_host.cleanup_calls, 1)
        self.assertEqual(session.calls, 0)


if __name__ == "__main__":
    unittest.main()
