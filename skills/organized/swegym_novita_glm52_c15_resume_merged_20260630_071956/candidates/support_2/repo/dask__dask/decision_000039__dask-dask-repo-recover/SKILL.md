---
name: "dask-dask-repo-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "stage"
candidate_decision: "memory_only"
support_bucket: 2
support_count: 2
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 39
repo: "dask__dask"
confidence: 0.57
proxy_reward: 0.62
---

# dask-dask-repo-recover

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `memory_only`
- Support: `2` `verifier_positive_task_events`; bucket `support_2`
- Reason: missing repeated repo path, edit, or validation evidence

## Trigger

current task is in repo dask__dask; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 2 verifier-positive events; paths=none; tests=none; failures=localization-drift

## Evaluator Summary

missing repeated repo path, edit, or validation evidence
