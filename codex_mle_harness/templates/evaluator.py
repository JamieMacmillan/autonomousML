from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


METRIC_NAME = "accuracy"
HIGHER_IS_BETTER = True
ENTRYPOINT_TIMEOUT_SECONDS = 300
SUBMISSION_PATH = Path("submission/predictions.csv")
LABELS_PATH = Path("input/labels.csv")
RESULT_PATH = Path("evaluator_result.json")


def main() -> int:
    result = {
        "metric_name": METRIC_NAME,
        "metric_value": None,
        "higher_is_better": HIGHER_IS_BETTER,
        "valid": False,
        "diagnostics": {},
        "extra_metrics": {},
    }

    run_py = Path("run.py")
    if not run_py.exists():
        result["diagnostics"]["error"] = "missing_entrypoint"
        write_result(result)
        return 0

    try:
        proc = subprocess.run(
            [sys.executable, "run.py"],
            text=True,
            capture_output=True,
            timeout=ENTRYPOINT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result["diagnostics"].update(
            {
                "error": "entrypoint_timeout",
                "entrypoint_stdout": (exc.stdout or "")[-4000:],
                "entrypoint_stderr": (exc.stderr or "")[-4000:],
            }
        )
        write_result(result)
        return 0

    result["diagnostics"]["entrypoint_stdout"] = proc.stdout[-4000:]
    result["diagnostics"]["entrypoint_stderr"] = proc.stderr[-4000:]
    if proc.returncode != 0:
        result["diagnostics"]["error"] = "entrypoint_failed"
        result["diagnostics"]["exit_code"] = proc.returncode
        write_result(result)
        return 0

    if not SUBMISSION_PATH.exists():
        result["diagnostics"]["error"] = "missing_submission"
        write_result(result)
        return 0
    if not LABELS_PATH.exists():
        result["diagnostics"]["error"] = "missing_labels"
        write_result(result)
        return 0

    try:
        metric_value, diagnostics = score_submission(SUBMISSION_PATH, LABELS_PATH)
    except Exception as exc:
        result["diagnostics"]["error"] = "scoring_failed"
        result["diagnostics"]["exception"] = f"{type(exc).__name__}: {exc}"
        write_result(result)
        return 0

    result["metric_value"] = metric_value
    result["valid"] = diagnostics.pop("valid", True)
    result["diagnostics"].update(diagnostics)
    write_result(result)
    return 0


def score_submission(submission_path: Path, labels_path: Path) -> tuple[float, dict]:
    """Replace this function for non-CSV or non-accuracy tasks."""

    labels = {
        row["id"]: row["label"]
        for row in csv.DictReader(labels_path.open(newline="", encoding="utf-8"))
    }
    predictions = {
        row["id"]: row["label"]
        for row in csv.DictReader(submission_path.open(newline="", encoding="utf-8"))
    }
    missing_ids = sorted(set(labels) - set(predictions))
    extra_ids = sorted(set(predictions) - set(labels))
    correct = sum(1 for item_id, label in labels.items() if predictions.get(item_id) == label)
    denominator = max(len(labels), 1)
    return correct / denominator, {
        "valid": not missing_ids and not extra_ids,
        "label_count": len(labels),
        "prediction_count": len(predictions),
        "correct": correct,
        "missing_ids": missing_ids[:50],
        "extra_ids": extra_ids[:50],
    }


def write_result(result: dict) -> None:
    RESULT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
