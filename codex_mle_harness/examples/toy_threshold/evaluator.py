import csv
import json
import subprocess
import sys
from pathlib import Path


workspace = Path.cwd()
run_py = workspace / "run.py"
result = {
    "metric_name": "accuracy",
    "metric_value": None,
    "higher_is_better": True,
    "valid": False,
    "diagnostics": {},
    "extra_metrics": {},
}

if not run_py.exists():
    result["diagnostics"]["error"] = "run.py missing"
    Path("evaluator_result.json").write_text(json.dumps(result, indent=2))
    sys.exit(0)

proc = subprocess.run([sys.executable, "run.py"], text=True, capture_output=True, timeout=30)
result["diagnostics"]["entrypoint_stdout"] = proc.stdout[-2000:]
result["diagnostics"]["entrypoint_stderr"] = proc.stderr[-2000:]
if proc.returncode != 0:
    result["diagnostics"]["error"] = f"run.py exited with {proc.returncode}"
    Path("evaluator_result.json").write_text(json.dumps(result, indent=2))
    sys.exit(0)

submission = workspace / "submission" / "predictions.csv"
if not submission.exists():
    result["diagnostics"]["error"] = "submission/predictions.csv missing"
    Path("evaluator_result.json").write_text(json.dumps(result, indent=2))
    sys.exit(0)

expected = {0: 0, 1: 0, 2: 1, 3: 1, 4: 1}
with submission.open(newline="") as f:
    rows = list(csv.DictReader(f))
predictions = {}
for row in rows:
    predictions[int(row["id"])] = int(float(row["label"]))

correct = sum(1 for key, value in expected.items() if predictions.get(key) == value)
accuracy = correct / len(expected)
result["metric_value"] = accuracy
result["valid"] = set(predictions) == set(expected)
result["diagnostics"]["rows"] = len(rows)
Path("evaluator_result.json").write_text(json.dumps(result, indent=2))
