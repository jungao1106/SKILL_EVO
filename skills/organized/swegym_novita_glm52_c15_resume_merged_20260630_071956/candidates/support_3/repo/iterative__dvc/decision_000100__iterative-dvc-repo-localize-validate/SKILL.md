---
name: "iterative-dvc-repo-localize-validate"
description: "candidate repo skill materialized from training candidate evidence."
active: false
status: "candidate"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 3
support_count: 5
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 100
repo: "iterative__dvc"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/iterative__dvc/candidate/iterative-dvc-repo-localize-validate/SKILL.md"
---

# iterative-dvc-repo-localize-validate

## Status

- Status: `candidate`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `5` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/iterative__dvc/candidate/iterative-dvc-repo-localize-validate/SKILL.md`

## Trigger

current task is in repo iterative__dvc; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q tests/unit/test_info.py::test_fs_info_outside_of_repo "tests/unit/test_info.py::test_info_in_repo[True]" tests/unit/test_info.py::test_info_outside_of_repo tests/unit/test_info.py::test_fs_info_in_repo "tests/unit/test_info.p

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 verifier-positive events; paths=dvc, dvc/config.py, dvc/repo/__init__.py; tests=cd /testbed && python -m pytest -q tests/unit/test_info.py::test_fs_info_outside_of_repo "tests/unit/test_info.py::test_info_in_repo[True]" tests/unit/test_info.py::test_info_outside_of_repo tests/unit/test_info.py::test_fs_info_in_repo "tests/unit/test_info.p; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
