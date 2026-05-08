from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    workspace = Path.cwd()
    result_path = workspace / "evaluator_result.json"
    run_py = workspace / "run.py"
    if not run_py.exists():
        write_result(result_path, valid=False, metric_value=0.0, diagnostics={"error": "missing run.py"})
        return 0

    try:
        proc = subprocess.run(
            [sys.executable, "run.py"],
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        diagnostics = {
            "run_stdout": (exc.stdout or "")[-4000:],
            "run_stderr": (exc.stderr or "")[-4000:],
            "error": "entrypoint_timeout",
            "timeout_seconds": 300,
        }
        write_result(result_path, valid=False, metric_value=0.0, diagnostics=diagnostics)
        return 0
    diagnostics = {"run_stdout": proc.stdout[-4000:], "run_stderr": proc.stderr[-4000:]}
    if proc.returncode != 0:
        diagnostics["error"] = "entrypoint_failed"
        diagnostics["exit_code"] = proc.returncode
        write_result(result_path, valid=False, metric_value=0.0, diagnostics=diagnostics)
        return 0

    submission_path = workspace / "submission" / "submission.csv"
    gold_path = workspace / "input" / "private" / "gold_submission.csv"
    public_test_path = workspace / "input" / "public" / "test.csv"
    if not submission_path.exists():
        diagnostics["error"] = "missing_submission"
        write_result(result_path, valid=False, metric_value=0.0, diagnostics=diagnostics)
        return 0
    if not gold_path.exists():
        diagnostics["error"] = "missing_gold"
        write_result(result_path, valid=False, metric_value=0.0, diagnostics=diagnostics)
        return 0

    submission_rows = list(csv.DictReader(submission_path.open(newline="", encoding="utf-8")))
    gold_rows = list(csv.DictReader(gold_path.open(newline="", encoding="utf-8")))
    public_rows = list(csv.DictReader(public_test_path.open(newline="", encoding="utf-8")))
    diagnostics["submission_rows"] = len(submission_rows)
    diagnostics["gold_rows"] = len(gold_rows)
    diagnostics["public_test_rows"] = len(public_rows)
    if len(submission_rows) != len(gold_rows) or len(submission_rows) != len(public_rows):
        diagnostics["error"] = "row_count_mismatch"
        write_result(result_path, valid=False, metric_value=0.0, diagnostics=diagnostics)
        return 0

    correct = 0
    invalid_labels = 0
    for idx, (pred, gold, public) in enumerate(zip(submission_rows, gold_rows, public_rows)):
        if pred.get("Date", "") != public.get("Date", "") or pred.get("Comment", "") != public.get("Comment", ""):
            diagnostics["error"] = "row_order_or_content_mismatch"
            diagnostics["first_bad_row"] = idx
            write_result(result_path, valid=False, metric_value=0.0, diagnostics=diagnostics)
            return 0
        label = (pred.get("Insult") or "").strip()
        if label not in {"0", "1"}:
            invalid_labels += 1
            continue
        correct += int(label) == int(gold["Insult"])

    diagnostics["correct"] = correct
    diagnostics["invalid_labels"] = invalid_labels
    write_result(
        result_path,
        valid=invalid_labels == 0,
        metric_value=correct / len(gold_rows),
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
