---
name: "pandas-dev-pandas-repo-localize-validate-recover"
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
source_decision_index: 156
repo: "pandas-dev__pandas"
confidence: 0.95
proxy_reward: 1.0
---

# pandas-dev-pandas-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `3` `verifier_positive_task_events`; bucket `support_3`
- Reason: accepted duplicate repo candidate already promoted in this version

## Trigger

current task is in repo pandas-dev__pandas; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && cat >> pandas/tests/scalar/timestamp/test_timestamp.py << 'EOF' def test_negative_dates(): # https://github.com/pandas-dev/pandas/issues/50787 ts = Timestamp("-2000-01-01") msg = ( " not yet supported on Timestamps which are outside the range of

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=doc/source/whatsnew/v2.0.0.rst; tests=cd /testbed && cat >> pandas/tests/scalar/timestamp/test_timestamp.py << 'EOF' def test_negative_dates(): # https://github.com/pandas-dev/pandas/issues/50787 ts = Timestamp("-2000-01-01") msg = ( " not yet supported on Timestamps which are outside the range of; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
