# Limitations Register

This file is intentionally direct. It keeps the demo credible.

## No Live E2E In This Readiness Pass

The local tests exercise fake-worker and Docker evaluator paths. They do not prove that a live Codex/provider run will solve a real business task.

Mitigation: run one private task with live credentials for a small fixed attempt budget before making production claims.

## Evaluator Required Per Task

The harness optimizes whatever the evaluator scores. It does not infer a reliable metric from prose alone.

Mitigation: start from `templates/evaluator.py` and keep evaluator logic deterministic, simple, and reviewable.

## Wall-Clock Budget Is Soft Between Attempts

`max_wall_clock_seconds` is checked between attempts. An already-running implementation or evaluator can continue until its own timeout.

Mitigation: set `attempt_timeout_seconds` and `evaluator_timeout_seconds` to values that fit the run budget.

## Docker Required For Normal Scoring

Evaluator runs are containerized. Docker must be installed and the daemon must be available.

Mitigation: run `validate-task` before long jobs.

## Codex CLI Required For Real Attempts

The default worker invokes the installed Codex CLI. Missing CLI or provider configuration fails before useful implementation work happens.

Mitigation: run `validate-task` with runtime checks and keep a small known-good example task.

## Candidate Dependencies Can Fail

Candidates own their `requirements.txt`. Bad packages, slow installs, native build failures, or image incompatibilities can invalidate attempts.

Mitigation: keep Docker images close to the task needs and inspect dependency install artifacts.

## Not A Final Domain Validator

A high evaluator score only means the candidate satisfied the evaluator. It may still be brittle, unfair, overfit, unsafe, or operationally unsuitable.

Mitigation: treat adopted branches as candidates for review, not production-ready answers.

## Data And Artifact Size Need Operator Judgment

Large videos, image corpora, model weights, or private data should be mounted read-only rather than copied into support files.

Mitigation: use `data_mounts` for large assets and keep `support_files` for small starter code/config only.

## Planner Quality Depends On Configuration

The static planner is useful for smoke tests but not deep search. LLM-backed planners depend on model quality, prompts, credentials, and provider stability.

Mitigation: use `ml_master` or `openai_compatible` for real search and review `promotions` plus attempt summaries.
