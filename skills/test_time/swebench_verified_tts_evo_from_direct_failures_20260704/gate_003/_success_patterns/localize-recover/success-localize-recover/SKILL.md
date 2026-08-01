---
name: success-localize-recover
description: Training-distilled success pattern for localization, and recovery.
active: true
quality_score: 0.87
quality_tier: success_pattern
risk_flags: []
use_policy: evidence-gated
level: success_pattern
status: frozen
skill_type: "success_pattern"
support_count: 7
event_support_count: 13
positive_support_count: 42
support_bucket: "support_3"
pattern_key: "localize+recover"
trigger_index: 131
---

# success-localize-recover

## Status

- Run: `swebench_verified_glm52_novita_v0100_frozen_direct_20260703_182027`
- Status: `frozen`
- Source: training-only repo-level success evidence
- Support: `7` repos; `13` repo events; `42` verifier-positive task events
- Support repos: Project-MONAI__MONAI, conan-io__conan, dask__dask, getmoto__moto, iterative__dvc, pydantic__pydantic, python__mypy

## Applicability

Use this skill when current task evidence calls for localization, and recovery. It is a procedural prior, not a repo-specific patch recipe.

## Evidence Gate

Use only after inspecting the current repository evidence. Ignore it if the issue, traceback, owner path, or test signal does not match the current task.

## Actions

1. Start from the smallest public symptom: issue text, traceback, failing assertion, or focused test.
2. Use repeated owner paths only as localization priors; confirm with current repository evidence.
3. Choose the narrowest owner path that is supported by current evidence before editing.
4. Keep edits minimal and re-check the focused behavior before broad validation.
5. If the current diff drifts away from the symptom, stop and re-localize before expanding scope.

## Do Not

1. Do not copy historical paths, tests, or patch shape without fresh current-task evidence.
2. Do not broaden the edit solely because this skill was retrieved.
3. Do not treat weak validation as success; tie validation to the changed behavior.

## Support Summary

- Pattern key: `localize+recover`
- Common paths: mypy, mypy/checker.py, mypy/nodes.py, tests/test_ahnet.py, conans/util/runners.py, dask/array/core.py, dask/array, dask/array/utils.py
- Common edited paths: none
- Common focused tests: none
- Associated failure signatures: localization-drift
