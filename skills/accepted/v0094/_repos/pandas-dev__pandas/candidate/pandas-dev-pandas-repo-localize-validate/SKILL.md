---
name: pandas-dev-pandas-repo-localize-validate
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# pandas-dev-pandas-repo-localize-validate

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo pandas-dev__pandas; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

python -m pytest -q pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_array_long_double pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_0d_array 'pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_bool[False]' 'pandas/tests/io

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=pandas, pandas/core/internals/managers.py; tests=python -m pytest -q pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_array_long_double pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_0d_array 'pandas/tests/io/json/test_ujson.py::TestNumpyJSONTests::test_bool[False]' 'pandas/tests/io; failures=none
