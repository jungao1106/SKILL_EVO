---
name: recover-from-localization-drift
description: Test-time failure-mode recovery skill distilled from evaluator-approved benchmark evidence for localization-drift.
active: true
quality_score: 1.00
quality_tier: failure_mode
risk_flags: []
use_policy: negative-evidence-gated
level: failure_mode
status: test_time_promoted
skill_type: "failure_mode"
support_count: 9
feedback_scope: test_time_evaluator_only
verifier_access: false
---

# recover-from-localization-drift

## Status

- Run: `swebench_verified_tts_evo_from_direct_failures_20260704_gate005`
- Status: `test_time_promoted`
- Source: test-time benchmark trace evidence
- Promotion source: evaluator only; hidden verifier is not used for evolution.
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Reason: candidate passed deterministic evaluator gate
- Support: `9`
- Support repos: astropy__astropy, django__django, matplotlib__matplotlib, pydata__xarray, pylint-dev__pylint, pytest-dev__pytest, scikit-learn__scikit-learn, sphinx-doc__sphinx, sympy__sympy

## Trigger

Use when the current public trace shows this failure signature; do not use solely because the skill exists.

## Negative Evidence Gate

Require fresh current-task evidence before applying this skill.

## Actions

1. Reconstruct the smallest current-task symptom before editing again.
2. Check whether the current diff still connects to the failing symbol, traceback, or focused test.
3. If localization drifted, discard unrelated paths and re-localize from public evidence.
4. If validation is weak or missing, derive the narrowest public check before broad testing.

## Do Not

1. Do not copy source-task patch shape or hidden benchmark information.
2. Do not use this skill if current issue text, path, traceback, or validation signal does not match.
3. Do not treat evaluator acceptance as verifier success.

## Stop Condition

Stop when the current trace no longer matches the failure signature or a narrower repo/current-task signal overrides it.

## Support Summary

9 repos; 49 events; repos=astropy__astropy, django__django, matplotlib__matplotlib, pydata__xarray, pylint-dev__pylint, pytest-dev__pytest, scikit-learn__scikit-learn, sphinx-doc__sphinx, sympy__sympy
