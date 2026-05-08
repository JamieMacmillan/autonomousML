import sys
from pathlib import Path

import pytest

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.models import AttemptStatus, EvaluationStatus, ExperimentResult, PlannerIdea, TaskSpec
from codex_mle_harness.core.scheduler import DiverseBeamScheduler
from codex_mle_harness.evaluation.evaluator import Evaluator
from codex_mle_harness.execution.docker_runner import DockerRunner
from codex_mle_harness.planning.planner import StaticPlanner
from codex_mle_harness.runner import HarnessRunner
from codex_mle_harness.workers.codex_worker import CodexWorker


def _task() -> TaskSpec:
    return TaskSpec.from_manifest(
        Path("codex_mle_harness/examples/toy_threshold/task.yaml")
    )


def test_scheduler_keeps_fresh_draft_slot(tmp_path):
    task = _task()
    store = ExperimentStore(tmp_path / ".harness")
    best = ExperimentResult(
        task_id=task.task_id,
        work_order_id="wo",
        branch_name="attempt/toy/best",
        artifact_dir=tmp_path / "a",
        workspace_path=tmp_path / "w",
        status=AttemptStatus.SUCCESS,
        metric_name="accuracy",
        metric_value=0.8,
        higher_is_better=True,
    )
    store.append_attempt(best)
    ideas = [
        PlannerIdea(operator="improve", parent_attempt_id=best.attempt_id, objective="exploit best"),
        PlannerIdea(operator="fresh_draft", objective="try a different approach", novelty_key="fresh"),
    ]
    selected = DiverseBeamScheduler().select_ideas(
        task=task, store=store, planner_ideas=ideas, round_index=0
    )
    assert {idea.operator for idea in selected} == {"improve"}
    task.scheduler.round_size = 2
    selected = DiverseBeamScheduler().select_ideas(
        task=task, store=store, planner_ideas=ideas, round_index=0
    )
    assert {idea.operator for idea in selected} == {"improve", "fresh_draft"}


def test_evaluator_runs_in_docker(tmp_path):
    task = _task()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    input_dir = workspace / "input"
    input_dir.mkdir()
    for source in task.data_mounts[0].source.iterdir():
        (input_dir / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (workspace / "run.py").write_text(
        "import csv, os\n"
        "os.makedirs('submission', exist_ok=True)\n"
        "rows=list(csv.DictReader(open('input/test.csv')))\n"
        "with open('submission/predictions.csv','w',newline='') as f:\n"
        " w=csv.DictWriter(f, fieldnames=['id','label']); w.writeheader()\n"
        " [w.writerow({'id': r['id'], 'label': int(float(r['x']) >= 0)}) for r in rows]\n",
        encoding="utf-8",
    )
    result, docker_result = Evaluator().run(attempt_id="attempt_test", task=task, workspace=workspace)
    assert docker_result.exit_code == 0
    assert result.valid is True
    assert result.metric_value == 1.0


@pytest.mark.docker
def test_docker_env_allowlist_controls_secret_passthrough(tmp_path, monkeypatch):
    task = _task()
    task.environment.pass_all = False
    task.environment.allowlist = ["VISIBLE_SECRET"]
    monkeypatch.setenv("VISIBLE_SECRET", "yes")
    monkeypatch.setenv("HIDDEN_SECRET", "no")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = DockerRunner().run(
        task=task,
        workspace=workspace,
        command=(
            "python - <<'PY'\n"
            "import os\n"
            "assert os.environ.get('VISIBLE_SECRET') == 'yes'\n"
            "assert 'HIDDEN_SECRET' not in os.environ\n"
            "PY"
        ),
        timeout_seconds=60,
    )
    assert result.exit_code == 0
    assert result.metadata.environment_count == 1


@pytest.mark.docker
def test_dependency_install_failure_is_classified(tmp_path):
    task = _task()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "requirements.txt").write_text("not a valid requirement @@@\n", encoding="utf-8")
    result, _docker_result = Evaluator().run(attempt_id="attempt_dep", task=task, workspace=workspace)
    assert result.status == EvaluationStatus.FAILED
    assert result.failure_class == "dependency_install_failed"
    assert result.diagnostics["dependency_install_exit_code"] != 0


