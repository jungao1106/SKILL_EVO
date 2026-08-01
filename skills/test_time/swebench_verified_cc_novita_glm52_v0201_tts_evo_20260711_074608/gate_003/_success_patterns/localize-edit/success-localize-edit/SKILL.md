---
name: success-localize-edit
description: Training-distilled success pattern for localization, and narrow edit.
active: true
quality_score: 0.84
quality_tier: success_pattern
risk_flags: []
use_policy: evidence-gated
level: success_pattern
status: frozen
skill_type: "success_pattern"
support_count: 6
event_support_count: 9
positive_support_count: 42
support_bucket: "support_3"
pattern_key: "localize+edit"
trigger_index: 120
---

# success-localize-edit

## Status

- Run: `swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608`
- Status: `frozen`
- Source: training-only repo-level success evidence
- Support: `6` repos; `9` repo events; `42` verifier-positive task events
- Support repos: dask__dask, getmoto__moto, modin-project__modin, pandas-dev__pandas, pydantic__pydantic, python__mypy

## Applicability

Use this skill when current task evidence calls for localization, and narrow edit. It is a procedural prior, not a repo-specific patch recipe.

## Evidence Gate

Use only after inspecting the current repository evidence. Ignore it if the issue, traceback, owner path, or test signal does not match the current task.

## Actions

1. Start from the smallest public symptom: issue text, traceback, failing assertion, or focused test.
2. Use repeated owner paths only as localization priors; confirm with current repository evidence.
3. Choose the narrowest owner path that is supported by current evidence before editing.
4. Keep edits minimal and re-check the focused behavior before broad validation.

## Do Not

1. Do not copy historical paths, tests, or patch shape without fresh current-task evidence.
2. Do not broaden the edit solely because this skill was retrieved.
3. Do not treat weak validation as success; tie validation to the changed behavior.

## Support Summary

- Pattern key: `localize+edit`
- Common paths: modin, modin/core/dataframe/pandas/dataframe/dataframe.py, modin/core/storage_formats/pandas/query_compiler.py, dask/array/creation.py, dask/array/core.py, dask/dataframe/core.py, dask/dataframe/tests/test_dataframe.py, dask
- Common edited paths: dask/array/creation.py, dask/dataframe/core.py, moto/s3/models.py, modin/core/dataframe/pandas/dataframe/dataframe.py, modin/pandas/base.py, modin/core/dataframe/pandas/metadata/dtypes.py, modin/pandas/groupby.py, pandas/core/frame.py
- Common focused tests: none
- Associated failure signatures: none
