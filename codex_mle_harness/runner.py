"""Production harness runner."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.attempt_summary import build_attempt_summary
from codex_mle_harness.core.failures import (
    classify_evaluator,
    classify_implementation,
    missing_required_outputs,
)
from codex_mle_harness.core.models import (
    AdoptionRecord,
    AttemptStatus,
    EvaluationStatus,
    ExperimentResult,
    FailureClass,
    ImplementationStatus,
    PlannerIdea,
    PromotionRecord,
    SearchNode,
    TaskSpec,
    new_id,
    utc_now,
)
from codex_mle_harness.core.scheduler import DiverseBeamScheduler
from codex_mle_harness.core.work_order import create_work_order, write_work_order_files
from codex_mle_harness.evaluation.evaluator import Evaluator
from codex_mle_harness.planning.planner import Planner, planner_from_task
from codex_mle_harness.utils.git_utils import (
    commit_all,
    ensure_git_repo,
    force_branch,
    get_head_commit,
    prepare_worktree,
    save_patch,
)
from codex_mle_harness.utils.task_files import (
    is_reserved_workspace_path,
    support_file_destination,
    workspace_relative_path,
)
from codex_mle_harness.workers.codex_worker import CodexWorker


class HarnessRunner:
    """Coordinates planning, Codex implementation, Docker evaluation, and recording."""

    def __init__(
        self,
        *,
        workspace: Path,
        store: ExperimentStore | None = None,
        planner: Planner | None = None,
        worker: CodexWorker | None = None,
        evaluator: Evaluator | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.harness_dir = self.workspace / ".codex_mle_harness"
        self.store = store or ExperimentStore(self.harness_dir)
        self.planner = planner
        self.worker = worker or CodexWorker()
        self.evaluator = evaluator or Evaluator()
        self.scheduler = DiverseBeamScheduler()

    def init(self) -> None:
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        self.store.mark_running_as_interrupted()

    def run_task(self, task: TaskSpec) -> ExperimentResult | None:
        """Run the sequential planner/worker/evaluator loop."""

        self.init()
        self.store.upsert_task(task)
        self._recover_partial_evaluations(task)
        planner = self.planner or planner_from_task(task)
        started = time.monotonic()
        round_index = len(self.store.list_promotions(task.task_id))
        while not self.scheduler.should_stop(
            task=task,
            store=self.store,
            started_at_monotonic=started,
            now_monotonic=time.monotonic(),
        ):
            ideas = planner.propose(
                task=task,
                store=self.store,
                round_index=round_index,
                limit=max(task.scheduler.round_size, task.scheduler.beam_width),
            )
            selected = self.scheduler.select_ideas(
                task=task,
                store=self.store,
                planner_ideas=ideas,
                round_index=round_index,
            )
            if not selected:
                break
            round_attempts: list[ExperimentResult] = []
            for idea in selected:
                if self.scheduler.should_stop(
                    task=task,
                    store=self.store,
                    started_at_monotonic=started,
                    now_monotonic=time.monotonic(),
                ):
                    break
                round_attempts.append(self.run_attempt(task, idea))
            self._promote_round(planner, task, round_index, round_attempts)
            round_index += 1
        return self.store.get_best_experiment(task.task_id)

    def resume_task(self, task: TaskSpec) -> ExperimentResult | None:
        """Recover interrupted state then continue the sequential loop."""

        return self.run_task(task)

    def run_attempt(self, task: TaskSpec, idea: PlannerIdea) -> ExperimentResult:
        attempt_id = new_id("attempt")
        parent = self.store.get_attempt(idea.parent_attempt_id) if idea.parent_attempt_id else None
        parent_ref = parent.commit_sha if parent and parent.commit_sha else "HEAD"
        branch_name = f"attempt/{task.task_id}/{attempt_id}"
        workspace = self.store.workspace_dir(attempt_id)
        repo = self.store.task_git_repo(task.task_id)
        ensure_git_repo(repo)
        actual_branch = prepare_worktree(repo, workspace, branch_name, parent_ref=parent_ref)
        artifact_dir = self.store.artifact_dir(attempt_id)
        self._prepare_workspace(task, workspace)
        work_order = create_work_order(
            task,
            objective=idea.objective,
            operator=idea.operator,
            hypothesis=idea.hypothesis,
            parent_attempt_id=idea.parent_attempt_id,
            parent_branch=parent.branch_name if parent else None,
            strategy_tags=idea.strategy_tags,
            attempt_index=len(self.store.list_attempts(task.task_id)),
        )
        self.store.append_work_order(work_order)
        write_work_order_files(work_order, task, workspace)
        setup_commit = commit_all(workspace, "harness setup for attempt")
        attempt = ExperimentResult(
            attempt_id=attempt_id,
            task_id=task.task_id,
            work_order_id=work_order.work_order_id,
            parent_attempt_id=idea.parent_attempt_id,
            branch_name=actual_branch,
            parent_branch=parent.branch_name if parent else None,
            parent_commit_sha=parent_ref if parent_ref != "HEAD" else get_head_commit(workspace),
            commit_sha=setup_commit,
            artifact_dir=artifact_dir,
            workspace_path=workspace,
            status=AttemptStatus.QUEUED,
            metric_name=task.primary_metric_name,
            higher_is_better=task.higher_is_better,
        )
        node = SearchNode(
            task_id=task.task_id,
            attempt_id=attempt_id,
            work_order_id=work_order.work_order_id,
            parent_attempt_id=idea.parent_attempt_id,
            depth=(self._parent_depth(task.task_id, idea.parent_attempt_id) + 1) if idea.parent_attempt_id else 0,
            objective=idea.objective,
            hypothesis=idea.hypothesis,
            novelty_key=idea.novelty_key,
            status=AttemptStatus.QUEUED,
        )
        self._record_attempt_state(attempt, node, AttemptStatus.QUEUED)
        self._record_attempt_state(attempt, node, AttemptStatus.RUNNING)

        start = time.monotonic()
        impl = self.worker.run(work_order, task, workspace)
        attempt.runtime_seconds = time.monotonic() - start
        self._write_implementation_artifacts(artifact_dir, workspace, impl)
        impl_commit = commit_all(workspace, "codex implementation attempt") if impl.changed_files else None
        attempt.implementation_status = impl.status
        attempt.commit_sha = impl_commit or setup_commit

        if impl.status != ImplementationStatus.SUCCESS:
            attempt.status = AttemptStatus.TIMEOUT if impl.status == ImplementationStatus.TIMEOUT else AttemptStatus.FAILED
            attempt.failure_class = classify_implementation(impl)
            attempt.failure_reason = impl.stderr
            attempt.completed_at = utc_now()
            self._record_attempt_state(attempt, node, attempt.status)
            self._record_attempt_summary(task, attempt, node)
            return attempt

        self._record_attempt_state(attempt, node, AttemptStatus.IMPLEMENTATION_COMPLETED)
        return self._evaluate_attempt(task, attempt, node, time.monotonic())

    def adopt_best(self, task_id: str, *, adopted_branch: str | None = None, notes: str | None = None) -> AdoptionRecord:
        """Promote the current best attempt to a stable git branch."""

        best = self.store.get_best_experiment(task_id)
        if best is None:
            raise RuntimeError(f"No successful scored attempt for task: {task_id}")
        if not best.commit_sha:
            raise RuntimeError(f"Best attempt has no commit sha: {best.attempt_id}")
        repo = self.store.task_git_repo(task_id)
        branch = force_branch(repo, adopted_branch or f"best/{task_id}", best.commit_sha)
        record = AdoptionRecord(
            task_id=task_id,
            attempt_id=best.attempt_id,
            branch_name=best.branch_name,
            adopted_branch=branch,
            commit_sha=best.commit_sha,
            metric_name=best.metric_name,
            metric_value=best.metric_value,
            higher_is_better=best.higher_is_better,
            notes=notes,
        )
        self.store.append_adoption(record)
        return record

    def _recover_partial_evaluations(self, task: TaskSpec) -> list[ExperimentResult]:
        recovered: list[ExperimentResult] = []
        for attempt in self.store.list_resumable_attempts(task.task_id):
            node = self._node_for_attempt(task.task_id, attempt.attempt_id)
            recovered.append(self._evaluate_attempt(task, attempt, node, time.monotonic()))
        return recovered

    def _evaluate_attempt(
        self,
        task: TaskSpec,
        attempt: ExperimentResult,
        node: SearchNode | None,
        started_at: float,
    ) -> ExperimentResult:
        self._record_attempt_state(attempt, node, AttemptStatus.EVALUATION_RUNNING)
        evaluator_result, docker_result = self.evaluator.run(
            attempt_id=attempt.attempt_id, task=task, workspace=attempt.workspace_path
        )
        artifact_dir = attempt.artifact_dir
        self._write_artifact(artifact_dir, "evaluator_result.json", evaluator_result.to_json_text())
        self._write_artifact(artifact_dir, "evaluator_stdout.txt", evaluator_result.stdout)
        self._write_artifact(artifact_dir, "evaluator_stderr.txt", evaluator_result.stderr)
        dep_dir = attempt.workspace_path / ".codex_mle_harness"
        self._copy_if_exists(dep_dir / "dependency_install_stdout.txt", artifact_dir / "dependency_install_stdout.txt")
        self._copy_if_exists(dep_dir / "dependency_install_stderr.txt", artifact_dir / "dependency_install_stderr.txt")
        self._copy_if_exists(dep_dir / "dependency_install_exit_code.txt", artifact_dir / "dependency_install_exit_code.txt")
        self._copy_if_exists(
            attempt.workspace_path / task.evaluator_result_path,
            artifact_dir / task.evaluator_result_path,
        )
        if (attempt.workspace_path / "working" / "result.json").exists():
            self._copy_if_exists(
                attempt.workspace_path / "working" / "result.json",
                artifact_dir / "candidate_result.json",
            )
        attempt.container = docker_result.metadata
        attempt.evaluator_status = evaluator_result.status
        attempt.metric_name = evaluator_result.metric_name
        attempt.metric_value = evaluator_result.metric_value
        attempt.higher_is_better = evaluator_result.higher_is_better
        attempt.failure_class = classify_evaluator(evaluator_result)
        attempt.failure_reason = evaluator_result.stderr or json.dumps(evaluator_result.diagnostics)
        attempt.runtime_seconds = (attempt.runtime_seconds or 0.0) + (time.monotonic() - started_at)
        self._record_attempt_state(attempt, node, AttemptStatus.EVALUATION_COMPLETED)

        missing = missing_required_outputs(task, attempt.workspace_path)
        if evaluator_result.status == EvaluationStatus.SUCCESS and evaluator_result.valid and not missing:
            attempt.status = AttemptStatus.SUCCESS
            attempt.failure_class = None
            attempt.failure_reason = None
        elif missing:
            attempt.status = AttemptStatus.INVALID
            attempt.failure_class = FailureClass.MISSING_REQUIRED_OUTPUT.value
            attempt.failure_reason = f"Missing required output(s): {', '.join(missing)}"
        elif evaluator_result.status == EvaluationStatus.TIMEOUT:
            attempt.status = AttemptStatus.TIMEOUT
        elif evaluator_result.status == EvaluationStatus.INVALID:
            attempt.status = AttemptStatus.INVALID
        else:
            attempt.status = AttemptStatus.FAILED
        attempt.completed_at = utc_now()
        self._record_attempt_state(attempt, node, attempt.status)
        self._record_attempt_summary(task, attempt, node)
        return attempt

    def _promote_round(
        self,
        planner: Planner,
        task: TaskSpec,
        round_index: int,
        attempts: list[ExperimentResult],
    ) -> None:
        try:
            promotion = planner.promote(
                task=task,
                store=self.store,
                round_index=round_index,
                attempts=attempts,
            )
        except Exception as exc:
            promotion = f"Knowledge promotion failed: {type(exc).__name__}: {exc}"
            failure_path = self.harness_dir / "promotions" / f"round_{round_index:03d}_failed.md"
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            failure_path.write_text(promotion, encoding="utf-8")
            self.store.append_promotion(
                PromotionRecord(
                    task_id=task.task_id,
                    round_index=round_index,
                    planner_name=planner.__class__.__name__,
                    content=promotion,
                    source_attempt_ids=[attempt.attempt_id for attempt in attempts],
                    artifact_path=failure_path,
                )
            )
            return
        if not promotion:
            return
        promotion_path = self.harness_dir / "promotions" / f"round_{round_index:03d}.md"
        promotion_path.parent.mkdir(parents=True, exist_ok=True)
        promotion_path.write_text(promotion, encoding="utf-8")
        self.store.append_promotion(
            PromotionRecord(
                task_id=task.task_id,
                round_index=round_index,
                planner_name=planner.__class__.__name__,
                content=promotion,
                source_attempt_ids=[attempt.attempt_id for attempt in attempts],
                artifact_path=promotion_path,
            )
        )

    def _record_attempt_state(
        self,
        attempt: ExperimentResult,
        node: SearchNode | None,
        status: AttemptStatus,
    ) -> None:
        attempt.status = status
        self.store.append_attempt(attempt)
        self.store.update_work_order_status(attempt.work_order_id, status)
        if node is not None:
            node.status = status
            node.score = attempt.metric_value
            self.store.append_search_node(node)

    def _node_for_attempt(self, task_id: str, attempt_id: str) -> SearchNode | None:
        matches = [node for node in self.store.list_search_nodes(task_id) if node.attempt_id == attempt_id]
        return matches[-1] if matches else None

    def _write_implementation_artifacts(self, artifact_dir: Path, workspace: Path, impl) -> None:
        self._write_artifact(artifact_dir, "implementation_result.json", impl.to_json_text())
        self._write_artifact(artifact_dir, "codex_stdout.txt", impl.stdout)
        self._write_artifact(artifact_dir, "codex_stderr.txt", impl.stderr)
        self._copy_if_exists(workspace / ".work_order.json", artifact_dir / ".work_order.json")
        self._copy_if_exists(workspace / ".work_order_prompt.md", artifact_dir / ".work_order_prompt.md")
        self._copy_if_exists(workspace / ".goal.md", artifact_dir / ".goal.md")
        if impl.patch:
            self._write_artifact(artifact_dir, "implementation.patch", impl.patch)
        elif impl.changed_files:
            save_patch(workspace, artifact_dir / "implementation.patch")
        dep_dir = workspace / ".codex_mle_harness"
        self._copy_if_exists(dep_dir / "dependency_install_stdout.txt", artifact_dir / "dependency_install_stdout.txt")
        self._copy_if_exists(dep_dir / "dependency_install_stderr.txt", artifact_dir / "dependency_install_stderr.txt")
        self._copy_if_exists(dep_dir / "dependency_install_exit_code.txt", artifact_dir / "dependency_install_exit_code.txt")

    def _record_attempt_summary(
        self,
        task: TaskSpec,
        attempt: ExperimentResult,
        node: SearchNode | None,
    ) -> None:
        summary = build_attempt_summary(
            task=task,
            attempt=attempt,
            store=self.store,
            artifact_dir=attempt.artifact_dir,
            workspace=attempt.workspace_path,
        )
        attempt.breakthrough = summary.breakthrough
        attempt.breakthrough_reason = summary.breakthrough_reason
        self.store.append_attempt(attempt)
        if node is not None:
            self.store.append_search_node(node)
        self.store.append_attempt_summary(summary)
        self._write_artifact(attempt.artifact_dir, "attempt_summary.json", summary.to_json_text())

    def _prepare_workspace(self, task: TaskSpec, workspace: Path) -> None:
        (workspace / "working").mkdir(parents=True, exist_ok=True)
        (workspace / "submission").mkdir(parents=True, exist_ok=True)
        if task.manifest_path:
            shutil.copy2(task.manifest_path, workspace / "task_manifest.yaml")
        shutil.copy2(task.description_path, workspace / "task_description.md")
        for support_file in task.support_files:
            if not support_file.is_file():
                raise ValueError(f"Support file must be a regular file: {support_file}")
            destination_rel = support_file_destination(task, support_file)
            if is_reserved_workspace_path(destination_rel):
                raise ValueError(f"Support file destination is reserved: {destination_rel}")
            destination = workspace / destination_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(support_file, destination)
        ignore = [
            ".codex_mle_harness/",
            ".codex_final_message.txt",
            "__pycache__/",
            "*.pyc",
            task.evaluator_result_path,
        ]
        for mount in task.data_mounts:
            target_rel = workspace_relative_path(mount.target)
            ignore.append(target_rel.as_posix().rstrip("/") + "/")
            target = workspace / target_rel
            if target.exists() or target.is_symlink():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.symlink_to(mount.source.resolve(), target_is_directory=mount.source.is_dir())
            except OSError:
                if mount.source.is_dir():
                    shutil.copytree(mount.source, target)
                else:
                    shutil.copy2(mount.source, target)
        (workspace / ".gitignore").write_text("\n".join(sorted(set(ignore))) + "\n", encoding="utf-8")

    def _parent_depth(self, task_id: str, parent_attempt_id: str | None) -> int:
        if parent_attempt_id is None:
            return -1
        nodes = self.store.list_search_nodes(task_id)
        depths = [node.depth for node in nodes if node.attempt_id == parent_attempt_id]
        return max(depths) if depths else 0

    def _write_artifact(self, artifact_dir: Path, name: str, content: str) -> None:
        path = artifact_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")

    def _copy_if_exists(self, source: Path, dest: Path) -> None:
        if source.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
