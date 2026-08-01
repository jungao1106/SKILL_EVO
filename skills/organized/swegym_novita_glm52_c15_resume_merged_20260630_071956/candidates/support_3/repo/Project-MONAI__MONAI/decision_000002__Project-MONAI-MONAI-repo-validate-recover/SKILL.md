---
name: "Project-MONAI-MONAI-repo-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "refresh"
candidate_decision: "accept"
support_bucket: 3
support_count: 3
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 2
repo: "Project-MONAI__MONAI"
confidence: 0.95
proxy_reward: 1.0
---

# Project-MONAI-MONAI-repo-validate-recover

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `3` `verifier_positive_task_events`; bucket `support_3`
- Reason: accepted duplicate repo candidate already promoted in this version

## Trigger

current task is in repo Project-MONAI__MONAI; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Prefer the repeated focused validation command when it matches the current issue.
2. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest -q tests/test_data_stats.py::TestDataStats::test_value_2 tests/test_handler_stats.py::TestHandlerStats::test_loss_print tests/test_handler_stats.py::TestHandlerStats::test_loss_dict tests/test_data_stats.py::TestDataStats::test_

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=none; tests=cd /testbed && python -m pytest -q tests/test_data_stats.py::TestDataStats::test_value_2 tests/test_handler_stats.py::TestHandlerStats::test_loss_print tests/test_handler_stats.py::TestHandlerStats::test_loss_dict tests/test_data_stats.py::TestDataStats::test_; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
