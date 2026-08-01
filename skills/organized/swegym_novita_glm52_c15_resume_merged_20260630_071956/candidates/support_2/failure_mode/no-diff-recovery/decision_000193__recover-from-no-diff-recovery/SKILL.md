---
name: "recover-from-no-diff-recovery"
description: "candidate failure_mode skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "failure_mode"
decision: "stage"
candidate_decision: "memory_only"
support_bucket: 2
support_count: 2
support_unit: "repos"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 193
failure_signature: "no-diff-recovery"
confidence: 0.52
proxy_reward: 0.57
---

# recover-from-no-diff-recovery

## Status

- Status: `candidate`
- Decision: `stage`
- Candidate decision: `memory_only`
- Support: `2` `repos`; bucket `support_2`
- Reason: insufficient cross-repo support for failure-mode candidate
- Support repos: conan-io__conan, facebookresearch__hydra
- Event support count: `2`

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

2 repos; 2 events; repos=conan-io__conan, facebookresearch__hydra

## Evaluator Summary

insufficient cross-repo support for failure-mode candidate
