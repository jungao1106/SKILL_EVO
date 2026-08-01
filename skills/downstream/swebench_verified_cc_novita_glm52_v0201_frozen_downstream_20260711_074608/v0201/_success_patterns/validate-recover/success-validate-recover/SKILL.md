---
name: success-validate-recover
description: Training-distilled success pattern for focused validation, and recovery.
active: true
quality_score: 0.71
quality_tier: success_pattern
risk_flags: []
use_policy: evidence-gated
level: success_pattern
status: frozen
skill_type: "success_pattern"
support_count: 3
event_support_count: 4
positive_support_count: 15
support_bucket: "support_3"
pattern_key: "validate+recover"
trigger_index: 66
---

# success-validate-recover

## Status

- Run: `swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608`
- Status: `frozen`
- Source: training-only repo-level success evidence
- Support: `3` repos; `4` repo events; `15` verifier-positive task events
- Support repos: Project-MONAI__MONAI, getmoto__moto, iterative__dvc

## Applicability

Use this skill when current task evidence calls for focused validation, and recovery. It is a procedural prior, not a repo-specific patch recipe.

## Evidence Gate

Use only after inspecting the current repository evidence. Ignore it if the issue, traceback, owner path, or test signal does not match the current task.

## Actions

1. Start from the smallest public symptom: issue text, traceback, failing assertion, or focused test.
2. Choose the narrowest owner path that is supported by current evidence before editing.
3. Keep edits minimal and re-check the focused behavior before broad validation.
4. Prefer a focused validation command that directly exercises the changed behavior.
5. If the current diff drifts away from the symptom, stop and re-localize before expanding scope.

## Do Not

1. Do not copy historical paths, tests, or patch shape without fresh current-task evidence.
2. Do not broaden the edit solely because this skill was retrieved.
3. Do not treat weak validation as success; tie validation to the changed behavior.

## Support Summary

- Pattern key: `validate+recover`
- Common paths: none
- Common edited paths: none
- Common focused tests: python -m pytest -q tests/test_csv_saver.py::TestCSVSaver::test_saved_content tests/test_handler_classification_saver.py::TestHandlerClassificationSaver::test_saved_content tests/test_save_classificationd.py::TestSaveClassificationd::test_saved_content tests/t | cd /testbed && python -m pytest -q tests/test_data_stats.py::TestDataStats::test_value_2 tests/test_handler_stats.py::TestHandlerStats::test_loss_print tests/test_handler_stats.py::TestHandlerStats::test_loss_dict tests/test_data_stats.py::TestDataStats::test_ | cd /testbed && python -m pytest -q tests/test_iam/test_iam.py::test_get_account_summary tests/test_iam/test_iam.py::test_create_policy_already_exists tests/test_iam/test_iam.py::test_create_policy_with_tag_containing_large_key tests/test_iam/test_iam.py::test_ | cd /testbed && python -m pytest -q tests/test_pipeline.py::TestPipelineShowSingle::test_tree tests/test_pipeline.py::TestPipelineShowSingle::test tests/test_pipeline.py::TestPipelineShowDeep::test_ascii tests/test_pipeline.py::TestPipelineShowDeep::test_ascii_
- Associated failure signatures: localization-drift
