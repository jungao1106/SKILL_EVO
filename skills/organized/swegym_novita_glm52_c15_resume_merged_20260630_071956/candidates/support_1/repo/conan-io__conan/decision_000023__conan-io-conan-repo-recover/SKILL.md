---
name: "conan-io-conan-repo-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "stage"
candidate_decision: "memory_only"
support_bucket: 1
support_count: 1
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 23
repo: "conan-io__conan"
confidence: 0.53
proxy_reward: 0.42
---

# conan-io-conan-repo-recover

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `memory_only`
- Support: `1` `verifier_positive_task_events`; bucket `support_1`
- Reason: insufficient verifier-positive repo support

## Trigger

current task is in repo conan-io__conan; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 1 verifier-positive events; paths=none; tests=none; failures=localization-drift, no-diff-recovery

## Evaluator Summary

insufficient verifier-positive repo support
