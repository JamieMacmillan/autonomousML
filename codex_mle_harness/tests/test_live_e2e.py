import os
import shutil
from pathlib import Path

import pytest

from codex_mle_harness.core.models import AttemptStatus, PlannerConfig, TaskSpec
from codex_mle_harness.planning.planner import OpenAICompatiblePlanner
from codex_mle_harness.runner import HarnessRunner


pytestmark = pytest.mark.live


def test_live_planner_codex_docker_e2e(tmp_path):
    """Live E2E: intentionally requires Docker, Codex CLI, and live planner keys."""

    if os.environ.get("RUN_CODEX_MLE_LIVE") != "1":
        pytest.skip("Set RUN_CODEX_MLE_LIVE=1 to run the live Codex E2E.")
    if not shutil.which("codex"):
        pytest.fail("Codex CLI is required for the live E2E.")

    task = TaskSpec.from_manifest(
        Path("codex_mle_harness/examples/toy_threshold/task.yaml")
    )
    task.stop_conditions.max_attempts = 1
    task.scheduler.round_size = 1
    task.planner = PlannerConfig(
        type="openai_compatible",
        api_key_env="CEREBRAS_API_KEY",
        base_url=os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1"),
        model=os.environ.get("CEREBRAS_MODEL", "llama3.1-8b"),
        max_tokens=1024,
    )
    planner = OpenAICompatiblePlanner(
        api_key_env=task.planner.api_key_env,
        base_url=task.planner.base_url,
        model=task.planner.model,
        max_tokens=task.planner.max_tokens,
    )
    best = HarnessRunner(workspace=tmp_path, planner=planner).run_task(task)
    assert best is not None
    assert best.status == AttemptStatus.SUCCESS
    assert best.metric_value is not None
    assert best.metric_value >= 0.8
    assert (best.artifact_dir / "implementation.patch").exists()
    assert (best.artifact_dir / "evaluator_result.json").exists()
