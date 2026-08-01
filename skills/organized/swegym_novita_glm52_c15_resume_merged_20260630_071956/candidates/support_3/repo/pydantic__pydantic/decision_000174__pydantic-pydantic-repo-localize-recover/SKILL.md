---
name: "pydantic-pydantic-repo-localize-recover"
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
source_decision_index: 174
repo: "pydantic__pydantic"
confidence: 0.95
proxy_reward: 1.0
---

# pydantic-pydantic-repo-localize-recover

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `3` `verifier_positive_task_events`; bucket `support_3`
- Reason: accepted duplicate repo candidate already promoted in this version

## Trigger

current task is in repo pydantic__pydantic; public trace matches repeated owner paths or adjacent modules; failure signature matches repeated repo failures

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

5 task events; 3 verifier-positive events; paths=pydantic/_internal/_generate_schema.py, pydantic/_internal/_typing_extra.py; tests=none; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
