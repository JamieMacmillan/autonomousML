# Task Description

Build a candidate solution that reads the mounted data from `input/`, runs from
`run.py`, and writes the required output file(s) under `submission/`.

## Data

- `input/`: replace this with the concrete data layout, file formats, and any
  train/test split rules.

## Required Output

- `submission/predictions.csv`
- Required columns: `id,label`
- One row per item to score.

## Metric

The evaluator computes `accuracy` from `submission/predictions.csv` against the
task-owned labels. Higher is better.

Replace this section with the real metric definition. For image, video, or
time-series tasks, describe the file naming scheme, expected output schema, and
any runtime constraints clearly enough that an implementation worker can run
without asking follow-up questions.

## Constraints

- Do not modify files under `input/`.
- Declare third-party runtime dependencies in `requirements.txt`.
- Keep the solution reproducible from a fresh attempt workspace.
