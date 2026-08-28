from __future__ import annotations

import random
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import AssetSpec
from .utils import load_json, validate_schema


HEADERS = {
    "User-Agent": "MusicShortFactory/7.0 (real-assets-only)",
    "Accept": "image/*,*/*;q=0.8",
}


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
        self.width = width * scale
        self.height = height * scale
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

    @staticmethod
    def _verify_image(path: Path) -> None:
        try:
            with Image.open(path) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise RuntimeError(f"Arquivo nao e uma imagem valida: {path}") from exc

    @staticmethod
    def _download(url: str, destination: Path, attempts: int = 6) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(url, headers=HEADERS, timeout=90, allow_redirects=True)
                if response.status_code == 429:
                    wait = float(response.headers.get("Retry-After", 2.0 * attempt))
                    last_error = RuntimeError(f"HTTP 429 ao baixar {url}")
                    time.sleep(wait + random.uniform(0.2, 0.6))
                    continue
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                if content_type and "image" not in content_type and "octet-stream" not in content_type:
                    raise RuntimeError(f"Resposta nao parece imagem ({content_type}): {url}")
                partial.write_bytes(response.content)
                AssetManager._verify_image(partial)
                partial.replace(destination)
                return
            except Exception as exc:  # requests exposes several transport exceptions.
                last_error = exc
                partial.unlink(missing_ok=True)
                if attempt < attempts:
                    time.sleep(1.4 * attempt + random.uniform(0.2, 0.6))
        raise RuntimeError(f"Falha ao baixar {url}: {last_error}") from last_error
