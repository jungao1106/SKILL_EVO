---
name: "pydantic-pydantic-repo-localize-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 3
support_count: 3
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 168
repo: "pydantic__pydantic"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/pydantic__pydantic/candidate/pydantic-pydantic-repo-localize-validate-recover/SKILL.md"
---

# pydantic-pydantic-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `3` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/pydantic__pydantic/candidate/pydantic-pydantic-repo-localize-validate-recover/SKILL.md`

## Trigger

current task is in repo pydantic__pydantic; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -c " from pydantic import BaseModel, ImportString import math, os, sys import tests.test_types as t import pytest class M(BaseModel): thing: ImportString cases = [ ('math:cos', 'math.cos', 'json'), ('math:cos', math.cos, 'python'), ('os.p

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=pydantic/_internal/_generate_schema.py, pydantic/_internal/_core_utils.py; tests=cd /testbed && python -c " from pydantic import BaseModel, ImportString import math, os, sys import tests.test_types as t import pytest class M(BaseModel): thing: ImportString cases = [ ('math:cos', 'math.cos', 'json'), ('math:cos', math.cos, 'python'), ('os.p; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
