# Work Demo Brief

## The Pitch

We do not need a data scientist for every first-pass optimization loop. If we can define the data mount, output format, and scoring function, this harness can repeatedly generate candidate solutions, score them, learn from failures, and preserve the best result for review.

## What To Show

1. `templates/` for creating a new task without reading source code.
2. `validate-task` catching bad setup before a long run.
3. Existing example tasks for tabular benchmark-style workflows.
4. A fake-worker test run that exercises planner, workspace setup, Docker evaluator, persistence, reports, and best-result export.
5. `python3 -m codex_mle_harness.demo_smoke --clean` producing a local artifact bundle without live Codex.
6. `READINESS.md` and `LIMITATIONS.md` to make the claims precise.

## What Not To Claim

- It is not a replacement for final domain validation.
- It is not proven here by live Codex E2E.
- It does not remove the need to write or review an evaluator.
- It does not guarantee useful results on every private dataset.

## Decision Statement

This repo is ready for an internal work demo when:

- non-live tests pass
- `validate-task --skip-runtime` passes on examples
- Docker and Codex runtime checks pass on the demo machine
- at least one example report can be generated
- the audience understands the evaluator requirement and limitations

Recommended next step after the demo: run one real internal task for 3 to 5 attempts and review the best branch plus failure artifacts.
