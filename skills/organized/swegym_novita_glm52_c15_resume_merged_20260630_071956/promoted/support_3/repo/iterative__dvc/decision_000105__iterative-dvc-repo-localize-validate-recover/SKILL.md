---
name: "iterative-dvc-repo-localize-validate-recover"
description: "promoted repo skill materialized from verifier-gated training evidence."
active: true
status: "promoted"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 3
support_count: 4
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 105
repo: "iterative__dvc"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/iterative__dvc/candidate/iterative-dvc-repo-localize-validate-recover/SKILL.md"
---

# iterative-dvc-repo-localize-validate-recover

## Status

- Status: `promoted`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/iterative__dvc/candidate/iterative-dvc-repo-localize-validate-recover/SKILL.md`

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

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
