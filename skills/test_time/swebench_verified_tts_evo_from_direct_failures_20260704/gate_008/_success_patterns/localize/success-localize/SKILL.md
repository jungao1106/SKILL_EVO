---
name: success-localize
description: Training-distilled success pattern for localization.
active: true
quality_score: 0.70
quality_tier: success_pattern
risk_flags: []
use_policy: evidence-gated
level: success_pattern
status: frozen
skill_type: "success_pattern"
support_count: 3
event_support_count: 3
positive_support_count: 14
support_bucket: "support_3"
pattern_key: "localize"
trigger_index: 92
---

# success-localize

## Status

- Run: `swebench_verified_glm52_novita_v0100_frozen_direct_20260703_182027`
- Status: `frozen`
- Source: training-only repo-level success evidence
- Support: `3` repos; `3` repo events; `14` verifier-positive task events
- Support repos: Project-MONAI__MONAI, dask__dask, modin-project__modin

## Applicability

Use this skill when current task evidence calls for localization. It is a procedural prior, not a repo-specific patch recipe.

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

- Pattern key: `localize`
- Common paths: /testbed, dask, dask/utils.py, modin, modin/core/storage_formats/base/query_compiler.py, modin/core/storage_formats/pandas/query_compiler.py, modin/pandas/base.py
- Common edited paths: none
- Common focused tests: none
- Associated failure signatures: none
