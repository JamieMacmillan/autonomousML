# Codex MLE Harness

Production-oriented autonomous ML/data-science harness for Blind Tester.

The harness is the product entrypoint. Codex is the implementation worker.
The retained ML-Master 2.0 assets contribute planner, memory, and
knowledge-promotion prompts.

## Architecture

- `TaskSpec` is loaded from `task.yaml` or `task.json`.
- The planner emits validated `PlannerIdea` values.
- The scheduler runs attempts sequentially so each round can learn from the last.
- `CodexWorker` receives a structured `WorkOrder` and writes code in a fresh git worktree branch.
- Docker installs candidate-owned dependencies from the attempt workspace, then runs the task-owned evaluator command.
- The evaluator writes `evaluator_result.json`, which is the authoritative score.
- SQLite stores tasks, work orders, attempts, structured attempt summaries, search nodes, planner validations, promotions, and branch adoptions.
- Artifacts are stored under `.codex_mle_harness/artifacts/<attempt_id>/`.

`working/result.json` remains optional candidate telemetry. It is not the source
of truth for scoring.

## CLI

```bash
python3 -m codex_mle_harness.cli --workspace runs/iris init
python3 -m codex_mle_harness.cli validate-task \
  --task codex_mle_harness/examples/iris_classification/task.yaml
python3 -m codex_mle_harness.cli --workspace runs/iris run-task \
  --task codex_mle_harness/examples/iris_classification/task.yaml
python3 -m codex_mle_harness.cli --workspace runs/iris status --task-id iris_classification
python3 -m codex_mle_harness.cli --workspace runs/iris list-tree --task-id iris_classification
python3 -m codex_mle_harness.cli --workspace runs/iris failures --task-id iris_classification
python3 -m codex_mle_harness.cli --workspace runs/iris promotions --task-id iris_classification
python3 -m codex_mle_harness.cli --workspace runs/iris best --task-id iris_classification
python3 -m codex_mle_harness.cli --workspace runs/iris adopt-best --task-id iris_classification
python3 -m codex_mle_harness.cli --workspace runs/iris adoption-log --task-id iris_classification
python3 -m codex_mle_harness.cli --workspace runs/iris export-best-patch \
  --task-id iris_classification --output runs/iris/best.patch
python3 -m codex_mle_harness.cli --workspace runs/iris show-branch --task-id iris_classification
python3 -m codex_mle_harness.cli --workspace runs/iris export-report \
  --task-id iris_classification --output runs/iris/report.md
```

Use `validate-task --skip-runtime` for offline manifest checks that do not
require Docker, Codex CLI, or planner credentials.

Use `resume --task <task.yaml>` after a crash. Attempts interrupted during
Codex execution are marked `interrupted`; attempts that completed implementation
but were not fully evaluated are resumed through the evaluator.

## Work-Demo Package

The repo includes the operator-facing assets needed for a credible internal
demo:

- `templates/`: copyable task manifest, description, and evaluator template.
- `RUNBOOK.md`: install, validate, run, resume, inspect, and export commands.
- `READINESS.md`: acceptance matrix and evidence commands.
- `LIMITATIONS.md`: explicit constraints and mitigations.
- `WORK_DEMO_BRIEF.md`: short decision memo for work discussions.
- `demo_smoke.py`: deterministic local artifact-bundle generator that does not
  require live Codex.

For new tasks, keep evaluator-owned assets beside `task.yaml` so they are
mounted read-only at `/task`; use `support_files` only for small candidate-visible
starter files; use `data_mounts` for large datasets and binary assets.

## Codex

The default worker invokes the installed Codex CLI:

```bash
codex exec -C <attempt_workspace> --full-auto --json -o .codex_final_message.txt -
```

The prompt is rendered to `.work_order_prompt.md` and passed on stdin. The model
and provider come from the local Codex CLI configuration. Tests use harmless
fake commands and do not require real Codex access by default.

If the installed Codex CLI exposes a `--goal` or `--goal-file` option, the
worker can use goal mode automatically. The harness always writes `.goal.md`;
on older Codex CLI versions it falls back to `codex exec` and records that in
`implementation_result.json`.

```yaml
implementation_worker:
  type: codex
  mode: auto  # auto, exec, or goal
  fallback_to_exec: true
```

## Candidate Dependencies

Candidate solutions own their runtime dependencies. If Codex needs third-party
packages, it writes `requirements.txt` in the attempt workspace. The evaluator
container installs that file before scoring and records dependency install logs
as artifacts.

```yaml
dependency_policy:
  allow_requirements_txt: true
  requirements_path: requirements.txt
  install_command: python -m pip install --quiet --root-user-action ignore -r {requirements_path}
  install_timeout_seconds: 300
```

Dependency install failures are classified separately as
`dependency_install_failed`.

## Planner Memory

After every attempt, the harness writes `attempt_summary.json` and stores it in
SQLite. Planner prompts use this structured summary rather than raw logs:

- candidate strategy and validation claim
- dependency files and packages
- evaluator outcome and root cause
- lessons and recommended next actions
- breakthrough markers for tree exploration

## Docker And Secrets

Each evaluator run uses a fresh Docker container. The workspace is mounted
read-write; task files and data mounts are mounted read-only. Evaluator
containers now use a manifest allowlist by default:

```yaml
environment:
  pass_all: false
  allowlist:
    - CEREBRAS_API_KEY
```

Setting `pass_all: true` restores broad host environment passthrough. That is
convenient and risky because secrets can be visible to generated code.

## Example Tasks

- `examples/toy_threshold`: tiny threshold fixture for fast smoke tests.
- `examples/iris_classification`: small Iris classification benchmark with a
  deterministic evaluator and required `submission/predictions.csv` output.
- `examples/insults_mlebench`: MLEBench-style text classification task backed
  by the local retained prepared data.

## Tests

```bash
python3 -m pytest codex_mle_harness/tests -q
python3 -m pytest codex_mle_harness/tests -m docker -q
```

The live Codex E2E is intentionally opt-in for this pass:

```bash
RUN_CODEX_MLE_LIVE=1 python3 -m pytest codex_mle_harness/tests/test_live_e2e.py -q
```

## Known Limitations

- Docker resource telemetry is recorded as configured metadata, not live stats.
- Branch adoption currently creates or moves a stable best branch; merge-review
  workflows can be layered on top.
- Native parallel attempt execution is intentionally absent so planner memory can
  learn after each run.
