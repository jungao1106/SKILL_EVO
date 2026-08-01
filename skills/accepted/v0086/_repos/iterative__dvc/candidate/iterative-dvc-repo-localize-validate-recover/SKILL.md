---
name: iterative-dvc-repo-localize-validate-recover
description: repo skill accepted from verifier-calibrated evaluator.
active: true
quality_score: 1.00
quality_tier: repo
risk_flags: []
use_policy: evidence-gated
level: repo
---

# iterative-dvc-repo-localize-validate-recover

- Run: `swegym_novita_glm52_c15_resume_merged_20260630_071956`
- Evaluator decision: `accept`
- Proxy reward: `1.0`
- Confidence: `0.95`

## Trigger

current task is in repo iterative__dvc; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /tmp && rm -rf myrepo && mkdir myrepo && cd myrepo && git init -q && python -m dvc init -q && git commit -qm init && mkdir dir && for i in $(seq 1 1000); do echo $i > dir/$i; done && python -c " import logging; logging.disable(logging.CRITICAL) from dvc.mai

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=dvc/state.py, dvc/remote/local.py, dvc/utils/fs.py, dvc/output/base.py, tests; tests=cd /tmp && rm -rf myrepo && mkdir myrepo && cd myrepo && git init -q && python -m dvc init -q && git commit -qm init && mkdir dir && for i in $(seq 1 1000); do echo $i > dir/$i; done && python -c " import logging; logging.disable(logging.CRITICAL) from dvc.mai; failures=localization-drift
