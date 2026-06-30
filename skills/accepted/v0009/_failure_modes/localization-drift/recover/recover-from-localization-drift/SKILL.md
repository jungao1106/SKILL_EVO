---
name: recover-from-localization-drift
description: failure_mode skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 0.91
quality_tier: failure_mode
risk_flags: []
use_policy: evidence-gated
level: failure_mode
---

# recover-from-localization-drift

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `0.91`
- Confidence: `0.86`

## Trigger

Use when the current public trace shows this failure signature; do not use solely because the skill exists.

## Evidence Gate

Use only when current public evidence matches the support summary.

## Actions

1. Reconstruct the smallest current-task symptom before editing again.
2. Check whether the current diff still connects to the failing symbol, traceback, or focused test.
3. If localization drifted, discard unrelated paths and re-localize from public evidence.
4. If validation is weak or missing, derive the narrowest public check before broad testing.

## Validation Hint



## Stop Condition

Stop when the current trace no longer matches the failure signature or a narrower repo/current-task signal overrides it.

## Support Summary

3 repos; 25 events; repos=Project-MONAI__MONAI, conan-io__conan, dask__dask
