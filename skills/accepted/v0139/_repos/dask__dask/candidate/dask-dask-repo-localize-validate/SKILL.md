---
name: dask-dask-repo-localize-validate
description: repo skill accepted by the evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# dask-dask-repo-localize-validate

- Run: `swegym_pi_novita_glm52_c15_resume_merged_20260709_101635`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo dask__dask; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q dask/tests/test_config.py::test_schema dask/tests/test_config.py::test_collect_yaml_paths dask/tests/test_config.py::test_get_set_canonical_name dask/tests/test_config.py::test_set_hard_to_copyables dask/tests/test_config.py:

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 positive events; paths=dask/array/creation.py, dask/array/core.py, dask/array; tests=cd /testbed && python -m pytest -q dask/tests/test_config.py::test_schema dask/tests/test_config.py::test_collect_yaml_paths dask/tests/test_config.py::test_get_set_canonical_name dask/tests/test_config.py::test_set_hard_to_copyables dask/tests/test_config.py:; failures=none
