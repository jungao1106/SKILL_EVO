---
name: success-localize-validate
description: Training-distilled success pattern for localization, and focused validation.
active: true
quality_score: 0.76
quality_tier: success_pattern
risk_flags: []
use_policy: evidence-gated
level: success_pattern
status: frozen
skill_type: "success_pattern"
support_count: 4
event_support_count: 6
positive_support_count: 28
support_bucket: "support_3"
pattern_key: "localize+validate"
trigger_index: 96
---

# success-localize-validate

## Status

- Run: `swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608`
- Status: `frozen`
- Source: training-only repo-level success evidence
- Support: `4` repos; `6` repo events; `28` verifier-positive task events
- Support repos: conan-io__conan, iterative__dvc, modin-project__modin, pandas-dev__pandas

## Applicability

Use this skill when current task evidence calls for localization, and focused validation. It is a procedural prior, not a repo-specific patch recipe.

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

- Pattern key: `localize+validate`
- Common paths: dvc/config.py, /testbed, conans/test/integration/toolchains/cmake/test_cmaketoolchain.py, conan/tools/cmake/toolchain/blocks.py, dvc, dvc/repo/__init__.py, modin/pandas, pandas
- Common edited paths: none
- Common focused tests: cd /testbed && python -m pytest -q conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore_with_package_file conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore conans/test/integration/command_v | cd /testbed && python -m pytest -q conans/test/integration/toolchains/cmake/test_cmaketoolchain.py::test_cross_build conans/test/integration/toolchains/cmake/test_cmaketoolchain.py::test_cross_build_conf conans/test/integration/toolchains/cmake/test_cmaketoolc | cd /testbed && python -m pytest -q tests/unit/test_info.py::test_fs_info_outside_of_repo "tests/unit/test_info.py::test_info_in_repo[True]" tests/unit/test_info.py::test_info_outside_of_repo tests/unit/test_info.py::test_fs_info_in_repo "tests/unit/test_info.p | cd /testbed && python -m pytest -q tests/unit/command/test_experiments.py::test_experiments_push tests/unit/command/test_experiments.py::test_experiments_pull tests/func/test_gc.py::test_gc_without_workspace tests/func/test_gc.py::test_gc_cloud_without_any_spe
- Associated failure signatures: none
