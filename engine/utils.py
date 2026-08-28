from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = 1
SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Arquivo obrigatorio ausente: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"JSON invalido em {path} (linha {exc.lineno}, coluna {exc.colno}): {exc.msg}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"Nao foi possivel ler {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"O arquivo {path} precisa conter um objeto JSON.")
    return data


def validate_schema(data: dict[str, Any], path: Path) -> None:
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise RuntimeError(
            f"schema_version invalido em {path}: esperado {SCHEMA_VERSION}, recebido {version!r}."
        )


def validate_slug(value: str, label: str = "episodio") -> str:
    slug = value.strip()
    if not SLUG_PATTERN.fullmatch(slug):
        raise RuntimeError(
            f"Nome de {label} invalido: {value!r}. Use letras minusculas, numeros, '_' ou '-'."
        )
    return slug


def safe_child(parent: Path, name: str) -> Path:
    parent = parent.resolve()
    child = (parent / name).resolve()
    if child.parent != parent:
        raise RuntimeError(f"Caminho fora da pasta permitida: {child}")
    return child
