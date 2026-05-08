"""Command line interface for the production Codex MLE harness."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.models import AttemptStatus, TaskSpec
from codex_mle_harness.preflight import validate_task_preflight
from codex_mle_harness.reporting import write_task_report
from codex_mle_harness.runner import HarnessRunner
from codex_mle_harness.utils.git_utils import branch_commit


def _store(workspace: Path) -> ExperimentStore:
    return ExperimentStore(workspace.resolve() / ".codex_mle_harness")


def cmd_init(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).resolve()
    HarnessRunner(workspace=workspace).init()
    print(f"Initialized harness workspace: {workspace / '.codex_mle_harness'}")


def cmd_run_task(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).resolve()
    task = TaskSpec.from_manifest(Path(args.task))
    runner = HarnessRunner(workspace=workspace)
    best = runner.run_task(task)
    if best is None:
        print("No successful experiment found.")
        sys.exit(1)
    print(
        f"Best attempt: {best.attempt_id} "
        f"{best.metric_name}={best.metric_value} branch={best.branch_name}"
    )


def cmd_validate_task(args: argparse.Namespace) -> None:
    task = TaskSpec.from_manifest(Path(args.task))
    report = validate_task_preflight(
        task,
        check_runtime=not args.skip_runtime,
        require_codex=not args.no_codex,
        require_docker=not args.no_docker,
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(report.to_text())
    if not report.ok:
        sys.exit(2)


def cmd_resume(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).resolve()
    if args.task:
        task = TaskSpec.from_manifest(Path(args.task))
        best = HarnessRunner(workspace=workspace).resume_task(task)
        if best:
            print(f"Best attempt: {best.attempt_id} {best.metric_name}={best.metric_value}")
        else:
            print("No successful scored attempt yet.")
    else:
        store = _store(workspace)
        interrupted = store.mark_running_as_interrupted()
        resumable = store.list_resumable_attempts()
        print(
            f"Marked {interrupted} running attempt(s) as interrupted; "
            f"{len(resumable)} partial evaluation attempt(s) can be resumed with --task."
        )


def cmd_status(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    attempts = store.list_attempts(args.task_id)
    counts: dict[str, int] = {}
    for attempt in attempts:
        counts[attempt.status.value] = counts.get(attempt.status.value, 0) + 1
    print(
        json.dumps(
            {
                "attempts": len(attempts),
                "by_status": counts,
                "resumable_partial_evaluations": len(store.list_resumable_attempts(args.task_id)),
                "promotions": len(store.list_promotions(args.task_id)),
                "adoptions": len(store.list_adoptions(args.task_id)),
            },
            indent=2,
        )
    )


def cmd_list_tree(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    nodes = store.list_search_nodes(args.task_id)
    for node in nodes:
        indent = "  " * node.depth
        score = "" if node.score is None else f" score={node.score}"
        print(f"{indent}- {node.node_id} attempt={node.attempt_id} status={node.status.value}{score}")
        print(f"{indent}  {node.objective[:120]}")


def cmd_show_attempt(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    attempt = store.get_attempt(args.attempt_id)
    if attempt is None:
        print(f"Attempt not found: {args.attempt_id}")
        sys.exit(1)
    print(attempt.to_json_text())


def cmd_best(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    best = store.get_best_experiment(args.task_id)
    if best is None:
        print(f"No successful scored attempt for task: {args.task_id}")
        sys.exit(1)
    print(best.to_json_text())


def cmd_artifacts(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    attempt = store.get_attempt(args.attempt_id)
    if attempt is None:
        print(f"Attempt not found: {args.attempt_id}")
        sys.exit(1)
    print(attempt.artifact_dir)


def cmd_failures(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    attempts = [a for a in store.list_attempts(args.task_id) if a.failure_class]
    for attempt in attempts:
        print(
            f"{attempt.attempt_id} status={attempt.status.value} "
            f"failure={attempt.failure_class} reason={(attempt.failure_reason or '')[:180]}"
        )


def cmd_promotions(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    for promotion in store.list_promotions(args.task_id):
        print(f"round={promotion.round_index} id={promotion.promotion_id}")
        print(promotion.content)
        print("")


def cmd_adopt_best(args: argparse.Namespace) -> None:
    runner = HarnessRunner(workspace=Path(args.workspace).resolve())
    record = runner.adopt_best(args.task_id, adopted_branch=args.branch, notes=args.notes)
    print(record.to_json_text())


def cmd_adoption_log(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    for adoption in store.list_adoptions(args.task_id):
        print(adoption.to_json_text())


def cmd_export_best_patch(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    best = store.get_best_experiment(args.task_id)
    if best is None:
        print(f"No successful scored attempt for task: {args.task_id}")
        sys.exit(1)
    source = best.artifact_dir / "implementation.patch"
    if not source.exists():
        print(f"Best attempt has no implementation.patch artifact: {best.attempt_id}")
        sys.exit(1)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    print(output)


def cmd_show_branch(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    repo = store.task_git_repo(args.task_id)
    ref = args.branch or f"best/{args.task_id}"
    commit = branch_commit(repo, ref)
    if commit is None:
        print(f"Branch/ref not found: {ref}")
        sys.exit(1)
    print(json.dumps({"task_id": args.task_id, "ref": ref, "commit": commit}, indent=2))


def cmd_compare_attempts(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    left = store.get_attempt(args.left_attempt_id)
    right = store.get_attempt(args.right_attempt_id)
    if left is None or right is None:
        print("Both attempts must exist.")
        sys.exit(1)
    print(
        json.dumps(
            {
                "left": _attempt_comparison_row(left),
                "right": _attempt_comparison_row(right),
            },
            indent=2,
        )
    )


def cmd_export_report(args: argparse.Namespace) -> None:
    store = _store(Path(args.workspace))
    output = write_task_report(store, args.task_id, Path(args.output))
    print(output)


def _attempt_comparison_row(attempt) -> dict:
    return {
        "attempt_id": attempt.attempt_id,
        "parent_attempt_id": attempt.parent_attempt_id,
        "status": attempt.status.value,
        "metric_name": attempt.metric_name,
        "metric_value": attempt.metric_value,
        "failure_class": attempt.failure_class,
        "branch_name": attempt.branch_name,
        "commit_sha": attempt.commit_sha,
        "artifact_dir": str(attempt.artifact_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Production Codex MLE Harness")
    parser.add_argument("--workspace", default=".", help="Harness workspace root")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init")
    init_p.set_defaults(func=cmd_init)

    run_p = sub.add_parser("run-task")
    run_p.add_argument("--task", required=True, help="Path to task.yaml/task.json")
    run_p.set_defaults(func=cmd_run_task)

    validate_p = sub.add_parser("validate-task")
    validate_p.add_argument("--task", required=True, help="Path to task.yaml/task.json")
    validate_p.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Skip Docker, Codex CLI, and planner API key runtime checks",
    )
    validate_p.add_argument("--no-codex", action="store_true", help="Do not require Codex CLI")
    validate_p.add_argument("--no-docker", action="store_true", help="Do not require Docker")
    validate_p.add_argument("--json", action="store_true", help="Emit structured JSON")
    validate_p.set_defaults(func=cmd_validate_task)

    resume_p = sub.add_parser("resume")
    resume_p.add_argument("--task", help="Optional task manifest to continue after recovery")
    resume_p.set_defaults(func=cmd_resume)

    status_p = sub.add_parser("status")
    status_p.add_argument("--task-id")
    status_p.set_defaults(func=cmd_status)

    tree_p = sub.add_parser("list-tree")
    tree_p.add_argument("--task-id")
    tree_p.set_defaults(func=cmd_list_tree)

    show_p = sub.add_parser("show-attempt")
    show_p.add_argument("attempt_id")
    show_p.set_defaults(func=cmd_show_attempt)

    best_p = sub.add_parser("best")
    best_p.add_argument("--task-id", required=True)
    best_p.set_defaults(func=cmd_best)

    artifacts_p = sub.add_parser("artifacts")
    artifacts_p.add_argument("attempt_id")
    artifacts_p.set_defaults(func=cmd_artifacts)

    failures_p = sub.add_parser("failures")
    failures_p.add_argument("--task-id")
    failures_p.set_defaults(func=cmd_failures)

    promotions_p = sub.add_parser("promotions")
    promotions_p.add_argument("--task-id")
    promotions_p.set_defaults(func=cmd_promotions)

    adopt_p = sub.add_parser("adopt-best")
    adopt_p.add_argument("--task-id", required=True)
    adopt_p.add_argument("--branch")
    adopt_p.add_argument("--notes")
    adopt_p.set_defaults(func=cmd_adopt_best)

    adoption_log_p = sub.add_parser("adoption-log")
    adoption_log_p.add_argument("--task-id")
    adoption_log_p.set_defaults(func=cmd_adoption_log)

    export_patch_p = sub.add_parser("export-best-patch")
    export_patch_p.add_argument("--task-id", required=True)
    export_patch_p.add_argument("--output", required=True)
    export_patch_p.set_defaults(func=cmd_export_best_patch)

    branch_p = sub.add_parser("show-branch")
    branch_p.add_argument("--task-id", required=True)
    branch_p.add_argument("--branch")
    branch_p.set_defaults(func=cmd_show_branch)

    compare_p = sub.add_parser("compare-attempts")
    compare_p.add_argument("left_attempt_id")
    compare_p.add_argument("right_attempt_id")
    compare_p.set_defaults(func=cmd_compare_attempts)

    report_p = sub.add_parser("export-report")
    report_p.add_argument("--task-id", required=True)
    report_p.add_argument("--output", required=True)
    report_p.set_defaults(func=cmd_export_report)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("Interrupted")
        sys.exit(130)


if __name__ == "__main__":
    main()
