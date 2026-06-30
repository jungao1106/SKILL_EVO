---
name: modin-project-modin-repo-localize-validate
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# modin-project-modin-repo-localize-validate

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo modin-project__modin; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q 'modin/test/storage_formats/pandas/test_internals.py::TestZeroComputationDtypes::test_preserve_dtypes_reset_index[False-False]' 'modin/test/storage_formats/pandas/test_internals.py::TestZeroComputationDtypes::test_preserve_dt

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 verifier-positive events; paths=modin/core/storage_formats/pandas/query_compiler.py, modin/pandas/dataframe.py, modin/test/storage_formats/pandas/test_internals.py, modin/core/dataframe/pandas/dataframe/dataframe.py; tests=cd /testbed && python -m pytest -q 'modin/test/storage_formats/pandas/test_internals.py::TestZeroComputationDtypes::test_preserve_dtypes_reset_index[False-False]' 'modin/test/storage_formats/pandas/test_internals.py::TestZeroComputationDtypes::test_preserve_dt; failures=none
