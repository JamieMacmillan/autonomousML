"""Diverse beam search scheduler."""

from __future__ import annotations

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.models import AttemptStatus, ExperimentResult, PlannerIdea, TaskSpec


class DiverseBeamScheduler:
    """Select parent attempts and planner ideas without pure hill-climbing."""

    def select_ideas(
        self,
        *,
        task: TaskSpec,
        store: ExperimentStore,
        planner_ideas: list[PlannerIdea],
        round_index: int,
    ) -> list[PlannerIdea]:
        limit = max(1, task.scheduler.round_size)
        attempts = store.list_attempts(task.task_id)
        successes = [a for a in attempts if a.status == AttemptStatus.SUCCESS and a.metric_value is not None]
        failures = [
            a
            for a in attempts
            if a.status
            in {AttemptStatus.FAILED, AttemptStatus.INVALID, AttemptStatus.TIMEOUT, AttemptStatus.INTERRUPTED}
        ]
        best = store.get_best_experiment(task.task_id)
        selected: list[PlannerIdea] = []

        def add(idea: PlannerIdea) -> None:
            if len(selected) >= limit:
                return
            key = (idea.operator, idea.parent_attempt_id, idea.novelty_key, idea.objective)
            existing = {(i.operator, i.parent_attempt_id, i.novelty_key, i.objective) for i in selected}
            if key not in existing:
                selected.append(idea)

        if not attempts:
            for idea in planner_ideas:
                if idea.operator in {"draft", "fresh_draft"}:
                    add(idea)
            if not selected and planner_ideas:
                idea = planner_ideas[0]
                idea.operator = "draft"
                add(idea)
            return selected

        if not successes and failures:
            latest_failure = failures[-1]
            for idea in planner_ideas:
                if idea.operator == "debug":
                    idea.parent_attempt_id = idea.parent_attempt_id or latest_failure.attempt_id
                    add(idea)
                    break
            if not selected:
                add(
                    PlannerIdea(
                        operator="debug",
                        parent_attempt_id=latest_failure.attempt_id,
                        objective=(
                            "Repair the latest failed branch using its recorded failure summary. "
                            f"Failure class: {latest_failure.failure_class or 'unknown'}."
                        ),
                        hypothesis="A failed implementation branch can become the first valid score after targeted repair.",
                        novelty_key=f"debug-{latest_failure.failure_class or 'unknown'}",
                        strategy_tags=["debug", "first-success"],
                    )
                )
            return selected

        if best is not None:
            for idea in planner_ideas:
                if idea.parent_attempt_id is None and idea.operator in {
                    "improve",
                    "breakthrough_expand",
                    "ablation",
                    "refactor_for_reliability",
                }:
                    idea.parent_attempt_id = best.attempt_id
                if idea.parent_attempt_id == best.attempt_id:
                    add(idea)
                    break

        breakthrough_summaries = [
            summary for summary in store.list_attempt_summaries(task.task_id) if summary.breakthrough
        ]
        for summary in reversed(breakthrough_summaries[-3:]):
            add(
                PlannerIdea(
                    operator="breakthrough_expand",
                    parent_attempt_id=summary.attempt_id,
                    objective=(
                        "Explore one nearby variant from a breakthrough branch while preserving "
                        f"the core strategy. Breakthrough reason: {summary.breakthrough_reason}."
                    ),
                    hypothesis="A branch that changed the search trajectory deserves local sequential exploration.",
                    novelty_key=f"breakthrough-{summary.attempt_id}",
                    strategy_tags=["breakthrough", "branch-expansion"],
                )
            )

        seen_novelty = {a.failure_class for a in failures if a.failure_class}
        for idea in planner_ideas:
            if idea.novelty_key and idea.novelty_key not in seen_novelty:
                add(idea)

        for idea in planner_ideas:
            if idea.operator in {"draft", "fresh_draft"}:
                idea.parent_attempt_id = None
                add(idea)

        if failures and best is not None:
            for failure in reversed(failures[-3:]):
                add(
                    PlannerIdea(
                        operator="debug",
                        parent_attempt_id=failure.attempt_id,
                        objective=(
                            "Repair the previous failure mode while preserving any useful implementation work. "
                            f"Failure class: {failure.failure_class or 'unknown'}."
                        ),
                        hypothesis="Structured failure memory can prevent repeated invalid attempts.",
                        novelty_key=f"avoid-{failure.failure_class or 'unknown'}",
                        strategy_tags=["failure-memory"],
                    )
                )

        for idea in planner_ideas:
            add(idea)

        return selected[:limit]

    def should_stop(self, *, task: TaskSpec, store: ExperimentStore, started_at_monotonic: float, now_monotonic: float) -> bool:
        attempts = store.list_attempts(task.task_id)
        completed = [
            a
            for a in attempts
            if a.status
            in {
                AttemptStatus.SUCCESS,
                AttemptStatus.FAILED,
                AttemptStatus.INVALID,
                AttemptStatus.TIMEOUT,
                AttemptStatus.INTERRUPTED,
            }
        ]
        stop = task.stop_conditions
        if len(completed) >= stop.max_attempts:
            return True
        if stop.max_wall_clock_seconds and now_monotonic - started_at_monotonic >= stop.max_wall_clock_seconds:
            return True
        best = store.get_best_experiment(task.task_id)
        if best is not None and stop.target_metric_value is not None and best.metric_value is not None:
            if task.higher_is_better and best.metric_value >= stop.target_metric_value:
                return True
            if not task.higher_is_better and best.metric_value <= stop.target_metric_value:
                return True
        if stop.plateau_rounds > 0 and len(completed) >= stop.plateau_rounds:
            recent = completed[-stop.plateau_rounds :]
            if best is not None and best not in recent and len(completed) >= stop.max_attempts:
                return True
        return False
