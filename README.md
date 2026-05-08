# Blind Tester

Blind Tester is a bounded autonomous ML/data-science evaluation loop.

This repository is a fork of EvoMaster and keeps the ML-Master 2.0 planner and
memory prompt assets that are useful for the loop. The broader EvoMaster
framework, playground examples, non-English documentation, and direct
ML-Master runner paths have been removed from this fork.

## What remains

- `codex_mle_harness/`: the product entrypoint and loop implementation.
- `codex_mle_harness/assets/prompts/`: retained ML-Master 2.0 planner and
  knowledge-promotion prompts.
- `codex_mle_harness/assets/data/`: sample prepared data used by the included
  insults benchmark example.
- `LICENSE`: original Apache 2.0 license and attribution.

The loop loads a task manifest, asks a planner for the next candidate idea,
hands implementation to Codex, evaluates the result, stores structured attempt
memory, and repeats within task-defined limits.

## Quick start

```bash
python3 -m pip install -e .
python3 -m codex_mle_harness.cli --workspace runs/iris init
python3 -m codex_mle_harness.cli validate-task \
  --task codex_mle_harness/examples/iris_classification/task.yaml
python3 -m codex_mle_harness.cli --workspace runs/iris run-task \
  --task codex_mle_harness/examples/iris_classification/task.yaml
```

Use `validate-task --skip-runtime` for manifest checks that do not require
Docker, Codex CLI, or planner credentials.

## Documentation

Harness-specific docs live with the package:

- `codex_mle_harness/README.md`
- `codex_mle_harness/RUNBOOK.md`
- `codex_mle_harness/READINESS.md`
- `codex_mle_harness/LIMITATIONS.md`

## Provenance

This fork derives from EvoMaster and preserves the Apache 2.0 license. The
retained ML-Master 2.0 assets are used only as planner and memory support for
the Blind Tester loop; Codex is the implementation worker.
