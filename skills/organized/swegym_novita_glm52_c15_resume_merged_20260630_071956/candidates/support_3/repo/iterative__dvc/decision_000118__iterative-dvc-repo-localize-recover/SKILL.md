---
name: "iterative-dvc-repo-localize-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "stage"
candidate_decision: "revise"
support_bucket: 3
support_count: 3
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 118
repo: "iterative__dvc"
confidence: 0.79
proxy_reward: 0.84
---

# iterative-dvc-repo-localize-recover

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `revise`
- Support: `3` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate has partial support but needs stronger evidence or structure

## Trigger

current task is in repo iterative__dvc; public trace matches repeated owner paths or adjacent modules; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=dvc/utils/hydra.py, tests/func/utils/test_hydra.py; tests=none; failures=localization-drift

## Evaluator Summary

candidate has partial support but needs stronger evidence or structure
