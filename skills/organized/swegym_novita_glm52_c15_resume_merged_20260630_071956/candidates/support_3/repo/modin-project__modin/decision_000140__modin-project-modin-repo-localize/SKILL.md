---
name: "modin-project-modin-repo-localize"
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
source_decision_index: 140
repo: "modin-project__modin"
confidence: 0.95
proxy_reward: 1.0
---

# modin-project-modin-repo-localize

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: accepted duplicate repo candidate already promoted in this version

## Trigger

current task is in repo modin-project__modin; public trace matches repeated owner paths or adjacent modules

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=modin/core/storage_formats/pandas/query_compiler.py, modin, modin/core/dataframe/pandas/dataframe/dataframe.py, modin/pandas/groupby.py; tests=none; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
