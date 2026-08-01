---
name: "pydantic-pydantic-repo-localize"
description: "promoted repo skill materialized from verifier-gated training evidence."
active: true
status: "promoted"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 3
support_count: 5
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 188
repo: "pydantic__pydantic"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/pydantic__pydantic/candidate/pydantic-pydantic-repo-localize/SKILL.md"
---

# pydantic-pydantic-repo-localize

## Status

- Status: `promoted`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `5` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/pydantic__pydantic/candidate/pydantic-pydantic-repo-localize/SKILL.md`

## Trigger

current task is in repo pydantic__pydantic; public trace matches repeated owner paths or adjacent modules

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.

## Validation Hint

derive the narrowest public check from the current issue

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 verifier-positive events; paths=pydantic/_internal/_generate_schema.py, pydantic, tests; tests=none; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
