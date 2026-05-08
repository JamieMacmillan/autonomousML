Create a small Python solution for a binary classification task.

The data is available in the `input` directory:

- `input/train.csv` has columns `x` and `label`.
- `input/test.csv` has columns `id` and `x`.

The pattern is deterministic: rows with `x >= 0` have label `1`, and rows with `x < 0` have label `0`.

Write `run.py`. It must read the input CSV files and create `submission/predictions.csv` with columns:

- `id`
- `label`

The evaluator will run `python run.py` and score accuracy against hidden labels for the toy test rows.
