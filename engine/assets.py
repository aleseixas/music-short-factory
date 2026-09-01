from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps, UnidentifiedImageError

from .ffmpeg import VideoStreamInfo, probe_video_stream
from .media_cache import download_to_cache
from .models import AssetSpec, TimelineScene
from .utils import load_json, validate_schema


OVERLAY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def load_asset_catalog(path: Path) -> dict[str, AssetSpec]:
    data = load_json(path)
    validate_schema(data, path)
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise RuntimeError("assets.json precisa ter uma lista nao vazia em 'assets'.")

    assets: dict[str, AssetSpec] = {}
    for index, raw in enumerate(raw_assets, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Asset {index} e invalido.")
        asset_id = str(raw.get("id", "")).strip()
        file_name = str(raw.get("file", "")).strip()
        focus = raw.get("focus", {})
        if not re.fullmatch(r"[A-Za-z0-9_-]+", asset_id) or asset_id in assets:
            raise RuntimeError(f"ID de asset ausente ou duplicado: {asset_id!r}")
        if not file_name or Path(file_name).name != file_name:
            raise RuntimeError(f"Nome de arquivo inseguro no asset {asset_id!r}.")
        if not isinstance(focus, dict):
            raise RuntimeError(f"Foco invalido no asset {asset_id!r}.")
        try:
            focus_x = float(focus.get("x", 0.5))
            focus_y = float(focus.get("y", 0.5))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Foco invalido no asset {asset_id!r}.") from exc
        if not 0 <= focus_x <= 1 or not 0 <= focus_y <= 1:
            raise RuntimeError(f"Foco do asset {asset_id!r} precisa ficar entre 0 e 1.")
        url = str(raw.get("url", "")).strip() or None
        if url and urlparse(url).scheme not in {"http", "https"}:
            raise RuntimeError(f"URL invalida no asset {asset_id!r}.")
        assets[asset_id] = AssetSpec(
            id=asset_id,
            file=file_name,
            url=url,
            credit=str(raw.get("credit", "")).strip(),
            license=str(raw.get("license", "")).strip(),
            focus_x=focus_x,
            focus_y=focus_y,
        )
    return assets


class AssetManager:
    def __init__(
        self,
        assets_dir: Path,
        work_dir: Path,
        width: int,
        height: int,
        scale: int,
        allowed_assets_root: Path | None = None,
        video_cache_dir: Path | None = None,
    ):
        self.assets_dir = assets_dir.resolve()
        allowed_root = (
            allowed_assets_root.resolve()
            if allowed_assets_root is not None
            else assets_dir.parent.resolve()
        )
        try:
            self.assets_dir.relative_to(allowed_root)
        except ValueError as exc:
            raise RuntimeError(
                f"Pasta de assets fora do episodio permitido: {self.assets_dir}"
            ) from exc
        self.prepared_dir = (work_dir / "prepared").resolve()
        self.video_cache_dir = video_cache_dir.resolve() if video_cache_dir else None
        self.output_width = width
        self.output_height = height
        self.width = width * scale
        self.height = height * scale
        self._video_info: dict[Path, VideoStreamInfo] = {}
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.prepared_dir.mkdir(parents=True, exist_ok=True)

    def source_path(self, asset: AssetSpec) -> Path:
        path = (self.assets_dir / asset.file).resolve()
        try:
            path.relative_to(self.assets_dir)
        except ValueError as exc:
            raise RuntimeError(f"Asset fora da pasta permitida: {asset.file}") from exc
        return path

    def ensure(self, asset: AssetSpec) -> Path:
        path = self.source_path(asset)
        if asset.is_video:
            remote = False
            if not path.is_file():
                if not asset.url:
                    raise RuntimeError(
                        f"Asset de video ausente e sem URL: {asset.id} ({path})."
                    )
                if self.video_cache_dir is None:
                    raise RuntimeError(
                        f"Cache de video nao configurado para o asset remoto {asset.id!r}."
                    )
                path = download_to_cache(
                    asset.url,
                    self.video_cache_dir,
                    asset.file,
                    f"asset de video {asset.id!r}",
                )
                remote = True
            if path not in self._video_info:
                try:
                    self._video_info[path] = probe_video_stream(path)
                except RuntimeError as exc:
                    if remote:
                        path.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Asset de video invalido {asset.id!r}: {exc}"
                    ) from exc
            return path
        if path.exists():
            self._verify_image(path)
            return path
        if not asset.url:
            raise RuntimeError(f"Asset ausente e sem URL: {asset.id} ({path})")
        self._download(asset.url, path)
        self._verify_image(path)
        return path

    def ensure_all(self, assets: list[AssetSpec] | tuple[AssetSpec, ...]) -> dict[str, Path]:
        resolved: dict[str, Path] = {}
        for asset in assets:
            if asset.id not in resolved:
                resolved[asset.id] = self.ensure(asset)
        return resolved

    def prepare(self, asset: AssetSpec, focus_x: float | None = None, focus_y: float | None = None) -> Path:
        source = self.ensure(asset)
        fx = asset.focus_x if focus_x is None else focus_x
        fy = asset.focus_y if focus_y is None else focus_y
        if not 0 <= fx <= 1 or not 0 <= fy <= 1:
            raise RuntimeError(f"Foco invalido para o asset {asset.id!r}.")
        if asset.is_video:
            return source
        # Six decimals are finer than one source pixel even on very large photos.
        target = self.prepared_dir / f"{asset.id}_{fx:.6f}_{fy:.6f}.jpg"
        if target.exists():
            return target

        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            fitted = ImageOps.fit(
                image,
                (self.width, self.height),
                method=Image.Resampling.LANCZOS,
                centering=(fx, fy),
            )
            fitted.save(target, format="JPEG", quality=95, subsampling=0, optimize=True)
        return target

    def video_info(self, asset: AssetSpec) -> VideoStreamInfo:
        if not asset.is_video:
            raise RuntimeError(f"Asset {asset.id!r} nao e um video.")
        source = self.ensure(asset)
        return self._video_info[source]

    def preflight_video_scene(self, scene: TimelineScene, fps: int) -> VideoStreamInfo | None:
        if not scene.asset.is_video:
            return None
        info = self.video_info(scene.asset)
        start = scene.shot.source_start_seconds
        requested_end = scene.shot.source_end_seconds
        tolerance = 1e-6
        if start >= info.duration - tolerance:
            raise RuntimeError(
                f"Shot {scene.shot.id!r}: source_start_seconds={start:.3f}s fica "
                f"no ou apos o fim do video {scene.asset.id!r} ({info.duration:.3f}s)."
            )
        if requested_end is not None and requested_end > info.duration + tolerance:
            raise RuntimeError(
                f"Shot {scene.shot.id!r}: source_end_seconds={requested_end:.3f}s "
                f"ultrapassa a duracao do video {scene.asset.id!r} ({info.duration:.3f}s)."
            )
        effective_end = min(requested_end or info.duration, info.duration)
        available = effective_end - start
        required = scene.render_frames / fps
        if available + tolerance < required:
            crossfade_note = (
                f", incluindo {scene.transition_frames / fps:.3f}s de handle de crossfade"
                if scene.transition_frames
                else ""
            )
            raise RuntimeError(
                f"Trecho de video insuficiente no shot {scene.shot.id!r} "
                f"(asset {scene.asset.id!r}): disponivel={available:.3f}s; "
                f"necessario={required:.3f}s{crossfade_note}. Loop nao e permitido."
            )
        return info

    def prepare_overlay(self, asset: AssetSpec, scale: float) -> Path:
        """Create a contained PNG overlay without fetching or cropping the source."""
        source = self.source_path(asset)
        if source.suffix.lower() not in OVERLAY_EXTENSIONS:
            supported = ", ".join(sorted(OVERLAY_EXTENSIONS))
            raise RuntimeError(
                f"Formato de overlay nao suportado para {asset.id!r}: {source.suffix or '(sem extensao)'}. "
                f"Use: {supported}."
            )
        if not source.is_file():
            raise RuntimeError(f"Asset de overlay ausente: {asset.id} ({source})")
        self._verify_image(source)
        target = self.prepared_dir / f"overlay_{asset.id}_{scale:.3f}.png"
        if target.exists():
            return target
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image = image.convert("RGBA")
            # Overlays are composed after scene downsampling, on the final canvas.
            # Reserve top/bottom safe areas and enough room for the 1.12 bounce peak.
            safe_height = round(self.output_height * 0.54)
            image.thumbnail(
                (
                    max(1, round(min(self.output_width * scale, self.output_width * 0.85 / 1.12))),
                    max(1, round(min(self.output_height * scale, safe_height / 1.12))),
                ),
                Image.Resampling.LANCZOS,
            )
            image.save(target, format="PNG", optimize=True)
        return target

    @staticmethod
    def _verify_image(path: Path) -> None:
        try:
            with Image.open(path) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise RuntimeError(f"Arquivo nao e uma imagem valida: {path}") from exc

    @staticmethod
    def _download(url: str, destination: Path, attempts: int = 6) -> None:
        try:
            downloaded = download_to_cache(
                url,
                destination.parent,
                destination.name,
                f"asset de imagem {destination.name!r}",
                attempts=attempts,
            )
            AssetManager._verify_image(downloaded)
        except RuntimeError:
            destination.unlink(missing_ok=True)
            raise
