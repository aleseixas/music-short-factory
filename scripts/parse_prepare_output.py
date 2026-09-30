"""Extract the final machine result from a prepare log without parsing log noise as JSON."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


def parse_prepare_output(output: str, slug: str, request_id: str) -> dict:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and ("commit_sha" in value or "next_action" in value):
            if (value.get("slug") != slug or value.get("request_id") != request_id
                    or value.get("next_action") != "wait_for_correlated_run"
                    or not re.fullmatch(r"[a-fA-F0-9]{40}", str(value.get("commit_sha", "")))):
                raise ValueError("PREPARE_RESULT_INVALID: wrong slug/request/SHA/action")
            return value
    raise ValueError("PREPARE_RESULT_MISSING: no final prepared result in stdout")


def main() -> int:
    result = parse_prepare_output(Path(sys.argv[1]).read_text(encoding="utf-8"), sys.argv[2], sys.argv[3])
    with Path(sys.argv[4]).open("a", encoding="utf-8") as handle:
        handle.write(f"source_sha={result['commit_sha']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
