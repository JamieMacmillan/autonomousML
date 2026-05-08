# Task Template Pack

This directory is the starting point for a new optimization task.

## Create A Task

1. Copy this directory to a new location, for example `tasks/my_task`.
2. Put data under `tasks/my_task/data` or point `data_mounts[].source` at an existing data directory.
3. Edit `description.md` so the worker knows the data layout, output schema, and metric goal.
4. Edit `evaluator.py` so it writes `evaluator_result.json` with:

   ```json
   {
     "metric_name": "accuracy",
     "metric_value": 0.0,
     "higher_is_better": true,
     "valid": true,
     "diagnostics": {}
   }
   ```

5. Edit `task.yaml` to set `task_id`, `required_outputs`, timeouts, resources, and planner.
6. Run preflight before spending a long run budget:

   ```bash
   python3 -m codex_mle_harness.cli validate-task --task tasks/my_task/task.yaml
   ```

## File Placement Rules

- Task-owned evaluator assets stay beside `task.yaml`; Docker mounts that directory read-only at `/task`.
- Candidate-visible starter files go in `support_files`; paths below the task directory are copied with the same relative path.
- Large data, image/video folders, models, and private files should be `data_mounts`, not `support_files`.
- Candidate outputs must be written inside the attempt workspace, usually under `submission/`.

## Offline Smoke Mode

Use `planner.type: static` when validating task wiring without planner-provider credentials. Switch to `ml_master` or `openai_compatible` for real autonomous search.
