---
name: iterative-dvc-repo-localize-validate
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# iterative-dvc-repo-localize-validate

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo iterative__dvc; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q tests/unit/test_info.py::test_fs_info_outside_of_repo "tests/unit/test_info.py::test_info_in_repo[True]" tests/unit/test_info.py::test_info_outside_of_repo tests/unit/test_info.py::test_fs_info_in_repo "tests/unit/test_info.p

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 verifier-positive events; paths=dvc, dvc/config.py, dvc/repo/__init__.py; tests=cd /testbed && python -m pytest -q tests/unit/test_info.py::test_fs_info_outside_of_repo "tests/unit/test_info.py::test_info_in_repo[True]" tests/unit/test_info.py::test_info_outside_of_repo tests/unit/test_info.py::test_fs_info_in_repo "tests/unit/test_info.p; failures=none
