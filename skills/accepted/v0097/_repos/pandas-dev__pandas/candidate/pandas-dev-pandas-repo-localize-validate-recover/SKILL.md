---
name: pandas-dev-pandas-repo-localize-validate-recover
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# pandas-dev-pandas-repo-localize-validate-recover

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo pandas-dev__pandas; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest "pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_inplace_dict_update_view[val1]" pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_fillna_downcast "pandas/tests/frame/methods/test_fillna.py::TestFillNA::tes

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=pandas/core/frame.py; tests=cd /testbed && python -m pytest "pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_inplace_dict_update_view[val1]" pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_fillna_downcast "pandas/tests/frame/methods/test_fillna.py::TestFillNA::tes; failures=localization-drift
