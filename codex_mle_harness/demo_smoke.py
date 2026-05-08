"""Create a deterministic demo artifact bundle without live Codex access."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from codex_mle_harness.core.models import TaskSpec
from codex_mle_harness.planning.planner import StaticPlanner
from codex_mle_harness.reporting import write_task_report
from codex_mle_harness.runner import HarnessRunner
from codex_mle_harness.workers.codex_worker import CodexWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a deterministic harness smoke demo")
    parser.add_argument(
        "--workspace",
        default="runs/work_demo_smoke",
        help="Output workspace for smoke-demo state and artifacts",
    )
    parser.add_argument(
        "--task",
        default="codex_mle_harness/examples/toy_threshold/task.yaml",
        help="Task manifest to run; defaults to the toy threshold fixture",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the smoke-demo workspace before running",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if args.clean and workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    task = TaskSpec.from_manifest(Path(args.task))
    task.stop_conditions.max_attempts = 1
    task.planner.type = "static"

    runner = HarnessRunner(
        workspace=workspace,
        planner=StaticPlanner(),
        worker=CodexWorker(command_prefix=[sys.executable, "-c", _toy_worker_code()], capture_json=False),
    )
    best = runner.run_task(task)
    report_path = write_task_report(runner.store, task.task_id, workspace / "report.md")
    best_patch = None
    if best is not None:
        runner.adopt_best(task.task_id, notes="Deterministic smoke-demo best candidate")
        patch_source = best.artifact_dir / "implementation.patch"
        if patch_source.exists():
            best_patch = workspace / "best.patch"
            shutil.copy2(patch_source, best_patch)

    print(f"Workspace: {workspace}")
    print(f"Report: {report_path}")
    if best is None:
        print("Best attempt: none")
        raise SystemExit(1)
    print(f"Best attempt: {best.attempt_id}")
    print(f"Metric: {best.metric_name}={best.metric_value}")
    print(f"Artifacts: {best.artifact_dir}")
    if best_patch is not None:
        print(f"Best patch: {best_patch}")


def _toy_worker_code() -> str:
    return (
        "from pathlib import Path\n"
        "Path('requirements.txt').write_text('')\n"
        "Path('run.py').write_text(\"\"\"import csv, os\\n"
        "os.makedirs('submission', exist_ok=True)\\n"
        "rows=list(csv.DictReader(open('input/test.csv')))\\n"
        "with open('submission/predictions.csv','w',newline='') as f:\\n"
        " w=csv.DictWriter(f, fieldnames=['id','label']); w.writeheader()\\n"
        " [w.writerow({'id': r['id'], 'label': int(float(r['x']) >= 0)}) for r in rows]\\n"
        "\"\"\")\n"
    )


if __name__ == "__main__":
    main()
