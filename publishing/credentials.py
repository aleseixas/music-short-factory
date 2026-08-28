from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


PLATFORM_KEYS: dict[str, tuple[str, ...]] = {
    "youtube": (
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_ACCESS_TOKEN",
        "YOUTUBE_REFRESH_TOKEN",
    ),
    "instagram": (
        "META_APP_ID",
        "META_APP_SECRET",
        "INSTAGRAM_ACCESS_TOKEN",
        "INSTAGRAM_ACCOUNT_ID",
        "META_GRAPH_API_VERSION",
        "INSTAGRAM_API_HOST",
        "INSTAGRAM_VIDEO_HOST",
        "INSTAGRAM_VIDEO_HOST_UPLOAD_URL",
        "INSTAGRAM_VIDEO_HOST_PUBLIC_URL",
        "INSTAGRAM_VIDEO_HOST_DELETE_URL",
        "CLOUDINARY_CLOUD_NAME",
        "CLOUDINARY_API_KEY",
        "CLOUDINARY_API_SECRET",
    ),
    "tiktok": (
        "TIKTOK_CLIENT_KEY",
        "TIKTOK_CLIENT_SECRET",
        "TIKTOK_ACCESS_TOKEN",
        "TIKTOK_REFRESH_TOKEN",
    ),
}
KNOWN_KEYS = frozenset(
    key for keys in PLATFORM_KEYS.values() for key in keys
) | {
    "YOUTUBE_REDIRECT_URI",
    "TIKTOK_REDIRECT_URI",
}


@dataclass(frozen=True, repr=False)
class CredentialStore:
    """Secret container whose representation never exposes values."""

    _values: Mapping[str, str]

    @classmethod
    def load(
        cls,
        project_root: Path,
        environ: Mapping[str, str] | None = None,
    ) -> "CredentialStore":
        file_values = _read_env_file(project_root / ".env")
        process_values = dict(os.environ if environ is None else environ)
        values = {
            key: value
            for key, value in {**file_values, **process_values}.items()
            if key in KNOWN_KEYS
        }
        clean_values = {key: str(value).strip() for key, value in values.items() if value}
        clean_values["__ENV_PATH__"] = str(project_root / ".env")
        return cls(clean_values)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "CredentialStore":
        return cls({key: str(value).strip() for key, value in values.items() if value})

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def has(self, key: str) -> bool:
        return bool(self.get(key))

    def present_for(self, platform: str) -> tuple[str, ...]:
        return tuple(key for key in PLATFORM_KEYS[platform] if self.has(key))

    def persist_env_values(self, updates: Mapping[str, str]) -> None:
        """Persist selected known credentials to the project .env without logging values."""
        env_path_value = self._values.get("__ENV_PATH__", "")
        if not env_path_value:
            raise RuntimeError(
                "CredentialStore: nao ha .env associado para persistir credenciais."
            )
        unknown = [key for key in updates if key not in KNOWN_KEYS]
        if unknown:
            raise RuntimeError(
                "CredentialStore: tentativa de persistir chave desconhecida: "
                + ", ".join(sorted(unknown))
            )
        _update_env_file(Path(env_path_value), updates)
        if isinstance(self._values, dict):
            for key, value in updates.items():
                clean = str(value).strip()
                if clean:
                    self._values[key] = clean
                else:
                    self._values.pop(key, None)

    def __repr__(self) -> str:
        present = sorted(
            key
            for key, value in self._values.items()
            if value and key != "__ENV_PATH__"
        )
        return f"CredentialStore(present_keys={present!r}, values=[REDACTED])"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise RuntimeError(f"Nao foi possivel ler {path}: {exc}") from exc
    for number, original in enumerate(lines, start=1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise RuntimeError(f"Linha invalida em {path} ({number}). Use NOME=valor.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise RuntimeError(f"Nome vazio em {path} ({number}).")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _update_env_file(path: Path, updates: Mapping[str, str]) -> None:
    """Update/append env keys while preserving unrelated lines and never printing values."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    except OSError as exc:
        raise RuntimeError(f"Nao foi possivel ler {path}: {exc}") from exc

    remaining = {key: str(value) for key, value in updates.items()}
    output: list[str] = []
    for original in lines:
        stripped = original.strip()
        candidate = stripped[7:].strip() if stripped.lower().startswith("export ") else stripped
        if candidate and not candidate.startswith("#") and "=" in candidate:
            key = candidate.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(original)
    for key, value in remaining.items():
        output.append(f"{key}={value}")
    try:
        path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Nao foi possivel atualizar {path}: {exc}") from exc
