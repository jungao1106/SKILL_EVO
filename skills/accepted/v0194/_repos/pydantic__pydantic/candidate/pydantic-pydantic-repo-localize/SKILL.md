---
name: pydantic-pydantic-repo-localize
description: repo skill accepted by the evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# pydantic-pydantic-repo-localize

- Run: `swegym_pi_novita_glm52_c15_resume_merged_20260709_101635`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo pydantic__pydantic; public trace matches repeated owner paths or adjacent modules

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 positive events; paths=pydantic, pydantic/_internal/_generate_schema.py, pydantic/_internal, pydantic/_internal/_fields.py, /testbed, tests; tests=none; failures=none
