from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.utils import safe_child, validate_slug
from engine.visual_usage import (
    VISUAL_USAGE_FILE,
    load_and_validate_visual_usage,
)


MAX_PUSH_ATTEMPTS = 3
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


class VisualUsagePersistenceError(RuntimeError):
    """Credential-free failure raised by the isolated persistence workflow."""


def persist_visual_usage(
    project_root: Path,
    episode: str,
    *,
    attempts: int = MAX_PUSH_ATTEMPTS,
) -> bool:
    """Commit only one validated visual_usage.json on top of current origin/main."""
    root = Path(project_root).resolve()
    slug = validate_slug(episode)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise VisualUsagePersistenceError("Numero de tentativas de persistencia invalido.")
    git_root = Path(_git_output(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if git_root != root:
        raise VisualUsagePersistenceError("O project root nao e a raiz do repositorio Git.")

    episode_dir = safe_child(root / "episodes", slug)
    source = safe_child(episode_dir, VISUAL_USAGE_FILE)
    payload = load_and_validate_visual_usage(source, slug)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    relative = Path("episodes") / slug / VISUAL_USAGE_FILE

    for attempt in range(1, attempts + 1):
        _git(root, "fetch", "--quiet", "origin", "main")
        if _persist_once(root, relative, serialized, slug):
            return True
        if attempt < attempts:
            time.sleep(min(2.0, 0.5 * attempt))
    raise VisualUsagePersistenceError(
        f"Push normal nao concluiu apos {attempts} tentativa(s)."
    )


def _persist_once(root: Path, relative: Path, serialized: str, slug: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="msf-visual-usage-") as temporary:
        temporary_root = Path(temporary).resolve()
        worktree = temporary_root / "main"
        try:
            _git(root, "worktree", "add", "--quiet", "--detach", worktree, "origin/main")
            destination = (worktree / relative).resolve()
            try:
                destination.relative_to(worktree)
            except ValueError as exc:
                raise VisualUsagePersistenceError(
                    "Destino do historico visual ficou fora do worktree."
                ) from exc
            if not destination.parent.is_dir():
                raise VisualUsagePersistenceError(
                    "O episodio nao existe na origin/main atual."
                )
            if destination.is_file():
                try:
                    current = load_and_validate_visual_usage(destination, slug)
                except RuntimeError:
                    current = None
                if current is not None and _recorded_at(current) >= _recorded_at_json(
                    serialized
                ):
                    print(
                        f"Visual usage remoto de {slug} ja e igual ou mais recente."
                    )
                    return True
            destination.write_text(serialized, encoding="utf-8")

            changed = _git_output(
                worktree,
                "status",
                "--porcelain",
                "--untracked-files=all",
            ).splitlines()
            expected_suffix = relative.as_posix()
            if not changed:
                print(f"Visual usage ja esta atualizado para {slug}.")
                return True
            if len(changed) != 1 or not changed[0][3:].replace("\\", "/").endswith(
                expected_suffix
            ):
                raise VisualUsagePersistenceError(
                    "Worktree temporario contem alteracoes fora de visual_usage.json."
                )

            _git(worktree, "add", "--", expected_suffix)
            staged = _git_output(
                worktree,
                "diff",
                "--cached",
                "--name-only",
                "--",
            ).splitlines()
            if staged != [expected_suffix]:
                raise VisualUsagePersistenceError(
                    "Escopo staged invalido; somente visual_usage.json e permitido."
                )
            _git(
                worktree,
                "-c",
                f"user.name={BOT_NAME}",
                "-c",
                f"user.email={BOT_EMAIL}",
                "commit",
                "--quiet",
                "-m",
                f"Record visual usage for {slug}",
            )
            pushed = _git_result(
                worktree,
                "push",
                "--quiet",
                "origin",
                "HEAD:refs/heads/main",
            )
            if pushed.returncode == 0:
                print(f"Visual usage persistido na main para {slug}.")
                return True
            return False
        finally:
            if worktree.exists():
                removed = _git_result(root, "worktree", "remove", "--force", worktree)
                if removed.returncode != 0:
                    # The enclosing TemporaryDirectory still owns this exact path.
                    shutil.rmtree(worktree, ignore_errors=True)
                    _git_result(root, "worktree", "prune")


def _recorded_at(payload: dict[str, object]) -> datetime:
    raw = str(payload["recorded_at"])
    return datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)


def _recorded_at_json(serialized: str) -> datetime:
    payload = json.loads(serialized)
    if not isinstance(payload, dict):
        raise VisualUsagePersistenceError("Payload de historico visual invalido.")
    return _recorded_at(payload)


def _git(root: Path, *arguments: object) -> None:
    result = _git_result(root, *arguments)
    if result.returncode != 0:
        raise VisualUsagePersistenceError(
            f"Comando Git falhou: {str(arguments[0]) if arguments else 'git'}."
        )


def _git_output(root: Path, *arguments: object) -> str:
    result = _git_result(root, *arguments)
    if result.returncode != 0:
        raise VisualUsagePersistenceError(
            f"Comando Git falhou: {str(arguments[0]) if arguments else 'git'}."
        )
    return result.stdout


def _git_result(root: Path, *arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *[str(value) for value in arguments]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main(
    argv: list[str] | None = None,
    *,
    project_root: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Persiste somente o historico visual validado na origin/main."
    )
    parser.add_argument("episode", help="Slug do episodio renderizado")
    args = parser.parse_args(argv)
    try:
        persist_visual_usage(project_root or PROJECT_ROOT, args.episode)
    except Exception as exc:
        # This step is intentionally advisory. Do not leak stderr from Git, remote
        # URLs, credentials or media URLs into the workflow log.
        print(
            "::warning::Visual usage nao foi persistido "
            f"({type(exc).__name__}); publicacao continuara."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
