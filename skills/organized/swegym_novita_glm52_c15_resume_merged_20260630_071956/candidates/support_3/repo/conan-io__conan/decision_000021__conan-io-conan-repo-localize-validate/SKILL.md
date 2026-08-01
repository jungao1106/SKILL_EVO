---
name: "conan-io-conan-repo-localize-validate"
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
source_decision_index: 21
repo: "conan-io__conan"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/conan-io__conan/candidate/conan-io-conan-repo-localize-validate/SKILL.md"
---

# conan-io-conan-repo-localize-validate

## Status

- Status: `candidate`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `5` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/conan-io__conan/candidate/conan-io-conan-repo-localize-validate/SKILL.md`

## Trigger

current task is in repo conan-io__conan; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.

## Validation Hint

cd /testbed && python -m pytest -q conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore_with_package_file conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore conans/test/integration/command_v

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 5 verifier-positive events; paths=/testbed; tests=cd /testbed && python -m pytest -q conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore_with_package_file conans/test/integration/command_v2/test_cache_save_restore.py::test_cache_save_restore conans/test/integration/command_v; failures=none

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
