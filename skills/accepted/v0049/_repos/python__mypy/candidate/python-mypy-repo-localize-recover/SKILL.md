---
name: python-mypy-repo-localize-recover
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 0.96
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# python-mypy-repo-localize-recover

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `0.96`
- Confidence: `0.91`

## Trigger

current task is in repo python__mypy; public trace matches repeated owner paths or adjacent modules; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 2 verifier-positive events; paths=mypy; tests=none; failures=localization-drift
