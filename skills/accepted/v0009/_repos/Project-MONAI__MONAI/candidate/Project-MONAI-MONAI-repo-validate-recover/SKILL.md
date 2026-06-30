---
name: Project-MONAI-MONAI-repo-validate-recover
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# Project-MONAI-MONAI-repo-validate-recover

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo Project-MONAI__MONAI; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Prefer the repeated focused validation command when it matches the current issue.
2. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

python -m pytest -q tests/test_csv_saver.py::TestCSVSaver::test_saved_content tests/test_handler_classification_saver.py::TestHandlerClassificationSaver::test_saved_content tests/test_save_classificationd.py::TestSaveClassificationd::test_saved_content tests/t

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=none; tests=python -m pytest -q tests/test_csv_saver.py::TestCSVSaver::test_saved_content tests/test_handler_classification_saver.py::TestHandlerClassificationSaver::test_saved_content tests/test_save_classificationd.py::TestSaveClassificationd::test_saved_content tests/t; failures=localization-drift
