from pathlib import Path

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.models import AttemptStatus, ExperimentResult, TaskSpec
from codex_mle_harness.utils.git_utils import (
    commit_all,
    ensure_git_repo,
    get_changed_files,
    get_git_diff,
)


def test_task_manifest_loads_example():
    task = TaskSpec.from_manifest(
        Path("codex_mle_harness/examples/toy_threshold/task.yaml")
    )
    assert task.task_id == "toy_threshold"
    assert task.description_path.exists()
    assert task.evaluator_command == "python /task/evaluator.py"
    assert task.data_mounts[0].source.exists()
    assert task.primary_metric_name == "accuracy"


def test_store_append_list_best_and_resume(tmp_path):
    store = ExperimentStore(tmp_path / ".codex_mle_harness")
    a1 = ExperimentResult(
        task_id="task",
        work_order_id="wo1",
        branch_name="attempt/task/1",
        artifact_dir=tmp_path / "a1",
        workspace_path=tmp_path / "w1",
        status=AttemptStatus.SUCCESS,
        metric_name="accuracy",
        metric_value=0.5,
        higher_is_better=True,
    )
    a2 = ExperimentResult(
        task_id="task",
        work_order_id="wo2",
        branch_name="attempt/task/2",
        artifact_dir=tmp_path / "a2",
        workspace_path=tmp_path / "w2",
        status=AttemptStatus.RUNNING,
        metric_name="accuracy",
        higher_is_better=True,
    )
    store.append_attempt(a1)
    store.append_attempt(a2)
    assert store.get_best_experiment("task").attempt_id == a1.attempt_id
    assert store.mark_running_as_interrupted() == 1
    assert store.get_attempt(a2.attempt_id).status == AttemptStatus.INTERRUPTED


def test_git_utils_capture_untracked_files_and_patch(tmp_path):
    ensure_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("old\n", encoding="utf-8")
    commit_all(tmp_path, "baseline")
    (tmp_path / "tracked.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "created.py").write_text("print('hello')\n", encoding="utf-8")
    files = get_changed_files(tmp_path)
    diff = get_git_diff(tmp_path)
    assert "tracked.txt" in files
    assert "created.py" in files
    assert "new" in diff
    assert "created.py" in diff
