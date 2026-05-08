# Work-Demo Readiness

## Positioning

The Codex MLE harness is ready to demo as a bounded autonomous optimization loop:

- point it at task data through read-only mounts
- define the output contract and evaluator
- run repeated implementation attempts through Codex
- score each attempt in Docker
- persist state, artifacts, failures, and best-result branches

It is not magic AutoML. The operator still needs a clear task description and an evaluator that turns a candidate output into a metric.

## Acceptance Matrix

| Claim | Evidence in repo |
| --- | --- |
| Can optimize a task against a metric | `TaskSpec` owns `evaluator_command`, `primary_metric_name`, `higher_is_better`, and required outputs. |
| Can use tabular, image, video, time-series, or other file data | `data_mounts` expose arbitrary file trees read-only to attempts and evaluator containers. |
| Can run unattended for a bounded search | `stop_conditions`, implementation timeout, evaluator timeout, Docker resource limits, and persisted SQLite state. |
| Can recover after interruption | `resume` marks running attempts interrupted and resumes partial evaluations. |
| Can inspect why attempts failed | artifacts include Codex logs, evaluator logs, dependency install logs, classified failures, and attempt summaries. |
| Can reuse the best result | `best`, `export-best-patch`, `adopt-best`, and `export-report` commands. |
| Can be handed to non-data-scientist operators | task templates, preflight validation, runbook, limitations register, and examples. |

## Required Operator Inputs

- A task directory containing `task.yaml`, `description.md`, and `evaluator.py`.
- Data mounted through `data_mounts`.
- A deterministic evaluator that writes `evaluator_result.json`.
- Codex CLI/provider configuration for implementation attempts.
- Optional planner-provider credentials for `ml_master` or `openai_compatible` planning.

## Demo-Ready Evidence Commands

Run these before taking the repo into a work discussion:

```bash
python3 -m pytest codex_mle_harness/tests -q
```

```bash
python3 -m codex_mle_harness.cli validate-task \
  --task codex_mle_harness/examples/toy_threshold/task.yaml \
  --skip-runtime
```

```bash
python3 -m codex_mle_harness.cli --help
```

For a full local smoke run that does not need live Codex, use the existing fake-worker tests:

```bash
python3 -m pytest codex_mle_harness/tests/test_scheduler_evaluator_runner.py \
  -k "fake_worker or iris_benchmark_fixture" -q
```

To generate a demo artifact bundle:

```bash
python3 -m codex_mle_harness.demo_smoke --clean
```

## Demo Narrative

The honest claim is:

> We can use this today for bounded optimization tasks where we can write an evaluator. It is suitable for first-pass internal prototyping, benchmark-style tasks, and repeated model or pipeline search. It does not remove the need for final domain review, but it reduces the need to have a data scientist manually run every experiment loop.

## Confidence Level

Without live Codex E2E, confidence is high for harness mechanics and medium for real autonomous search quality:

- High: manifest parsing, workspace setup, Docker evaluator execution, scoring contract, persistence, reporting, resume, artifacts.
- Medium: planner quality, Codex behavior on real private tasks, long unattended provider stability.

The next confidence step is one private task run for a fixed attempt budget using real Codex/provider credentials.
