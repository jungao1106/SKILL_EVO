---
name: "facebookresearch-hydra-repo-localize-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "stage"
candidate_decision: "memory_only"
support_bucket: 0
support_count: 0
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 65
repo: "facebookresearch__hydra"
confidence: 0.55
proxy_reward: 0.6
---

# facebookresearch-hydra-repo-localize-recover

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `memory_only`
- Support: `0` `verifier_positive_task_events`; bucket `support_0`
- Reason: insufficient verifier-positive repo support

## Trigger

current task is in repo facebookresearch__hydra; public trace matches repeated owner paths or adjacent modules; failure signature matches repeated repo failures

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

5 task events; 0 verifier-positive events; paths=hydra/_internal/defaults_list.py, hydra/core/default_element.py, tests/defaults_list/test_defaults_tree.py, hydra, tests/defaults_list/data/legacy_override_hydra2.yaml; tests=none; failures=localization-drift

## Evaluator Summary

insufficient verifier-positive repo support
