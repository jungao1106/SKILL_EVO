---
name: "facebookresearch-hydra-repo-localize-validate-recover"
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
source_decision_index: 70
repo: "facebookresearch__hydra"
confidence: 0.51
proxy_reward: 0.56
---

# facebookresearch-hydra-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `memory_only`
- Support: `0` `verifier_positive_task_events`; bucket `support_0`
- Reason: insufficient verifier-positive repo support

## Trigger

current task is in repo facebookresearch__hydra; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest -q 'tests/instantiate/test_instantiate.py::test_cannot_locate_target[instantiate2]' 'tests/instantiate/test_instantiate.py::test_instantiated_regular_class_container_types_partial[instantiate2]' 'tests/instantiate/test_instantia

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 0 verifier-positive events; paths=hydra/_internal/instantiate/_instantiate2.py, tests/instantiate/test_instantiate.py; tests=cd /testbed && python -m pytest -q 'tests/instantiate/test_instantiate.py::test_cannot_locate_target[instantiate2]' 'tests/instantiate/test_instantiate.py::test_instantiated_regular_class_container_types_partial[instantiate2]' 'tests/instantiate/test_instantia; failures=localization-drift, weak-validation

## Evaluator Summary

insufficient verifier-positive repo support
