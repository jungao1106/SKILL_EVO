---
name: recover-from-localization-drift
description: Frozen training-distilled failure-mode recovery skill for localization-drift.
active: true
quality_score: 0.91
quality_tier: failure_mode
risk_flags: []
use_policy: negative-evidence-gated
level: failure_mode
status: promoted
skill_type: "failure_mode"
failure_signature: "localization-drift"
support_count: 10
event_support_count: 207
support_bucket: "support_3"
trigger_index: 32
---

# recover-from-localization-drift

## Status

- Run: `swebench_verified_cc_novita_glm52_v0201_frozen_downstream_20260711_074608`
- Status: `promoted`
- Source: training-only failure-mode evidence
- Evaluator decision: `accept`
- Promotion decision: `stage`
- Reason: candidate passed deterministic verifier-proxy gate
- Support: `10` repos; `207` events
- Support repos: Project-MONAI__MONAI, conan-io__conan, dask__dask, facebookresearch__hydra, getmoto__moto, iterative__dvc, modin-project__modin, pandas-dev__pandas, pydantic__pydantic, python__mypy

## Trigger

Use when the current public trace shows this failure signature; do not use solely because the skill exists.

## Negative Evidence Gate

Use this skill to avoid or recover from a historically recurring failure mode. Do not copy source-task edits; require fresh current-task evidence.

## Recovery Actions

1. Reconstruct the smallest current-task symptom before editing again.
2. Check whether the current diff still connects to the failing symbol, traceback, or focused test.
3. If localization drifted, discard unrelated paths and re-localize from public evidence.
4. If validation is weak or missing, derive the narrowest public check before broad testing.

## Do Not

1. Do not continue a diff that no longer connects to the failing symbol, traceback, issue text, or focused test.
2. Do not accept a broad edit when validation is weak, missing, or unrelated to the current symptom.
3. Do not treat this as proof that the historical patch shape transfers.

## Stop Condition

Stop when the current trace no longer matches the failure signature or a narrower repo/current-task signal overrides it.

## Support Summary

10 repos; 207 events; repos=Project-MONAI__MONAI, conan-io__conan, dask__dask, facebookresearch__hydra, getmoto__moto, iterative__dvc, modin-project__modin, pandas-dev__pandas, pydantic__pydantic, python__mypy
