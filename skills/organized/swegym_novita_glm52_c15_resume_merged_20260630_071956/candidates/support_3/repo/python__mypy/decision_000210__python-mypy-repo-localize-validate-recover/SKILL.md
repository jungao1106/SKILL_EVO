---
name: "python-mypy-repo-localize-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "stage"
candidate_decision: "reject"
support_bucket: 3
support_count: 4
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 210
repo: "python__mypy"
confidence: 0.79
proxy_reward: 0.84
---

# python-mypy-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `reject`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: evaluator policy rejected candidate because false-accept risk is present

## Trigger

current task is in repo python__mypy; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest -q "mypy/test/testcheck.py::TypeCheckSuite::check-flags.test::testDisallowUntypedCallsAllowListConfig" "mypy/test/testcheck.py::TypeCheckSuite::check-flags.test::testDisallowUntypedCallsAllowListFlags" "mypy/test/testcheck.py::T

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=mypy, mypy/messages.py, mypy/checker.py; tests=cd /testbed && python -m pytest -q "mypy/test/testcheck.py::TypeCheckSuite::check-flags.test::testDisallowUntypedCallsAllowListConfig" "mypy/test/testcheck.py::TypeCheckSuite::check-flags.test::testDisallowUntypedCallsAllowListFlags" "mypy/test/testcheck.py::T; failures=localization-drift

## Evaluator Summary

evaluator policy rejected candidate because false-accept risk is present
