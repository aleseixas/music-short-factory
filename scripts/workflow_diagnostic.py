"""Write one stable diagnostic envelope, including when an earlier workflow step failed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re


def write_report(root: Path, *, stage: str, slug: str = "", request_id: str = "",
                 error_code: str = "", error_class: str = "", detail: str = "",
                 recoverable: bool = False, target: str = "", errors: list | None = None,
                 success: bool = False) -> dict:
    record = {"error_code": error_code, "error_class": error_class,
              "recoverable": recoverable, "stage": stage, "slug": slug,
              "request_id": request_id or os.environ.get("PIPELINE_REQUEST_ID", ""),
              "target": target, "detail": detail,
              "commit_sha": os.environ.get("GITHUB_SHA", ""),
              "run_id": os.environ.get("GITHUB_RUN_ID", ""),
              "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
              "status": "PASS" if success else "FAIL"}
    record["errors"] = errors if errors is not None else ([] if success else [{
        key: record[key] for key in ("error_code", "error_class", "recoverable", "target", "detail")
    }])
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{stage}-{slug}-{target}")
    folder = root / "ci-diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("PIPELINE_DIAGNOSTIC_JSON=" + json.dumps(record, ensure_ascii=False))
    return record


def main() -> int:
    stage = os.environ.get("PIPELINE_STAGE", os.environ.get("GITHUB_JOB", "workflow"))
    steps = json.loads(os.environ.get("PIPELINE_STEPS", "{}"))
    errors = [{"error_code": "WORKFLOW_STEP_FAILED", "error_class": "external_or_infrastructure",
               "recoverable": False, "stage": stage, "target": key,
               "detail": f"Step {key} concluded {value.get('outcome')}"}
              for key, value in steps.items() if value.get("outcome") in {"failure", "cancelled"}]
    success = os.environ.get("PIPELINE_JOB_STATUS") == "success"
    code = os.environ.get("PIPELINE_RESULT", "")
    if not success and not errors:
        errors.append({"error_code": "WORKFLOW_INTERRUPTED", "error_class": "external_or_infrastructure",
                       "recoverable": False, "target": stage,
                       "detail": "Job did not finish successfully; consult the stage diagnostic artifact"})
    write_report(Path.cwd(), stage=stage, slug=os.environ.get("PIPELINE_SLUG", ""),
                 error_code=code if success else "WORKFLOW_STEP_FAILED",
                 error_class="" if success else "external_or_infrastructure",
                 detail=code if success else "Inspect errors[] and stage diagnostics",
                 errors=errors, success=success)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
