---
name: success-localize-edit-validate-recover
description: Training-distilled success pattern for localization, narrow edit, focused validation, and recovery.
active: true
quality_score: 0.77
quality_tier: success_pattern
risk_flags: []
use_policy: evidence-gated
level: success_pattern
status: frozen
skill_type: "success_pattern"
support_count: 4
event_support_count: 7
positive_support_count: 26
support_bucket: "support_3"
pattern_key: "localize+edit+validate+recover"
trigger_index: 112
---

# success-localize-edit-validate-recover

## Status

- Run: `swebench_verified_glm52_novita_v0100_frozen_direct_20260703_182027`
- Status: `frozen`
- Source: training-only repo-level success evidence
- Support: `4` repos; `7` repo events; `26` verifier-positive task events
- Support repos: dask__dask, iterative__dvc, pandas-dev__pandas, pydantic__pydantic

## Applicability

Use this skill when current task evidence calls for localization, narrow edit, focused validation, and recovery. It is a procedural prior, not a repo-specific patch recipe.

## Evidence Gate

Use only after inspecting the current repository evidence. Ignore it if the issue, traceback, owner path, or test signal does not match the current task.

## Actions

1. Start from the smallest public symptom: issue text, traceback, failing assertion, or focused test.
2. Use repeated owner paths only as localization priors; confirm with current repository evidence.
3. Choose the narrowest owner path that is supported by current evidence before editing.
4. Keep edits minimal and re-check the focused behavior before broad validation.
5. Prefer a focused validation command that directly exercises the changed behavior.
6. If the current diff drifts away from the symptom, stop and re-localize before expanding scope.

## Do Not

1. Do not copy historical paths, tests, or patch shape without fresh current-task evidence.
2. Do not broaden the edit solely because this skill was retrieved.
3. Do not treat weak validation as success; tie validation to the changed behavior.

## Support Summary

- Pattern key: `localize+edit+validate+recover`
- Common paths: pydantic/_internal/_generate_schema.py, dask/array, dask/array/__init__.py, dvc/state.py, dvc/remote/local.py, dvc/utils/fs.py, dvc/output/base.py, tests
- Common edited paths: pydantic/_internal/_generate_schema.py, dask/array/__init__.py, dvc/remote/local.py, pandas/core/frame.py, doc/source/whatsnew/v1.5.0.rst, doc/source/whatsnew/v2.0.0.rst, pydantic/types.py
- Common focused tests: cd /testbed && python -m pytest -q dask/array/tests/test_masked.py::test_count 'dask/array/tests/test_masked.py::test_mixed_concatenate[<lambda>4]' 'dask/array/tests/test_masked.py::test_basic[<lambda>1]' 'dask/array/tests/test_masked.py::test_mixed_random[<la | cd /tmp && rm -rf myrepo && mkdir myrepo && cd myrepo && git init -q && python -m dvc init -q && git commit -qm init && mkdir dir && for i in $(seq 1 1000); do echo $i > dir/$i; done && python -c " import logging; logging.disable(logging.CRITICAL) from dvc.mai | cd /testbed && python -m pytest "pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_inplace_dict_update_view[val1]" pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_fillna_downcast "pandas/tests/frame/methods/test_fillna.py::TestFillNA::tes | cd /testbed && python -m pytest -q 'pandas/tests/io/test_parquet.py::TestBasic::test_use_nullable_dtypes[pyarrow]' 'pandas/tests/io/test_parquet.py::TestBasic::test_read_empty_array[Float64]' pandas/tests/io/test_parquet.py::TestParquetPyArrow::test_categorica
- Associated failure signatures: localization-drift
