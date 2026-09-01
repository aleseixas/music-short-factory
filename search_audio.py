from pathlib import Path

from engine.audio_search import main


if __name__ == "__main__":
    raise SystemExit(main(default_project_root=Path(__file__).resolve().parent))
