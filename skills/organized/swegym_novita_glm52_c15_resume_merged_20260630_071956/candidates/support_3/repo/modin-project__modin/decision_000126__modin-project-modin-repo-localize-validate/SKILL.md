---
name: "modin-project-modin-repo-localize-validate"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "refresh"
candidate_decision: "accept"
support_bucket: 3
support_count: 4
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 126
repo: "modin-project__modin"
confidence: 0.95
proxy_reward: 1.0
---

# modin-project-modin-repo-localize-validate

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: accepted duplicate repo candidate already promoted in this version

## Trigger

current task is in repo modin-project__modin; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && timeout 1800 python -m pytest -q \ 'modin/pandas/test/test_groupby.py::test_agg_func_None_rename[False-by_and_agg_dict0]' \ modin/pandas/test/test_groupby.py::test_validate_by \ 'modin/pandas/test/test_groupby.py::test_simple_row_groupby[col1_ca

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=modin/pandas/groupby.py; tests=cd /testbed && timeout 1800 python -m pytest -q \ 'modin/pandas/test/test_groupby.py::test_agg_func_None_rename[False-by_and_agg_dict0]' \ modin/pandas/test/test_groupby.py::test_validate_by \ 'modin/pandas/test/test_groupby.py::test_simple_row_groupby[col1_ca; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
