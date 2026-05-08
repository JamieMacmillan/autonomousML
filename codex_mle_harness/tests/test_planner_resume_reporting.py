import json
import sys
from pathlib import Path

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.models import (
    AttemptStatus,
    ContainerMetadata,
    EvaluationStatus,
    EvaluatorResult,
    ExperimentResult,
    FailureClass,
    PlannerIdea,
    TaskSpec,
)
from codex_mle_harness.core.work_order import create_work_order, write_work_order_files
from codex_mle_harness.planning.planner import MLMasterPlannerAdapter, OpenAICompatiblePlanner
from codex_mle_harness.reporting import render_task_report
from codex_mle_harness.runner import HarnessRunner
from codex_mle_harness.utils.git_utils import ensure_git_repo
from codex_mle_harness.workers.codex_worker import CodexWorker


def _toy_task() -> TaskSpec:
    return TaskSpec.from_manifest(Path("codex_mle_harness/examples/toy_threshold/task.yaml"))


def test_planner_validation_repairs_and_retries(tmp_path):
    task = _toy_task()
    store = ExperimentStore(tmp_path / ".harness")
    calls = []

    def completion(_system: str, user: str) -> str:
        calls.append(user)
        if len(calls) == 1:
            return "not json"
        return json.dumps(
            {
                "ideas": [
                    {
                        "operator": "unknown",
                        "objective": "Write a baseline",
                        "hypothesis": "Baseline should be valid",
                        "rationale": "Repair pass",
                        "novelty_key": "baseline",
                        "strategy_tags": ["repair"],
                    }
                ]
            }
        )

    planner = OpenAICompatiblePlanner(
        api_key_env="NO_KEY_NEEDED",
        model="fake",
        completion_fn=completion,
    )
    ideas = planner.propose(task=task, store=store, round_index=0, limit=1)
    assert ideas[0].objective == "Write a baseline"
    assert ideas[0].operator == "draft"
    validations = store.list_planner_validations(task.task_id)
    assert len(validations) == 2
    assert validations[0].errors
    assert validations[1].repaired is True


def test_ml_master_planner_adapter_uses_surviving_prompts(tmp_path):
    task = _toy_task()
    store = ExperimentStore(tmp_path / ".harness")

    def completion(_system: str, user: str) -> str:
        if "Current Research Plan" in user:
            return "Amplify threshold baselines; avoid output-format mistakes."
        assert "Competition Information" in user
        return json.dumps({"threshold direction": {"1": "Use the sign of x as the label"}})

    planner = MLMasterPlannerAdapter(completion_fn=completion)
    ideas = planner.propose(task=task, store=store, round_index=0, limit=2)
    assert ideas
    assert ideas[0].operator == "improve"
    assert "threshold direction" in ideas[0].objective
    promotion = planner.promote(task=task, store=store, round_index=0, attempts=[])
    assert "threshold" in promotion


def test_codex_worker_missing_command_has_stable_failure_class(tmp_path):
    task = _toy_task()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ensure_git_repo(workspace)
    work_order = create_work_order(task, objective="Create a baseline")
    write_work_order_files(work_order, task, workspace)
    result = CodexWorker(command_prefix=[str(tmp_path / "missing-codex")]).run(
        work_order, task, workspace
    )
    assert result.status.value == "failed"
    assert result.failure_class == FailureClass.CODEX_CLI_MISSING.value


def test_codex_worker_goal_mode_writes_goal_file(tmp_path):
    task = _toy_task()
    task.implementation_worker.mode = "goal"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ensure_git_repo(workspace)
    work_order = create_work_order(task, objective="Create a baseline")
    code = "from pathlib import Path; import sys; print(Path(sys.argv[1]).name)"
    result = CodexWorker(command_prefix=[sys.executable, "-c", code], mode="goal").run(
        work_order, task, workspace
    )
    assert result.status.value == "success"
    assert (workspace / ".goal.md").exists()
    assert ".goal.md" in result.stdout


class _FakeDockerResult:
    def __init__(self):
        self.metadata = ContainerMetadata(
            image="fake",
            command="fake",
            container_name="fake",
        )


class _FakeEvaluator:
    def run(self, *, attempt_id: str, task: TaskSpec, workspace: Path):
        (workspace / "submission").mkdir(exist_ok=True)
        (workspace / "submission" / "predictions.csv").write_text("id,label\n1,1\n", encoding="utf-8")
        return (
            EvaluatorResult(
                attempt_id=attempt_id,
                status=EvaluationStatus.SUCCESS,
                metric_name=task.primary_metric_name,
                metric_value=0.75,
                higher_is_better=task.higher_is_better,
                valid=True,
            ),
            _FakeDockerResult(),
        )


def test_resume_recovers_partial_evaluation(tmp_path):
    task = _toy_task()
    task.stop_conditions.max_attempts = 1
    store = ExperimentStore(tmp_path / ".harness")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact_dir = store.artifact_dir("attempt_partial")
    attempt = ExperimentResult(
        attempt_id="attempt_partial",
        task_id=task.task_id,
        work_order_id="wo_partial",
        branch_name="attempt/toy/partial",
        artifact_dir=artifact_dir,
        workspace_path=workspace,
        status=AttemptStatus.IMPLEMENTATION_COMPLETED,
        metric_name=task.primary_metric_name,
        higher_is_better=task.higher_is_better,
    )
    store.append_attempt(attempt)
    runner = HarnessRunner(
        workspace=tmp_path,
        store=store,
        evaluator=_FakeEvaluator(),
        worker=CodexWorker(command_prefix=[sys.executable, "-c", ""]),
    )
    recovered = runner._recover_partial_evaluations(task)
    assert recovered[0].status == AttemptStatus.SUCCESS
    assert store.get_attempt("attempt_partial").metric_value == 0.75


def test_resume_marks_running_but_not_partial_evaluation_interrupted(tmp_path):
    store = ExperimentStore(tmp_path / ".harness")
    running = ExperimentResult(
        task_id="task",
        work_order_id="wo_running",
        branch_name="attempt/task/running",
        artifact_dir=tmp_path / "a",
        workspace_path=tmp_path / "w",
        status=AttemptStatus.RUNNING,
    )
    partial = ExperimentResult(
        task_id="task",
        work_order_id="wo_partial",
        branch_name="attempt/task/partial",
        artifact_dir=tmp_path / "b",
        workspace_path=tmp_path / "x",
        status=AttemptStatus.IMPLEMENTATION_COMPLETED,
    )
    store.append_attempt(running)
    store.append_attempt(partial)
    assert store.mark_running_as_interrupted() == 1
    assert store.get_attempt(running.attempt_id).status == AttemptStatus.INTERRUPTED
    assert store.get_attempt(partial.attempt_id).status == AttemptStatus.IMPLEMENTATION_COMPLETED


def test_report_includes_failures_and_promotions(tmp_path):
    store = ExperimentStore(tmp_path / ".harness")
    attempt = ExperimentResult(
        task_id="task",
        work_order_id="wo",
        branch_name="attempt/task/fail",
        artifact_dir=tmp_path / "a",
        workspace_path=tmp_path / "w",
        status=AttemptStatus.FAILED,
        failure_class=FailureClass.EVALUATOR_FAILED.value,
    )
    store.append_attempt(attempt)
    report = render_task_report(store, "task")
    assert "evaluator_failed" in report
    assert "Attempts: 1" in report
