"""Operator reports for CI logs and long-running harness runs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from codex_mle_harness.core.experiment_store import ExperimentStore


def render_task_report(store: ExperimentStore, task_id: str) -> str:
    attempts = store.list_attempts(task_id)
    best = store.get_best_experiment(task_id)
    promotions = store.list_promotions(task_id)
    adoptions = store.list_adoptions(task_id)
    summaries = {summary.attempt_id: summary for summary in store.list_attempt_summaries(task_id)}
    failures = Counter(a.failure_class or "none" for a in attempts if a.failure_class)
    failures_by_depth: Counter[str] = Counter()
    nodes = store.list_search_nodes(task_id)
    depth_by_attempt = {node.attempt_id: node.depth for node in nodes if node.attempt_id}
    for attempt in attempts:
        if attempt.failure_class:
            failures_by_depth[f"depth_{depth_by_attempt.get(attempt.attempt_id, 0)}:{attempt.failure_class}"] += 1
    statuses = Counter(a.status.value for a in attempts)

    lines = [
        f"# Codex MLE Harness Report: {task_id}",
        "",
        "## Status",
        "",
        f"- Attempts: {len(attempts)}",
        f"- Status counts: {dict(statuses)}",
    ]
    if best is None:
        lines.append("- Best attempt: none")
    else:
        lines.append(
            f"- Best attempt: {best.attempt_id} `{best.metric_name}`={best.metric_value} branch `{best.branch_name}`"
        )
    lines.extend(["", "## Failure Taxonomy", ""])
    if failures:
        for failure_class, count in sorted(failures.items()):
            lines.append(f"- {failure_class}: {count}")
    else:
        lines.append("- No classified failures recorded.")

    lines.extend(["", "## Failure Classes By Tree Depth", ""])
    if failures_by_depth:
        for key, count in sorted(failures_by_depth.items()):
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- No depth-aware failures recorded.")

    lines.extend(["", "## Recent Attempts", ""])
    for attempt in attempts[-20:]:
        metric = "" if attempt.metric_value is None else f" {attempt.metric_name}={attempt.metric_value}"
        failure = "" if not attempt.failure_class else f" failure={attempt.failure_class}"
        summary = summaries.get(attempt.attempt_id)
        root_cause = "" if summary is None or not summary.root_cause else f" root_cause={summary.root_cause}"
        lines.append(
            f"- {attempt.attempt_id} status={attempt.status.value}{metric}{failure}{root_cause} branch={attempt.branch_name}"
        )

    lines.extend(["", "## Promotions", ""])
    if promotions:
        for promotion in promotions[-10:]:
            preview = promotion.content.replace("\n", " ")[:180]
            lines.append(f"- round={promotion.round_index} {preview}")
    else:
        lines.append("- None recorded.")

    lines.extend(["", "## Branch Adoptions", ""])
    if adoptions:
        for adoption in adoptions[-10:]:
            lines.append(
                f"- {adoption.adopted_branch} -> {adoption.attempt_id} commit={adoption.commit_sha}"
            )
    else:
        lines.append("- None recorded.")

    return "\n".join(lines) + "\n"


def write_task_report(store: ExperimentStore, task_id: str, output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_task_report(store, task_id), encoding="utf-8")
    return output_path
