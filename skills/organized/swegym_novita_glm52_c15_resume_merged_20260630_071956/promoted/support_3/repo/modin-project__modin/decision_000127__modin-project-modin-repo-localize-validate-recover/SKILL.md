---
name: "modin-project-modin-repo-localize-validate-recover"
description: "promoted repo skill materialized from verifier-gated training evidence."
active: true
status: "promoted"
level: "repo"
decision: "promote"
candidate_decision: "accept"
support_bucket: 3
support_count: 3
support_unit: "verifier_positive_task_events"
run: "swegym_novita_glm52_c15_resume_merged_20260630_071956"
source_decision_index: 127
repo: "modin-project__modin"
confidence: 0.95
proxy_reward: 1.0
official_skill_path: "/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/modin-project__modin/candidate/modin-project-modin-repo-localize-validate-recover/SKILL.md"
---

# modin-project-modin-repo-localize-validate-recover

## Status

- Status: `promoted`
- Decision: `promote`
- Candidate decision: `accept`
- Support: `3` `verifier_positive_task_events`; bucket `support_3`
- Reason: candidate passed deterministic verifier-proxy gate
- Official promoted path: `/vePFS-Mindverse/user/intern/jungao/SKILLS_EVO/skills/accepted/v0100/_repos/modin-project__modin/candidate/modin-project-modin-repo-localize-validate-recover/SKILL.md`

## Trigger

current task is in repo modin-project__modin; public trace matches repeated owner paths or adjacent modules; focused validation resembles repeated test commands; failure signature matches repeated repo failures

## Evidence Gate

Use only when current public evidence independently matches the repo cluster; do not copy a source-task patch.

## Actions

1. Start localization from the repeated owner paths only after current evidence matches them.
2. Prefer the repeated focused validation command when it matches the current issue.
3. If the repeated failure signature appears, recover before broadening the edit.

## Validation Hint

cd /testbed && python -m pytest -q 'modin/pandas/test/test_io.py::TestSql::test_to_sql[sqlalchemy+connect-True]' 'modin/pandas/test/test_io.py::TestSql::test_to_sql[sqlalchemy-True]' 'modin/pandas/test/test_io.py::TestSql::test_to_sql[sqlalchemy+connect-False]

## Stop Condition

Stop using this repo candidate if the current traceback, symbol, path, or focused test does not match the cluster evidence.

## Support Summary

5 task events; 3 verifier-positive events; paths=modin/core/storage_formats/pandas/parsers.py, modin/pandas/base.py; tests=cd /testbed && python -m pytest -q 'modin/pandas/test/test_io.py::TestSql::test_to_sql[sqlalchemy+connect-True]' 'modin/pandas/test/test_io.py::TestSql::test_to_sql[sqlalchemy-True]' 'modin/pandas/test/test_io.py::TestSql::test_to_sql[sqlalchemy+connect-False]; failures=localization-drift

## Evaluator Summary

candidate passed deterministic verifier-proxy gate
