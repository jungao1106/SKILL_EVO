---
name: sympy-sympy-repo-validate-recover
description: Test-time repo skill distilled from evaluator-approved benchmark evidence for sympy__sympy.
active: true
quality_score: 0.80
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
status: test_time_promoted
skill_type: "repo"
support_count: 5
feedback_scope: test_time_evaluator_only
verifier_access: false
---

# sympy-sympy-repo-validate-recover

## Status

- Run: `swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608_gate005`
- Status: `test_time_promoted`
- Source: test-time benchmark trace evidence
- Promotion source: evaluator only; hidden verifier is not used for evolution.
- Evaluator decision: `accept`
- Proxy reward: `0.8`
- Reason: candidate passed deterministic evaluator gate
- Support: `5`
- Support repos: sympy__sympy

## Trigger

current task is in repo sympy__sympy; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Prefer the repeated focused validation command when it matches the current issue.
2. If the repeated failure signature appears, recover before broadening the edit.

## Do Not

1. Do not copy source-task patch shape or hidden benchmark information.
2. Do not use this skill if current issue text, path, traceback, or validation signal does not match.
3. Do not treat evaluator acceptance as verifier success.

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 0 positive events; paths=none; tests=python -m pytest sympy/printing/tests/test_latex.py -q 2>&1 | tail -20; failures=no-diff-recovery
