"""WorkOrder creation and prompt rendering."""

from __future__ import annotations

from pathlib import Path

from .models import TaskSpec, WorkOrder


def create_work_order(
    task: TaskSpec,
    *,
    objective: str,
    operator: str = "draft",
    hypothesis: str | None = None,
    parent_attempt_id: str | None = None,
    parent_branch: str | None = None,
    strategy_tags: list[str] | None = None,
    attempt_index: int = 0,
) -> WorkOrder:
    """Create a WorkOrder using task defaults."""

    return WorkOrder(
        task_id=task.task_id,
        parent_attempt_id=parent_attempt_id,
        parent_branch=parent_branch,
        operator=operator,
        objective=objective,
        hypothesis=hypothesis,
        constraints=[
            f"Primary metric: {task.primary_metric_name}",
            f"Higher is better: {task.higher_is_better}",
            "The evaluator command owns the authoritative score.",
            "Do not rely on terminal-output parsing as the final metric source.",
            "If you use third-party packages, declare them in requirements.txt.",
            "A solution is incomplete until it can create every required output from the attempt workspace.",
        ],
        allowed_actions=[
            "Create or modify files inside the attempt workspace.",
            "Use the read-only data mounts described in the prompt.",
            "Use the internet and install packages if needed.",
        ],
        required_outputs=task.required_outputs,
        entrypoint=task.entrypoint,
        strategy_tags=strategy_tags or [],
        attempt_index=attempt_index,
        timeout_seconds=task.attempt_timeout_seconds,
    )


def render_work_order_prompt(work_order: WorkOrder, task: TaskSpec) -> str:
    """Render the prompt consumed by CodexWorker."""

    lines = [
        f"# Work Order: {work_order.work_order_id}",
        "",
        "You are the implementation worker for an autonomous ML/data-science experiment.",
        "Modify only this attempt workspace. The harness will run the evaluator after you finish.",
        "",
        "## Task",
        "",
        f"- Task ID: `{task.task_id}`",
        f"- Task name: `{task.task_name or task.task_id}`",
        f"- Entrypoint: `{work_order.entrypoint}`",
        f"- Primary metric: `{task.primary_metric_name}`",
        f"- Higher is better: `{task.higher_is_better}`",
        f"- Attempt mode: `{work_order.operator}`",
        "",
        "## Task Description",
        "",
        task.description_text(),
        "",
        "## Objective",
        "",
        work_order.objective,
        "",
    ]
    if work_order.hypothesis:
        lines.extend(["## Hypothesis", "", work_order.hypothesis, ""])
    lines.extend(["## Attempt Mode Contract", "", _mode_contract(work_order), ""])
    if work_order.strategy_tags:
        lines.extend(["## Strategy Tags", "", ", ".join(work_order.strategy_tags), ""])
    if task.data_mounts:
        lines.extend(["## Data Mounts", ""])
        for mount in task.data_mounts:
            mode = "read-only" if mount.read_only else "read-write"
            lines.append(f"- `{mount.target}` from `{mount.source}` ({mode})")
        lines.append("")
    if work_order.required_outputs:
        lines.extend(["## Required Outputs", ""])
        for output in work_order.required_outputs:
            lines.append(f"- `{output}`")
        lines.append("")
    lines.extend(
        [
            "## Dependency Contract",
            "",
            f"- Requirements file: `{task.dependency_policy.requirements_path}`",
            "- Declare every third-party runtime dependency needed by the candidate solution.",
            "- Do not assume packages available during implementation are available during evaluation.",
            "- Keep dependencies reproducible and compatible with the task Docker image.",
            "",
        ]
    )
    lines.extend(
        [
            "## Result Telemetry",
            "",
            "If useful, write optional telemetry to `working/result.json` with:",
            "",
            "```json",
            '{ "metric_name": "...", "metric_value": 0.0, "higher_is_better": true, "validation_strategy": "...", "notes": "..." }',
            "```",
            "",
            "This telemetry is not authoritative. The harness evaluator command decides the final score.",
            "",
            "## Constraints",
            "",
        ]
    )
    for constraint in work_order.constraints:
        lines.append(f"- {constraint}")
    return "\n".join(lines) + "\n"


def render_goal_prompt(work_order: WorkOrder, task: TaskSpec) -> str:
    """Render the durable goal consumed by Codex goal-capable workers."""

    return "\n".join(
        [
            f"# Goal: {work_order.work_order_id}",
            "",
            "Produce a valid scored ML/data-science solution for this task.",
            "",
            "You are done only when:",
            f"1. `{work_order.entrypoint}` exists and runs from the attempt workspace.",
            "2. Every needed third-party package is declared in requirements.txt.",
            "3. The required output files are created in the exact required format.",
            "4. Optional candidate telemetry is written to `working/result.json`.",
            "5. The implementation notes explain the strategy, validation claim, and risks.",
            "",
            "Attempt mode:",
            _mode_contract(work_order),
            "",
            "Task contract and details:",
            render_work_order_prompt(work_order, task),
        ]
    )


def write_work_order_files(work_order: WorkOrder, task: TaskSpec, workspace: Path) -> tuple[Path, Path]:
    """Write machine-readable and human-readable work order artifacts."""

    json_path = workspace / ".work_order.json"
    prompt_path = workspace / ".work_order_prompt.md"
    goal_path = workspace / ".goal.md"
    json_path.write_text(work_order.to_json_text(), encoding="utf-8")
    prompt_path.write_text(render_work_order_prompt(work_order, task), encoding="utf-8")
    goal_path.write_text(render_goal_prompt(work_order, task), encoding="utf-8")
    return json_path, prompt_path


def _mode_contract(work_order: WorkOrder) -> str:
    parent = work_order.parent_attempt_id or "none"
    contracts = {
        "draft": "Start from the task contract and create a complete candidate solution.",
        "fresh_draft": "Explore a materially different solution strategy from scratch.",
        "improve": (
            f"Start from parent attempt `{parent}`. Preserve working behavior while making "
            "one coherent improvement tied to the hypothesis."
        ),
        "debug": (
            f"Start from failed attempt `{parent}`. First repair the recorded failure mode, "
            "then make only changes needed for a valid scored run."
        ),
        "breakthrough_expand": (
            f"Start from breakthrough attempt `{parent}`. Preserve the core idea and explore "
            "one meaningful nearby variant."
        ),
        "ablation": (
            f"Start from parent attempt `{parent}`. Remove or simplify one component to test "
            "whether it actually contributes to score or reliability."
        ),
        "refactor_for_reliability": (
            f"Start from parent attempt `{parent}`. Keep predictions as close as possible "
            "while improving reproducibility, dependency declaration, and runtime reliability."
        ),
    }
    return contracts.get(work_order.operator, contracts["draft"])


class WorkOrderManager:
    """Compatibility wrapper around the new WorkOrder helpers."""

    def __init__(self, harness_dir: Path):
        self.harness_dir = Path(harness_dir)
        self.work_orders_dir = self.harness_dir / "work_orders"
        self.work_orders_dir.mkdir(parents=True, exist_ok=True)

    def save_work_order(self, work_order: WorkOrder) -> Path:
        path = self.work_orders_dir / f"{work_order.work_order_id}.json"
        path.write_text(work_order.to_json_text(), encoding="utf-8")
        return path

    def load_work_order_from_path(self, path: Path) -> WorkOrder:
        return WorkOrder.model_validate_json(Path(path).read_text(encoding="utf-8"))
