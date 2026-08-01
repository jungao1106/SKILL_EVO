---
name: "dask-dask-repo-localize-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "stage"
candidate_decision: "revise"
support_bucket: 3
support_count: 3
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 42
repo: "dask__dask"
confidence: 0.79
proxy_reward: 0.84
---

# dask-dask-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `revise`
- Support: `3` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate has partial support but needs stronger evidence or structure

## Trigger

current task is in repo dask__dask; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest -q 'dask/dataframe/tests/test_rolling.py::test_rolling_axis[kwargs5]' 'dask/dataframe/tests/test_rolling.py::test_rolling_raises' 'dask/dataframe/tests/test_rolling.py::test_rolling_axis[kwargs0]' 'dask/dataframe/tests/test_roll

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=dask/dataframe/_compat.py, dask/dataframe/core.py, dask/dataframe; tests=cd /testbed && python -m pytest -q 'dask/dataframe/tests/test_rolling.py::test_rolling_axis[kwargs5]' 'dask/dataframe/tests/test_rolling.py::test_rolling_raises' 'dask/dataframe/tests/test_rolling.py::test_rolling_axis[kwargs0]' 'dask/dataframe/tests/test_roll; failures=localization-drift

## Evaluator Summary

candidate has partial support but needs stronger evidence or structure
