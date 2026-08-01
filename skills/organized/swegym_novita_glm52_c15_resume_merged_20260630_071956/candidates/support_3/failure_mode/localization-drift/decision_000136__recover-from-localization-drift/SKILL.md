---
name: "recover-from-localization-drift"
description: "candidate failure_mode skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "failure_mode"
decision: "stage"
candidate_decision: "accept"
support_bucket: 3
support_count: 7
support_unit: "repos"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 136
failure_signature: "localization-drift"
confidence: 0.95
proxy_reward: 1.0
---

# recover-from-localization-drift

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `accept`
- Support: `7` `repos`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Support repos: Project-MONAI__MONAI, conan-io__conan, dask__dask, facebookresearch__hydra, getmoto__moto, iterative__dvc, modin-project__modin
- Event support count: `151`

## Trigger

Use when the current public trace shows this failure signature; do not use solely because the skill exists.

## Evidence Gate

Use only when current public evidence matches the support summary.

## Actions

1. Reconstruct the smallest current-task symptom before editing again.
2. Check whether the current diff still connects to the failing symbol, traceback, or focused test.
3. If localization drifted, discard unrelated paths and re-localize from public evidence.
4. If validation is weak or missing, derive the narrowest public check before broad testing.

## Stop Condition

Stop when the current trace no longer matches the failure signature or a narrower repo/current-task signal overrides it.

## Support Summary

7 repos; 151 events; repos=Project-MONAI__MONAI, conan-io__conan, dask__dask, facebookresearch__hydra, getmoto__moto, iterative__dvc, modin-project__modin

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
