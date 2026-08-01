---
name: "pandas-dev-pandas-repo-localize-validate-recover"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 3
support_count: 4
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 146
repo: "pandas-dev__pandas"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/pandas-dev__pandas/candidate/pandas-dev-pandas-repo-localize-validate-recover/SKILL.md"
---

# pandas-dev-pandas-repo-localize-validate-recover

## Status

- Status: `candidate`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `4` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/pandas-dev__pandas/candidate/pandas-dev-pandas-repo-localize-validate-recover/SKILL.md`

## Trigger

current task is in repo pandas-dev__pandas; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest "pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_inplace_dict_update_view[val1]" pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_fillna_downcast "pandas/tests/frame/methods/test_fillna.py::TestFillNA::tes

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 4 verifier-positive events; paths=pandas/core/frame.py; tests=cd /testbed && python -m pytest "pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_inplace_dict_update_view[val1]" pandas/tests/frame/methods/test_fillna.py::TestFillNA::test_fillna_downcast "pandas/tests/frame/methods/test_fillna.py::TestFillNA::tes; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
