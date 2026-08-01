---
name: matplotlib-matplotlib-repo-localize-validate-recover
description: Test-time repo skill distilled from evaluator-approved benchmark evidence for matplotlib__matplotlib.
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

# matplotlib-matplotlib-repo-localize-validate-recover

## Status

- Run: `swebench_verified_tts_evo_from_direct_failures_20260704_gate003`
- Status: `test_time_promoted`
- Source: test-time benchmark trace evidence
- Promotion source: evaluator only; hidden verifier is not used for evolution.
- Evaluator decision: `accept`
- Proxy reward: `0.8`
- Reason: candidate passed deterministic evaluator gate
- Support: `5`
- Support repos: matplotlib__matplotlib

## Trigger

current task is in repo matplotlib__matplotlib; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Do Not

1. Do not copy source-task patch shape or hidden benchmark information.
2. Do not use this skill if current issue text, path, traceback, or validation signal does not match.
3. Do not treat evaluator acceptance as verifier success.

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 0 positive events; paths=lib/matplotlib, lib/matplotlib/pyplot.py; tests=cd . && python -m pytest lib/matplotlib/tests/test_pyplot.py -q 2>&1 | tail -15; failures=localization-drift
