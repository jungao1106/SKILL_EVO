---
name: "pandas-dev-pandas-repo-localize-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "refresh"
candidate_decision: "accept"
support_bucket: 3
support_count: 4
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 153
repo: "pandas-dev__pandas"
confidence: 0.95
proxy_reward: 1.0
---

# pandas-dev-pandas-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `refresh`
- Candidate decision: `accept`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: accepted duplicate repo candidate already promoted in this version

## Trigger

current task is in repo pandas-dev__pandas; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest -q 'pandas/tests/io/test_parquet.py::TestBasic::test_use_nullable_dtypes[pyarrow]' 'pandas/tests/io/test_parquet.py::TestBasic::test_read_empty_array[Float64]' pandas/tests/io/test_parquet.py::TestParquetPyArrow::test_categorica; cd /testbed && python -m pytest -q 'pandas/tests/frame/indexing/test_indexing.py::TestDataFrameIndexing::test_setting_mismatched_na_into_nullable_fails[Int16-null2]' pandas/tests/frame/indexing/test_indexing.py::TestDataFrameIndexing::test_setitem_mixed_dateti

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=doc/source/whatsnew/v1.5.0.rst, pandas/core, pandas/core/internals/blocks.py; tests=cd /testbed && python -m pytest -q 'pandas/tests/io/test_parquet.py::TestBasic::test_use_nullable_dtypes[pyarrow]' 'pandas/tests/io/test_parquet.py::TestBasic::test_read_empty_array[Float64]' pandas/tests/io/test_parquet.py::TestParquetPyArrow::test_categorica, cd /testbed && python -m pytest -q 'pandas/tests/frame/indexing/test_indexing.py::TestDataFrameIndexing::test_setting_mismatched_na_into_nullable_fails[Int16-null2]' pandas/tests/frame/indexing/test_indexing.py::TestDataFrameIndexing::test_setitem_mixed_dateti; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
