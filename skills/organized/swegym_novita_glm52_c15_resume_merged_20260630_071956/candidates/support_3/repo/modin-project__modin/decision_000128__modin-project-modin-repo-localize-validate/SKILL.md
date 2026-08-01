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
source_decision_index: 128
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

cd /testbed && python -m pytest -q modin/pandas/test/test_api.py::test_series_cat_api_equality 'modin/pandas/test/test_api.py::test_series_groupby_api_equality[SeriesGroupBy]' 'modin/pandas/test/test_api.py::test_sparse_accessor_api_equality[Series]' modin/pan

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 verifier-positive events; paths=modin/pandas; tests=cd /testbed && python -m pytest -q modin/pandas/test/test_api.py::test_series_cat_api_equality 'modin/pandas/test/test_api.py::test_series_groupby_api_equality[SeriesGroupBy]' 'modin/pandas/test/test_api.py::test_sparse_accessor_api_equality[Series]' modin/pan; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
