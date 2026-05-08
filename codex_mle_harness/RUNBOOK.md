# Codex MLE Harness Runbook

This runbook is for operating the harness as a work demo or internal prototype.

## Prerequisites

- Python 3.10+
- Docker CLI with a running Docker daemon
- Codex CLI configured with a usable provider
- Provider credentials for any non-static planner, for example `CEREBRAS_API_KEY`

Install from the repository root:

```bash
pip install -r requirements.txt
```

or:

```bash
uv sync
```

## Validate A Task

Run preflight before a long unattended run:

```bash
python3 -m codex_mle_harness.cli validate-task \
  --task codex_mle_harness/examples/toy_threshold/task.yaml
```

For offline wiring checks that do not require Docker or Codex:

```bash
python3 -m codex_mle_harness.cli validate-task \
  --task codex_mle_harness/examples/toy_threshold/task.yaml \
  --skip-runtime
```

Structured output for logs:

```bash
python3 -m codex_mle_harness.cli validate-task \
  --task codex_mle_harness/examples/toy_threshold/task.yaml \
  --json
```

## Run A Task

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy init
python3 -m codex_mle_harness.cli --workspace runs/toy run-task \
  --task codex_mle_harness/examples/toy_threshold/task.yaml
```

The run stops according to the task manifest:

- `stop_conditions.max_attempts`
- `stop_conditions.max_wall_clock_seconds`
- `stop_conditions.target_metric_value`
- per-attempt implementation timeout
- evaluator timeout

Wall-clock stopping is checked between attempts. Set attempt and evaluator timeouts to sensible values for the run budget.

## Generate A Demo Artifact Bundle Without Live Codex

This command uses a deterministic fake worker with the toy fixture. It still
exercises workspace setup, Docker evaluation, SQLite state, artifacts, best
selection, report export, and branch adoption.

```bash
python3 -m codex_mle_harness.demo_smoke --clean
```

Expected outputs:

- `runs/work_demo_smoke/.codex_mle_harness/state.sqlite`
- `runs/work_demo_smoke/report.md`
- `runs/work_demo_smoke/best.patch`
- attempt artifacts under `runs/work_demo_smoke/.codex_mle_harness/artifacts/`

## Resume After Interruption

Mark running attempts as interrupted and inspect resumable partial evaluations:

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy resume
```

Continue a task:

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy resume \
  --task codex_mle_harness/examples/toy_threshold/task.yaml
```

Attempts that completed implementation but not evaluation are evaluated before new attempts are planned.

## Inspect A Run

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy status --task-id toy_threshold
python3 -m codex_mle_harness.cli --workspace runs/toy list-tree --task-id toy_threshold
python3 -m codex_mle_harness.cli --workspace runs/toy failures --task-id toy_threshold
python3 -m codex_mle_harness.cli --workspace runs/toy best --task-id toy_threshold
```

Find an attempt artifact directory:

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy artifacts attempt_xxx
```

Typical artifacts:

- `implementation_result.json`
- `codex_stdout.txt`
- `codex_stderr.txt`
- `implementation.patch`
- `evaluator_result.json`
- `evaluator_stdout.txt`
- `evaluator_stderr.txt`
- `attempt_summary.json`
- `dependency_install_stdout.txt`
- `dependency_install_stderr.txt`

## Export Results

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy export-report \
  --task-id toy_threshold \
  --output runs/toy/report.md
```

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy export-best-patch \
  --task-id toy_threshold \
  --output runs/toy/best.patch
```

Promote the current best attempt to a stable branch inside the harness git repo:

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy adopt-best \
  --task-id toy_threshold \
  --notes "Demo best candidate"
```

Show the adopted branch commit:

```bash
python3 -m codex_mle_harness.cli --workspace runs/toy show-branch \
  --task-id toy_threshold
```

## Create New Tasks

Start from `codex_mle_harness/templates/`. Keep evaluator-owned helpers beside `task.yaml`, copy only starter files through `support_files`, and mount large or private assets through `data_mounts`.
