---
name: "conan-io-conan-repo"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "stage"
candidate_decision: "memory_only"
support_bucket: 3
support_count: 5
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 34
repo: "conan-io__conan"
confidence: 0.81
proxy_reward: 0.86
---

# conan-io-conan-repo

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `memory_only`
- Support: `5` `verifier_positive_task_events`; bucket `support_3`
- Reason: missing repeated repo path, edit, or validation evidence

## Trigger

current task is in repo conan-io__conan

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Use the repo evidence only as weak background and collect a fresh current-task signal first.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 verifier-positive events; paths=none; tests=none; failures=none

## Evaluator Summary

missing repeated repo path, edit, or validation evidence
