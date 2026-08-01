---
name: pydantic-pydantic-repo-localize-validate-recover
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# pydantic-pydantic-repo-localize-validate-recover

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo pydantic__pydantic; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -c " from pydantic import BaseModel, ImportString import math, os, sys import tests.test_types as t import pytest class M(BaseModel): thing: ImportString cases = [ ('math:cos', 'math.cos', 'json'), ('math:cos', math.cos, 'python'), ('os.p

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=pydantic/_internal/_generate_schema.py, pydantic/_internal/_core_utils.py; tests=cd /testbed && python -c " from pydantic import BaseModel, ImportString import math, os, sys import tests.test_types as t import pytest class M(BaseModel): thing: ImportString cases = [ ('math:cos', 'math.cos', 'json'), ('math:cos', math.cos, 'python'), ('os.p; failures=localization-drift
