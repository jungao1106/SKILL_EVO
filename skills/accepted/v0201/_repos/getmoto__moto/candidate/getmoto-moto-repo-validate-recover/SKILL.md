---
name: getmoto-moto-repo-validate-recover
description: repo skill accepted by the evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# getmoto-moto-repo-validate-recover

- Run: `swegym_pi_novita_glm52_c15_resume_merged_20260709_101635`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo getmoto__moto; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Prefer the repeated focused validation command when it matches the current issue.
2. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

python -m pytest -q tests/test_ecs/test_ecs_boto3.py::test_update_missing_service tests/test_ecs/test_ecs_boto3.py::test_start_task_with_tags tests/test_ecs/test_ecs_boto3.py::test_create_running_service_negative_env_var tests/test_ecs/test_ecs_boto3.py::test_

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 positive events; paths=none; tests=python -m pytest -q tests/test_ecs/test_ecs_boto3.py::test_update_missing_service tests/test_ecs/test_ecs_boto3.py::test_start_task_with_tags tests/test_ecs/test_ecs_boto3.py::test_create_running_service_negative_env_var tests/test_ecs/test_ecs_boto3.py::test_; failures=localization-drift
