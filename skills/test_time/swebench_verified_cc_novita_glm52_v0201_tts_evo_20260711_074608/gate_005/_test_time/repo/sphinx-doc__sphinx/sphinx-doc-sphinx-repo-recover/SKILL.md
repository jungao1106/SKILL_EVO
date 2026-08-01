---
name: sphinx-doc-sphinx-repo-recover
description: Test-time repo skill distilled from evaluator-approved benchmark evidence for sphinx-doc__sphinx.
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

# sphinx-doc-sphinx-repo-recover

## Status

- Run: `swebench_verified_cc_novita_glm52_v0201_tts_evo_20260711_074608_gate005`
- Status: `test_time_promoted`
- Source: test-time benchmark trace evidence
- Promotion source: evaluator only; hidden verifier is not used for evolution.
- Evaluator decision: `accept`
- Proxy reward: `0.8`
- Reason: candidate passed deterministic evaluator gate
- Support: `5`
- Support repos: sphinx-doc__sphinx

## Trigger

current task is in repo sphinx-doc__sphinx; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. If the repeated failure signature appears, recover before broadening the edit.

## Do Not

1. Do not copy source-task patch shape or hidden benchmark information.
2. Do not use this skill if current issue text, path, traceback, or validation signal does not match.
3. Do not treat evaluator acceptance as verifier success.

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 0 positive events; paths=none; tests=none; failures=localization-drift, no-diff-recovery
