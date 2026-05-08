"""Planner output parsing, validation, and deterministic repair."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from codex_mle_harness.core.experiment_store import ExperimentStore
from codex_mle_harness.core.models import PlannerIdea, PlannerValidationReport, TaskSpec

VALID_OPERATORS = {
    "draft",
    "fresh_draft",
    "improve",
    "debug",
    "breakthrough_expand",
    "ablation",
    "refactor_for_reliability",
}


def validate_planner_output(
    *,
    raw_text: str,
    task: TaskSpec,
    store: ExperimentStore,
    round_index: int,
    planner_name: str,
    limit: int,
) -> tuple[list[PlannerIdea], PlannerValidationReport]:
    """Parse planner text into valid ideas and record every repair."""

    errors: list[str] = []
    repaired = False
    attempts = store.list_attempts(task.task_id)
    known_attempt_ids = {attempt.attempt_id for attempt in attempts}
    best = store.get_best_experiment(task.task_id)

    try:
        data = parse_json_object(raw_text)
    except Exception as exc:
        data = {"ideas": []}
        repaired = True
        errors.append(f"json_parse_failed: {exc}")

    raw_items = _extract_idea_items(data)
    if not raw_items:
        repaired = True
        errors.append("no_ideas_found")

    ideas: list[PlannerIdea] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    default_operator = "draft" if not attempts else "improve"
    for idx, raw_item in enumerate(raw_items):
        item = dict(raw_item)
        if not item.get("objective"):
            if item.get("description"):
                item["objective"] = item["description"]
                repaired = True
            elif item.get("suggestion"):
                item["objective"] = item["suggestion"]
                repaired = True
            else:
                errors.append(f"idea_{idx}_missing_objective")
                continue
        operator = item.get("operator") or default_operator
        if operator not in VALID_OPERATORS:
            errors.append(f"idea_{idx}_invalid_operator:{operator}")
            operator = default_operator
            repaired = True
        item["operator"] = operator
        if operator in {"draft", "fresh_draft"}:
            if item.get("parent_attempt_id"):
                repaired = True
            item["parent_attempt_id"] = None
        elif item.get("parent_attempt_id") not in known_attempt_ids:
            if item.get("parent_attempt_id") is not None:
                errors.append(f"idea_{idx}_unknown_parent:{item['parent_attempt_id']}")
                repaired = True
            item["parent_attempt_id"] = best.attempt_id if best else None
        if "strategy_tags" not in item or not isinstance(item["strategy_tags"], list):
            tag = item.get("novelty_key") or operator
            item["strategy_tags"] = [str(tag)]
            repaired = True
        allowed = {
            "objective",
            "hypothesis",
            "rationale",
            "operator",
            "parent_attempt_id",
            "novelty_key",
            "strategy_tags",
        }
        item = {key: value for key, value in item.items() if key in allowed}
        try:
            idea = PlannerIdea.model_validate(item)
        except ValidationError as exc:
            errors.append(f"idea_{idx}_validation_failed:{exc.errors()}")
            repaired = True
            continue
        key = (idea.operator, idea.parent_attempt_id, idea.objective.strip())
        if key in seen:
            repaired = True
            continue
        seen.add(key)
        ideas.append(idea)
        if len(ideas) >= limit:
            break

    if not ideas:
        repaired = True
        fallback = "Create a simple, valid baseline solution that writes all required outputs."
        ideas = [
            PlannerIdea(
                operator="draft",
                objective=fallback,
                hypothesis="A valid baseline is needed before targeted improvements.",
                rationale="Planner output was unusable; the harness repaired it to a baseline draft.",
                novelty_key="repaired-baseline",
                strategy_tags=["baseline", "planner-repair"],
            )
        ][:limit]

    report = PlannerValidationReport(
        round_index=round_index,
        planner_name=planner_name,
        raw_text=raw_text,
        repaired_json={"ideas": [idea.model_dump(mode="json") for idea in ideas]},
        valid=not errors,
        repaired=repaired,
        errors=errors,
    )
    return ideas[:limit], report


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from plain text or a fenced block."""

    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("planner JSON root must be an object")
        return data
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        depth = 0
        in_string = False
        escape = False
        for idx, ch in enumerate(text[start:], start=start):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    data = json.loads(text[start : idx + 1])
                    if not isinstance(data, dict):
                        raise ValueError("planner JSON root must be an object")
                    return data
        raise


def _extract_idea_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    ideas = data.get("ideas")
    if isinstance(ideas, list):
        return [item for item in ideas if isinstance(item, dict)]

    converted: list[dict[str, Any]] = []
    for direction, suggestions in data.items():
        if not isinstance(suggestions, dict):
            continue
        for key, suggestion in suggestions.items():
            if not isinstance(suggestion, str):
                continue
            converted.append(
                {
                    "operator": "improve",
                    "objective": f"{direction}: {suggestion}",
                    "hypothesis": suggestion,
                    "rationale": f"Converted from ML-Master research direction `{direction}` idea `{key}`.",
                    "novelty_key": str(direction),
                    "strategy_tags": [str(direction), str(key)],
                }
            )
    return converted
