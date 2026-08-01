---
name: success-localize-edit-validate
description: Training-distilled success pattern for localization, narrow edit, and focused validation.
active: true
quality_score: 0.71
quality_tier: success_pattern
risk_flags: []
use_policy: evidence-gated
level: success_pattern
status: frozen
skill_type: "success_pattern"
support_count: 2
event_support_count: 7
positive_support_count: 34
support_bucket: "support_2"
pattern_key: "localize+edit+validate"
trigger_index: 88
---

# success-localize-edit-validate

## Status

- Run: `swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608`
- Status: `frozen`
- Source: training-only repo-level success evidence
- Support: `2` repos; `7` repo events; `34` verifier-positive task events
- Support repos: iterative__dvc, modin-project__modin

## Applicability

Use this skill when current task evidence calls for localization, narrow edit, and focused validation. It is a procedural prior, not a repo-specific patch recipe.

## Evidence Gate

Use only after inspecting the current repository evidence. Ignore it if the issue, traceback, owner path, or test signal does not match the current task.

## Actions

1. Start from the smallest public symptom: issue text, traceback, failing assertion, or focused test.
2. Use repeated owner paths only as localization priors; confirm with current repository evidence.
3. Choose the narrowest owner path that is supported by current evidence before editing.
4. Keep edits minimal and re-check the focused behavior before broad validation.
5. Prefer a focused validation command that directly exercises the changed behavior.

## Do Not

1. Do not copy historical paths, tests, or patch shape without fresh current-task evidence.
2. Do not broaden the edit solely because this skill was retrieved.
3. Do not treat weak validation as success; tie validation to the changed behavior.

## Support Summary

- Pattern key: `localize+edit+validate`
- Common paths: modin/core/dataframe/pandas/dataframe/dataframe.py, modin/core/storage_formats/pandas/query_compiler.py, modin/test/storage_formats/pandas/test_internals.py, modin/pandas/dataframe.py, modin, modin/core/dataframe/pandas/metadata/dtypes.py, dvc/scm/git/__init__.py, dvc/scm/base.py
- Common edited paths: modin/core/dataframe/pandas/dataframe/dataframe.py, modin/core/storage_formats/pandas/query_compiler.py, modin/pandas/dataframe.py, dvc/scm/git/__init__.py, modin/pandas/groupby.py, modin/test/storage_formats/pandas/test_internals.py, modin/pandas/base.py, modin/core/storage_formats/base/query_compiler.py
- Common focused tests: cd /testbed && python -m pytest -q 'modin/test/storage_formats/pandas/test_internals.py::test_reorder_labels_cache[unbalanced_partitioning-no_reordering-projection_only]' 'modin/test/storage_formats/pandas/test_internals.py::test_reorder_labels_cache[two_unbal | cd /testbed && python -m pytest -q tests/unit/remote/test_local.py 2>&1 | tail -20 | cd /testbed && python -m pytest -q 'modin/test/storage_formats/pandas/test_internals.py::TestZeroComputationDtypes::test_preserve_dtypes_reset_index[False-False]' 'modin/test/storage_formats/pandas/test_internals.py::TestZeroComputationDtypes::test_preserve_dt | cd /testbed && python -m pytest -q modin/pandas/test/test_api.py::test_top_level_api_equality 'modin/pandas/test/test_api.py::test_groupby_api_equality[SeriesGroupBy]' 'modin/pandas/test/test_api.py::test_sparse_accessor_api_equality[Series]' 'modin/pandas/tes
- Associated failure signatures: none
