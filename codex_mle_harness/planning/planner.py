"""Planner interface and concrete planner adapters."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.models import (
    AttemptStatus,
    ExperimentResult,
    PlannerIdea,
    TaskSpec,
)
from codex_mle_harness.planning.validation import validate_planner_output

CompletionFn = Callable[[str, str], str]


class Planner(ABC):
    """Generate WorkOrder ideas from structured experiment state."""

    @abstractmethod
    def propose(
        self,
        *,
        task: TaskSpec,
        store: ExperimentStore,
        round_index: int,
        limit: int,
    ) -> list[PlannerIdea]:
        ...

    def promote(
        self,
        *,
        task: TaskSpec,
        store: ExperimentStore,
        round_index: int,
        attempts: list[ExperimentResult],
    ) -> str:
        return ""


class StaticPlanner(Planner):
    """Deterministic planner used for unit tests and fallback dry runs."""

    def propose(
        self,
        *,
        task: TaskSpec,
        store: ExperimentStore,
        round_index: int,
        limit: int,
    ) -> list[PlannerIdea]:
        best = store.get_best_experiment(task.task_id)
        failures = [
            attempt
            for attempt in store.list_attempts(task.task_id)
            if attempt.status in {AttemptStatus.FAILED, AttemptStatus.INVALID, AttemptStatus.TIMEOUT}
        ]
        if best is None:
            if failures:
                latest = failures[-1]
                summary = store.get_attempt_summary(latest.attempt_id)
                return [
                    PlannerIdea(
                        operator="debug",
                        parent_attempt_id=latest.attempt_id,
                        objective=(
                            "Repair the most promising failed branch using its structured root cause. "
                            f"Root cause: {(summary.root_cause if summary else latest.failure_class) or 'unknown'}."
                        ),
                        hypothesis="A focused debug attempt can turn an already-developed branch into the first valid score.",
                        rationale="No successful attempts exist, but a failed implementation branch exists.",
                        novelty_key=f"debug-{latest.failure_class or 'unknown'}",
                        strategy_tags=["debug", "first-success"],
                    )
                ][:limit]
            return [
                PlannerIdea(
                    operator="fresh_draft",
                    objective=(
                        "Create a complete data-science candidate solution for the task. "
                        f"Write `{task.entrypoint}`, declare dependencies, and produce all required outputs."
                    ),
                    hypothesis="A complete first candidate establishes a valid starting point for the tree.",
                    rationale="No successful attempts exist yet.",
                    novelty_key="baseline",
                    strategy_tags=["baseline", "first-success"],
                )
            ][:limit]
        ideas = [
            PlannerIdea(
                operator="improve",
                parent_attempt_id=best.attempt_id,
                objective="Improve the current best solution with a targeted, low-risk change.",
                hypothesis="A small refinement may improve the validation metric without breaking outputs.",
                rationale="Exploit the current best branch before drifting too far.",
                novelty_key="targeted-refinement",
                strategy_tags=["exploit"],
            ),
            PlannerIdea(
                operator="breakthrough_expand" if best.breakthrough else "improve",
                parent_attempt_id=best.attempt_id,
                objective="Explore a nearby variant from the current best branch while preserving its core strategy.",
                hypothesis="A local variant of the strongest branch may improve the score.",
                rationale="Exploit promising tree structure sequentially.",
                novelty_key="best-neighborhood",
                strategy_tags=["exploit", "branch-expansion"],
            ),
        ]
        return ideas[:limit]


class OpenAICompatiblePlanner(Planner):
    """OpenAI-compatible planner for Cerebras-compatible endpoints."""

    def __init__(
        self,
        *,
        api_key_env: str,
        model: str,
        base_url: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        completion_fn: CompletionFn | None = None,
    ):
        self.api_key_env = api_key_env
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.completion_fn = completion_fn

    def propose(
        self,
        *,
        task: TaskSpec,
        store: ExperimentStore,
        round_index: int,
        limit: int,
    ) -> list[PlannerIdea]:
        system = (
            "You are the planning and memory layer for a sequential autonomous ML agent. "
            "Return only JSON with an `ideas` list. Each idea must include objective, "
            "hypothesis, rationale, operator, novelty_key, and strategy_tags. Operators "
            "must be one of draft, fresh_draft, improve, debug, breakthrough_expand, "
            "ablation, refactor_for_reliability. Prefer learning from the latest attempt "
            "before proposing a new branch."
        )
        user = (
            f"Task description:\n{task.description_text()}\n\n"
            f"Primary metric: {task.primary_metric_name}; higher_is_better={task.higher_is_better}\n\n"
            f"Structured attempt memory:\n{json.dumps(_attempt_summary(store, task.task_id), indent=2)}\n\n"
            f"Knowledge promotions:\n{_promotion_summary(store, task.task_id)}\n\n"
            f"Generate {limit} diverse but sequentially useful ideas."
        )
        return self._validated_query(
            task=task,
            store=store,
            round_index=round_index,
            limit=limit,
            system=system,
            user=user,
            planner_name="openai_compatible",
        )

    def _validated_query(
        self,
        *,
        task: TaskSpec,
        store: ExperimentStore,
        round_index: int,
        limit: int,
        system: str,
        user: str,
        planner_name: str,
    ) -> list[PlannerIdea]:
        raw = self._complete(system, user)
        ideas, report = validate_planner_output(
            raw_text=raw,
            task=task,
            store=store,
            round_index=round_index,
            planner_name=planner_name,
            limit=limit,
        )
        store.append_planner_validation(task.task_id, report)
        if report.errors:
            repair_user = (
                "Repair the following planner output into strict JSON with an `ideas` list. "
                "Do not add prose. Preserve useful ideas and satisfy the schema.\n\n"
                f"Original output:\n{raw}\n\n"
                f"Validation errors:\n{json.dumps(report.errors, indent=2)}"
            )
            repaired_raw = self._complete(system, repair_user)
            repaired_ideas, repaired_report = validate_planner_output(
                raw_text=repaired_raw,
                task=task,
                store=store,
                round_index=round_index,
                planner_name=f"{planner_name}_repair",
                limit=limit,
            )
            store.append_planner_validation(task.task_id, repaired_report)
            if repaired_ideas:
                return repaired_ideas
        return ideas

    def _complete(self, system: str, user: str) -> str:
        if self.completion_fn is not None:
            return self.completion_fn(system, user)
        _load_env_files()
        from openai import OpenAI

        api_key = os.environ.get(self.api_key_env, "")
        if not api_key:
            raise RuntimeError(f"Planner API key env var is not set: {self.api_key_env}")
        client = OpenAI(api_key=api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        message = response.choices[0].message
        return message.content or "{}"


class MLMasterPlannerAdapter(OpenAICompatiblePlanner):
    """Adapter for the surviving ML-Master research and promotion prompts.

    This is the planner/memory boundary only. It intentionally avoids the old
    direct code-writing experiment path; Codex remains the implementation
    worker behind WorkOrder execution.
    """

    def __init__(
        self,
        *,
        api_key_env: str = "CEREBRAS_API_KEY",
        model: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        completion_fn: CompletionFn | None = None,
        prompt_dir: Path | None = None,
    ):
        super().__init__(
            api_key_env=api_key_env,
            model=model or os.environ.get("CEREBRAS_MODEL", "llama3.1-8b"),
            base_url=base_url or os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
            temperature=temperature,
            max_tokens=max_tokens,
            completion_fn=completion_fn,
        )
        if prompt_dir is not None:
            self.prompt_dir = prompt_dir
        else:
            repo_prompt_dir = (
                Path(__file__).resolve().parents[1]
                / "assets"
                / "prompts"
            )
            self.prompt_dir = (
                repo_prompt_dir
                if repo_prompt_dir.exists()
                else Path.cwd() / "codex_mle_harness" / "assets" / "prompts"
            )

    def propose(
        self,
        *,
        task: TaskSpec,
        store: ExperimentStore,
        round_index: int,
        limit: int,
    ) -> list[PlannerIdea]:
        system = _read_prompt(self.prompt_dir / "reseach_system_prompt.txt")
        user_template = _read_prompt(self.prompt_dir / "reseach_user_prompt.txt")
        best = store.get_best_experiment(task.task_id)
        user = user_template.format(
            task_description=task.description_text(),
            data_preview=_data_preview(task),
            initial_code=_initial_code_summary(task, store),
            best_code=_attempt_code_summary(best),
            research_plan_and_result_text=_research_memory_text(store, task.task_id),
        )
        return self._validated_query(
            task=task,
            store=store,
            round_index=round_index,
            limit=limit,
            system=system,
            user=user,
            planner_name="ml_master_research",
        )

    def promote(
        self,
        *,
        task: TaskSpec,
        store: ExperimentStore,
        round_index: int,
        attempts: list[ExperimentResult],
    ) -> str:
        system = _read_prompt(self.prompt_dir / "knowledge_promotion_system_prompt.txt")
        user_template = _read_prompt(self.prompt_dir / "knowledge_promotion_user_prompt.txt")
        best = store.get_best_experiment(task.task_id)
        user = user_template.format(
            task_description=task.description_text(),
            current_base_code=_attempt_code_summary(best),
            research_plan=json.dumps([_attempt_to_memory(a) for a in attempts], indent=2),
            results=_round_results_text(store, attempts),
        )
        return self._complete(system, user).strip()


def planner_from_task(task: TaskSpec) -> Planner:
    cfg = task.planner
    if cfg.type == "openai_compatible":
        return OpenAICompatiblePlanner(
            api_key_env=cfg.api_key_env or "CEREBRAS_API_KEY",
            model=cfg.model or os.environ.get("CEREBRAS_MODEL", "llama3.1-8b"),
            base_url=cfg.base_url or os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )
    if cfg.type == "ml_master":
        return MLMasterPlannerAdapter(
            api_key_env=cfg.api_key_env or "CEREBRAS_API_KEY",
            model=cfg.model,
            base_url=cfg.base_url,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )
    return StaticPlanner()


def _load_env_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    current = Path.cwd().resolve()
    for path in [current, *current.parents]:
        env_path = path / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)


def _attempt_summary(store: ExperimentStore, task_id: str) -> list[dict[str, object]]:
    summaries = store.list_attempt_summaries(task_id)
    if summaries:
        return [summary.model_dump(mode="json") for summary in summaries[-12:]]
    return [_attempt_to_memory(store, attempt) for attempt in store.list_attempts(task_id)[-12:]]


def _attempt_to_memory(store: ExperimentStore, attempt: ExperimentResult) -> dict[str, object]:
    summary = store.get_attempt_summary(attempt.attempt_id)
    if summary is not None:
        return summary.model_dump(mode="json")
    return {
        "attempt_id": attempt.attempt_id,
        "parent_attempt_id": attempt.parent_attempt_id,
        "status": attempt.status.value,
        "metric_name": attempt.metric_name,
        "metric_value": attempt.metric_value,
        "higher_is_better": attempt.higher_is_better,
        "failure_class": attempt.failure_class,
        "failure_reason": attempt.failure_reason,
        "branch_name": attempt.branch_name,
        "commit_sha": attempt.commit_sha,
        "breakthrough": attempt.breakthrough,
        "breakthrough_reason": attempt.breakthrough_reason,
    }


def _promotion_summary(store: ExperimentStore, task_id: str) -> str:
    promotions = store.list_promotions(task_id)[-3:]
    if not promotions:
        return "No promoted knowledge yet."
    return "\n\n".join(promotion.content for promotion in promotions)


def _research_memory_text(store: ExperimentStore, task_id: str) -> str:
    attempts = store.list_attempts(task_id)
    if not attempts:
        return "You have not made any implementation attempts yet."
    lines = ["Recent attempts and outcomes:"]
    for item in _attempt_summary(store, task_id):
        lines.append(json.dumps(item, sort_keys=True))
    promotions = _promotion_summary(store, task_id)
    lines.extend(["", "Promoted knowledge:", promotions])
    return "\n".join(lines)


def _round_results_text(store: ExperimentStore, attempts: list[ExperimentResult]) -> str:
    if not attempts:
        return "No attempts were completed in this round."
    best = store.get_best_experiment(attempts[0].task_id)
    lines = ["Round attempt results:"]
    for attempt in attempts:
        lines.append(json.dumps(_attempt_to_memory(store, attempt), sort_keys=True))
    if best:
        lines.append(
            f"Current best: {best.attempt_id} {best.metric_name}={best.metric_value} branch={best.branch_name}"
        )
    return "\n".join(lines)


def _data_preview(task: TaskSpec) -> str:
    lines: list[str] = []
    for mount in task.data_mounts:
        source = mount.source
        lines.append(f"Mount `{mount.target}` from `{source}` read_only={mount.read_only}")
        if source.is_dir():
            for child in sorted(source.iterdir())[:8]:
                lines.append(f"- {child.name}")
                if child.is_file() and child.suffix.lower() in {".csv", ".txt", ".md"}:
                    lines.extend(_head(child, max_lines=4))
        elif source.is_file():
            lines.extend(_head(source, max_lines=8))
    return "\n".join(lines) if lines else "No data mounts declared."


def _head(path: Path, *, max_lines: int) -> list[str]:
    try:
        return [f"  {line.rstrip()}" for line in path.read_text(encoding="utf-8").splitlines()[:max_lines]]
    except UnicodeDecodeError:
        return [f"  <binary or non-UTF8 file: {path.name}>"]


def _initial_code_summary(task: TaskSpec, store: ExperimentStore) -> str:
    attempts = store.list_attempts(task.task_id, statuses=[AttemptStatus.SUCCESS])
    if attempts:
        return _attempt_code_summary(attempts[0])
    return "No initial implementation has been adopted yet."


def _attempt_code_summary(attempt: ExperimentResult | None) -> str:
    if attempt is None:
        return "No successful implementation yet."
    summary_path = attempt.artifact_dir / "attempt_summary.json"
    if summary_path.exists():
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        return json.dumps(
            {
                "candidate_strategy": data.get("candidate_strategy"),
                "implementation_summary": data.get("implementation_summary"),
                "dependencies": data.get("dependencies", []),
                "lessons": data.get("lessons", []),
            },
            indent=2,
        )
    return f"Attempt {attempt.attempt_id} branch={attempt.branch_name} commit={attempt.commit_sha}"


def _read_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"ML-Master prompt file not found: {path}")
    return path.read_text(encoding="utf-8")
