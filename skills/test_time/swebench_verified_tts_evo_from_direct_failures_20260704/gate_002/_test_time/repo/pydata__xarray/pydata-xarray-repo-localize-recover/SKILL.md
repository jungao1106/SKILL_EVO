---
name: pydata-xarray-repo-localize-recover
description: Test-time repo skill distilled from evaluator-approved benchmark evidence for pydata__xarray.
active: true
quality_score: 0.77
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
status: test_time_promoted
skill_type: "repo"
support_count: 4
feedback_scope: test_time_evaluator_only
verifier_access: false
---

# pydata-xarray-repo-localize-recover

## Status

- Run: `swebench_verified_tts_evo_from_direct_failures_20260704`
- Status: `test_time_promoted`
- Source: test-time benchmark trace evidence
- Promotion source: evaluator only; hidden verifier is not used for evolution.
- Evaluator decision: `accept`
- Proxy reward: `0.77`
- Reason: candidate passed deterministic evaluator gate
- Support: `4`
- Support repos: pydata__xarray

## Trigger

current task is in repo pydata__xarray; public trace matches repeated owner paths or adjacent modules; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. If the repeated failure signature appears, recover before broadening the edit.

## Do Not

1. Do not copy source-task patch shape or hidden benchmark information.
2. Do not use this skill if current issue text, path, traceback, or validation signal does not match.
3. Do not treat evaluator acceptance as verifier success.

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

4 task events; 0 positive events; paths=xarray/core/dataset.py, xarray/core/dataarray.py, xarray, xarray/tests/test_dataset.py; tests=none; failures=localization-drift
