---
name: "python-mypy-repo-localize-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 2
support_count: 2
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 190
repo: "python__mypy"
confidence: 0.91
proxy_reward: 0.96
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/python__mypy/candidate/python-mypy-repo-localize-recover/SKILL.md"
---

# python-mypy-repo-localize-recover

## Status

- Status: `candidate`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `2` `verifier_positive_task_events`; bucket `support_2`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/python__mypy/candidate/python-mypy-repo-localize-recover/SKILL.md`

## Trigger

current task is in repo python__mypy; public trace matches repeated owner paths or adjacent modules; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 2 verifier-positive events; paths=mypy; tests=none; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
