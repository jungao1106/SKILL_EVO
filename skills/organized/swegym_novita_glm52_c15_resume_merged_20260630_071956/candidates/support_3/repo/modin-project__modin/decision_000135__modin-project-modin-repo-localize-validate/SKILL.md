---
name: "modin-project-modin-repo-localize-validate"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "refresh"
candidate_decision: "accept"
support_bucket: 3
support_count: 5
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 135
repo: "modin-project__modin"
confidence: 0.95
proxy_reward: 1.0
---

# modin-project-modin-repo-localize-validate

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `5` `verifier_positive_task_events`; bucket `support_3`
- Reason: accepted duplicate repo candidate already promoted in this version

## Trigger

current task is in repo modin-project__modin; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q 'modin/test/storage_formats/pandas/test_internals.py::test_reorder_labels_cache[unbalanced_partitioning-no_reordering-projection_only]' 'modin/test/storage_formats/pandas/test_internals.py::TestZeroComputationDtypes::test_pre

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 verifier-positive events; paths=modin/core/dataframe/pandas/dataframe/dataframe.py, modin/core/storage_formats/pandas/query_compiler.py, modin/test/storage_formats/pandas/test_internals.py, modin/core/dataframe/pandas/metadata/dtypes.py, modin/pandas/series.py, modin/pandas/test/test_series.py; tests=cd /testbed && python -m pytest -q 'modin/test/storage_formats/pandas/test_internals.py::test_reorder_labels_cache[unbalanced_partitioning-no_reordering-projection_only]' 'modin/test/storage_formats/pandas/test_internals.py::TestZeroComputationDtypes::test_pre; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
