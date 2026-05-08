from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

EXPECTED = {
    "1": "setosa",
    "2": "setosa",
    "3": "versicolor",
    "4": "versicolor",
    "5": "virginica",
    "6": "virginica",
}


def main() -> int:
    workspace = Path.cwd()
    result_path = workspace / "evaluator_result.json"
    run_py = workspace / "run.py"
    if not run_py.exists():
        write_result(result_path, valid=False, metric_value=0.0, diagnostics={"error": "missing run.py"})
        return 0
    proc = subprocess.run([sys.executable, "run.py"], cwd=workspace, text=True, capture_output=True, timeout=60)
    submission = workspace / "submission" / "predictions.csv"
    diagnostics = {"run_stdout": proc.stdout[-4000:], "run_stderr": proc.stderr[-4000:]}
    if proc.returncode != 0:
        diagnostics["error"] = "entrypoint_failed"
        diagnostics["exit_code"] = proc.returncode
        write_result(result_path, valid=False, metric_value=0.0, diagnostics=diagnostics)
        return 0
    if not submission.exists():
        diagnostics["error"] = "missing_submission"
        write_result(result_path, valid=False, metric_value=0.0, diagnostics=diagnostics)
        return 0
    rows = list(csv.DictReader(submission.open(newline="", encoding="utf-8")))
    predictions = {row.get("id", ""): row.get("species", "") for row in rows}
    correct = sum(1 for item_id, label in EXPECTED.items() if predictions.get(item_id) == label)
    diagnostics["rows"] = len(rows)
    diagnostics["correct"] = correct
    diagnostics["missing_ids"] = [item_id for item_id in EXPECTED if item_id not in predictions]
    write_result(
        result_path,
        valid=not diagnostics["missing_ids"],
        metric_value=correct / len(EXPECTED),
        diagnostics=diagnostics,
    )
    return 0


def write_result(path: Path, *, valid: bool, metric_value: float, diagnostics: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "metric_name": "accuracy",
                "metric_value": metric_value,
                "higher_is_better": True,
                "valid": valid,
                "diagnostics": diagnostics,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
