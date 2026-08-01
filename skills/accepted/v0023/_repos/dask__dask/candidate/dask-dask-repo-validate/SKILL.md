---
name: dask-dask-repo-validate
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 0.96
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# dask-dask-repo-validate

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `0.96`
- Confidence: `0.91`

## Trigger

current task is in repo dask__dask; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[disk-cumprod]' 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[tasks-cumprod]' 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[disk-cumsum]'

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=none; tests=cd /testbed && python -m pytest -q 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[disk-cumprod]' 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[tasks-cumprod]' 'dask/dataframe/tests/test_groupby.py::test_cumulative_axis[disk-cumsum]'; failures=none