def test_runner_records_fake_codex_attempt(tmp_path):
    task = _task()
    task.stop_conditions.max_attempts = 1
    code = (
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
    worker = CodexWorker(command_prefix=[sys.executable, "-c", code], capture_json=False)
    runner = HarnessRunner(
        workspace=tmp_path,
        planner=StaticPlanner(),
        worker=worker,
    )
    best = runner.run_task(task)
    assert best is not None
    assert best.status == AttemptStatus.SUCCESS
    assert best.metric_value == 1.0
    assert (best.artifact_dir / "implementation_result.json").exists()
    assert (best.artifact_dir / "evaluator_result.json").exists()
    assert (best.artifact_dir / "dependency_install_exit_code.txt").exists()
    assert (best.artifact_dir / "attempt_summary.json").exists()
    assert best.breakthrough is True
    adoption = runner.adopt_best(task.task_id)
    assert adoption.attempt_id == best.attempt_id
    assert adoption.adopted_branch == f"best/{task.task_id}"


def test_iris_benchmark_fixture_runs_with_fake_worker(tmp_path):
    task = TaskSpec.from_manifest(
        Path("codex_mle_harness/examples/iris_classification/task.yaml")
    )
    task.stop_conditions.max_attempts = 1
    code = (
        "from pathlib import Path\n"
        "Path('run.py').write_text(\"\"\"import csv, math, os\\n"
        "os.makedirs('submission', exist_ok=True)\\n"
        "train=list(csv.DictReader(open('input/train.csv')))\\n"
        "test=list(csv.DictReader(open('input/test.csv')))\\n"
        "features=['sepal_length','sepal_width','petal_length','petal_width']\\n"
        "centroids={}\\n"
        "for label in sorted({r['species'] for r in train}):\\n"
        " rows=[r for r in train if r['species']==label]\\n"
        " centroids[label]=[sum(float(r[f]) for r in rows)/len(rows) for f in features]\\n"
        "with open('submission/predictions.csv','w',newline='') as f:\\n"
        " w=csv.DictWriter(f, fieldnames=['id','species']); w.writeheader()\\n"
        " for row in test:\\n"
        "  x=[float(row[f]) for f in features]\\n"
        "  pred=min(centroids, key=lambda c: sum((a-b)**2 for a,b in zip(x, centroids[c])))\\n"
        "  w.writerow({'id': row['id'], 'species': pred})\\n"
        "\"\"\")\n"
    )
    worker = CodexWorker(command_prefix=[sys.executable, "-c", code], capture_json=False)
    runner = HarnessRunner(workspace=tmp_path, planner=StaticPlanner(), worker=worker)
    best = runner.run_task(task)
    assert best is not None
    assert best.status == AttemptStatus.SUCCESS
    assert best.metric_value == 1.0


def test_insults_mlebench_fixture_runs_with_fake_worker(tmp_path):
    task = TaskSpec.from_manifest(
        Path("codex_mle_harness/examples/insults_mlebench/task.yaml")
    )
    task.stop_conditions.max_attempts = 1
    task.setup_command = None
    code = (
        "from pathlib import Path\n"
        "Path('run.py').write_text(\"\"\"import csv, os\\n"
        "os.makedirs('submission', exist_ok=True)\\n"
        "rows=list(csv.DictReader(open('input/public/sample_submission_null.csv')))\\n"
        "with open('submission/submission.csv','w',newline='',encoding='utf-8') as f:\\n"
        " w=csv.DictWriter(f, fieldnames=['Insult','Date','Comment']); w.writeheader()\\n"
        " for row in rows:\\n"
        "  text=row['Comment'].lower()\\n"
        "  label=int(any(word in text for word in ['fuck','idiot','stupid','retarded','faggot','suck']))\\n"
        "  w.writerow({'Insult': label, 'Date': row['Date'], 'Comment': row['Comment']})\\n"
        "\"\"\")\n"
    )
    worker = CodexWorker(command_prefix=[sys.executable, "-c", code], capture_json=False)
    runner = HarnessRunner(workspace=tmp_path, planner=StaticPlanner(), worker=worker)
    best = runner.run_task(task)
    assert best is not None
    assert best.status == AttemptStatus.SUCCESS
    assert best.metric_value is not None
    assert best.metric_value > 0.6
