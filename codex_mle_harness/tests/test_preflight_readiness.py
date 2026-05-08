import textwrap
from pathlib import Path

from codex_mle_harness.core.models import TaskSpec
from codex_mle_harness.preflight import validate_task_preflight
from codex_mle_harness.runner import HarnessRunner


def _toy_task() -> TaskSpec:
    return TaskSpec.from_manifest(Path("codex_mle_harness/examples/toy_threshold/task.yaml"))


def test_preflight_accepts_example_without_runtime_checks():
    report = validate_task_preflight(_toy_task(), check_runtime=False)
    assert report.ok
    assert not report.errors


def test_preflight_reports_missing_data_mount(tmp_path):
    task = _toy_task()
    task.data_mounts[0].source = tmp_path / "missing"
    report = validate_task_preflight(task, check_runtime=False)
    assert not report.ok
    assert any(check.code == "data_mount_missing" for check in report.errors)


def test_preflight_reports_support_file_collision(tmp_path):
    task_dir = tmp_path / "task"
    external_a = tmp_path / "a" / "helper.py"
    external_b = tmp_path / "b" / "helper.py"
    task_dir.mkdir()
    external_a.parent.mkdir()
    external_b.parent.mkdir()
    (task_dir / "description.md").write_text("Predict labels.\n", encoding="utf-8")
    (task_dir / "data").mkdir()
    (task_dir / "evaluator.py").write_text("print('ok')\n", encoding="utf-8")
    external_a.write_text("A = 1\n", encoding="utf-8")
    external_b.write_text("B = 1\n", encoding="utf-8")
    (task_dir / "task.yaml").write_text(
        textwrap.dedent(
            f"""
            task_id: collision_task
            description_path: description.md
            data_mounts:
              - source: data
                target: input
            support_files:
              - {external_a}
              - {external_b}
            required_outputs:
              - submission/predictions.csv
            evaluator_command: python /task/evaluator.py
            primary_metric_name: accuracy
            higher_is_better: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    task = TaskSpec.from_manifest(task_dir / "task.yaml")
    report = validate_task_preflight(task, check_runtime=False)
    assert not report.ok
    assert any(check.code == "support_file_destination_collision" for check in report.errors)


def test_prepare_workspace_preserves_manifest_relative_support_paths(tmp_path):
    task_dir = tmp_path / "task"
    nested = task_dir / "starter" / "helpers"
    task_dir.mkdir()
    nested.mkdir(parents=True)
    (task_dir / "description.md").write_text("Predict labels.\n", encoding="utf-8")
    (task_dir / "data").mkdir()
    (task_dir / "evaluator.py").write_text("print('ok')\n", encoding="utf-8")
    (nested / "features.py").write_text("FEATURES = []\n", encoding="utf-8")
    (task_dir / "task.yaml").write_text(
        textwrap.dedent(
            """
            task_id: nested_support_task
            description_path: description.md
            data_mounts:
              - source: data
                target: input
            support_files:
              - starter/helpers/features.py
            required_outputs:
              - submission/predictions.csv
            evaluator_command: python /task/evaluator.py
            primary_metric_name: accuracy
            higher_is_better: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    task = TaskSpec.from_manifest(task_dir / "task.yaml")
    workspace = tmp_path / "workspace"
    HarnessRunner(workspace=tmp_path / "runs")._prepare_workspace(task, workspace)
    assert (workspace / "starter" / "helpers" / "features.py").exists()
    assert not (workspace / "features.py").exists()
