# Detecting Insults In Social Commentary

Build a classifier that predicts whether each social-commentary row is insulting.

Candidate-visible files are under `input/public/`:

- `train.csv`: training data with columns `Insult`, `Date`, and `Comment`.
- `test.csv`: test rows with columns `Date` and `Comment`.
- `sample_submission_null.csv`: required submission shape.

Write `submission/submission.csv` with exactly the same columns and row order as
`input/public/sample_submission_null.csv`. The `Insult` column must contain
integer labels `0` or `1`.

The evaluator scores classification accuracy against held-out labels.
